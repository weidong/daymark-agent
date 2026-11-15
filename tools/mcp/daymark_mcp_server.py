#!/usr/bin/env python3
"""Daymark MCP server: 把飞书工作记录能力暴露成 MCP 工具（Streamable HTTP）。"""
import contextlib
import datetime as dt
import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "feishu"))
import feishu_client as fc  # noqa: E402

PROTOCOL = "2025-06-18"
SERVER_INFO = {"name": "daymark-feishu", "version": "0.1.0"}
DEFAULT_PORT = 8765
_lock = threading.Lock()


TOOLS = [
    {
        "name": "daymark_today",
        "description": "在飞书年度记录中写入当天（或指定日期）的“开工”；已开工则跳过。",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "YYYY-MM-DD，缺省为今天"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "daymark_append",
        "description": "把一条工作记录追加到当天（或指定日期）下；重复内容会自动跳过。",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要记录的原话，不加工"},
                "date": {"type": "string", "description": "YYYY-MM-DD，缺省为今天"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "daymark_show",
        "description": "查看某一天已记录的内容。",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "YYYY-MM-DD，缺省为今天"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "daymark_month",
        "description": "汇总某个月所有日期与条目，用于生成月度复盘。",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {"month": {"type": "string", "description": "YYYY-MM，缺省为当月"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "daymark_update",
        "description": "按原文精确匹配，修改某天的一条记录。",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "old": {"type": "string", "description": "原文"},
                "new": {"type": "string", "description": "新内容"},
                "date": {"type": "string", "description": "YYYY-MM-DD，缺省为今天"},
            },
            "required": ["old", "new"],
            "additionalProperties": False,
        },
    },
    {
        "name": "daymark_delete",
        "description": "删除某天的一条记录。必须由用户明确确认后，confirm 才能传 true。",
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要删除的原文"},
                "date": {"type": "string", "description": "YYYY-MM-DD，缺省为今天"},
                "confirm": {"type": "boolean", "description": "用户已明确同意删除时为 true"},
            },
            "required": ["text", "confirm"],
            "additionalProperties": False,
        },
    },
]


def parse_day(value):
    return dt.date.fromisoformat(value) if value else dt.date.today()


def parse_month(value):
    if value:
        year, month = value.split("-")[:2]
        return int(year), int(month)
    today = dt.date.today()
    return today.year, today.month


def open_doc():
    cfg = fc.load_env()
    tokens = fc.Tokens()
    tokens.refresh_if_needed()
    return fc.Doc(tokens, cfg["FEISHU_DOC_TOKEN"])


def call_tool(name, args):
    with _lock:
        doc = open_doc()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            if name == "daymark_today":
                fc.cmd_today(doc, parse_day(args.get("date")))
            elif name == "daymark_append":
                if not (args.get("text") or "").strip():
                    raise ValueError("text 不能为空")
                fc.cmd_append(doc, parse_day(args.get("date")), args["text"].strip())
            elif name == "daymark_show":
                fc.cmd_show(doc, parse_day(args.get("date")))
            elif name == "daymark_month":
                year, month = parse_month(args.get("month"))
                fc.cmd_month(doc, year, month, False)
            elif name == "daymark_update":
                fc.cmd_update(doc, parse_day(args.get("date")), args["old"], args["new"])
            elif name == "daymark_delete":
                fc.cmd_delete(doc, parse_day(args.get("date")), args["text"], bool(args.get("confirm")))
            else:
                raise ValueError(f"未知工具: {name}")
    return out.getvalue().strip() or "完成"


def handle(message):
    method = message.get("method")
    mid = message.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            text = call_tool(params.get("name"), params.get("arguments") or {})
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"content": [{"type": "text", "text": text}], "isError": False},
            }
        except Exception as exc:  # 把失败如实返回给模型
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"content": [{"type": "text", "text": f"执行失败：{exc}"}], "isError": True},
            }
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"未知方法: {method}"}}


class Handler(BaseHTTPRequestHandler):
    server_version = "DaymarkMcp/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, payload=None):
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        if body:
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send(200, {"status": "ok", "server": SERVER_INFO["name"]})
        else:
            self._send(405, {"error": "只支持 POST"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON 解析失败"}})
            return
        if isinstance(message, list):
            results = [handle(m) for m in message]
            self._send(200, [r for r in results if r is not None])
            return
        result = handle(message)
        if result is None:
            self._send(202)
        else:
            self._send(200, result)


def main():
    port = int(os.environ.get("DAYMARK_MCP_PORT", DEFAULT_PORT))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Daymark MCP server 已启动: http://0.0.0.0:{port}/mcp")
    print(f"模拟器访问地址示例: http://10.0.2.2:{port}/mcp")
    server.serve_forever()


if __name__ == "__main__":
    main()
