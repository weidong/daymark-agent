"""Daymark 飞书客户端：定位日志结构、写入记录、读写校验。

用法：
    python tools/feishu/feishu_client.py today
    python tools/feishu/feishu_client.py append "完成结营作业"
    python tools/feishu/feishu_client.py show

依赖：项目根目录 .env 中的 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_DOC_TOKEN，
以及 tools/feishu/login.py 授权后生成的 .secrets/tokens.json。
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://open.feishu.cn/open-apis"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENV_FILE = os.path.join(ROOT, ".env")
TOKEN_FILE = os.path.join(ROOT, ".secrets", "tokens.json")

WEEKDAYS = "一二三四五六日"
# BlockType：1 页面、3-11 标题 1-9、12 无序列表
HEADING_START = 3
HEADING_END = 11
BULLET = 12
PAGE = 1


def load_env():
    cfg = {}
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def api(method, path, token=None, payload=None, retries=3):
    url = path if path.startswith("http") else BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data:
        headers["Content-Type"] = "application/json; charset=utf-8"
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            try:
                return json.loads(body)
            except Exception:
                return {"code": e.code, "msg": body}
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError(f"{method} {url} failed: {last}")


class Tokens:
    def __init__(self):
        self.data = json.load(open(TOKEN_FILE, encoding="utf-8"))

    @property
    def access(self):
        return self.data["access_token"]

    def save(self):
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def refresh_if_needed(self):
        saved_at = self.data.get("saved_at", 0)
        expires_in = self.data.get("expires_in", 0)
        if time.time() < saved_at + expires_in - 300:
            return
        cfg = load_env()
        tr = api("POST", "/auth/v3/tenant_access_token/internal", payload={
            "app_id": cfg["FEISHU_APP_ID"],
            "app_secret": cfg["FEISHU_APP_SECRET"],
        })
        if tr.get("code") != 0:
            raise RuntimeError(f"tenant token 失败: {tr}")
        rr = api("POST", "/authen/v1/refresh_access_token", token=tr["tenant_access_token"], payload={
            "grant_type": "refresh_token",
            "refresh_token": self.data["refresh_token"],
        })
        if rr.get("code") != 0:
            raise RuntimeError(f"刷新用户令牌失败: {rr}")
        d = rr["data"]
        self.data.update({
            "access_token": d["access_token"],
            "refresh_token": d.get("refresh_token", self.data["refresh_token"]),
            "expires_in": d.get("expires_in", 0),
            "saved_at": int(time.time()),
        })
        self.save()


class Doc:
    def __init__(self, tokens, doc_id):
        self.tokens = tokens
        self.doc_id = doc_id

    def blocks(self):
        items, page_token = [], None
        while True:
            path = f"/docx/v1/documents/{self.doc_id}/blocks?page_size=500&document_revision_id=-1"
            if page_token:
                path += f"&page_token={page_token}"
            r = api("GET", path, token=self.tokens.access)
            if r.get("code") != 0:
                raise RuntimeError(f"读取块失败: {r}")
            items.extend(r["data"].get("items", []))
            if not r["data"].get("has_more"):
                break
            page_token = r["data"].get("page_token")
        return items

    def page(self, blocks):
        for b in blocks:
            if b.get("block_type") == PAGE:
                return b
        raise RuntimeError("未找到页面根块")

    def insert(self, index, children):
        r = api("POST", f"/docx/v1/documents/{self.doc_id}/blocks/{self.doc_id}/children",
                token=self.tokens.access, payload={"index": index, "children": children})
        if r.get("code") != 0:
            raise RuntimeError(f"插入失败: {r}")
        return r


def block_text(block):
    for key, node in block.items():
        if key.startswith("heading") or key in ("bullet", "text", "ordered"):
            if isinstance(node, dict) and node.get("elements"):
                return "".join((e.get("text_run") or {}).get("content", "") for e in node["elements"])
    return ""


def heading_level(block):
    bt = block.get("block_type", 0)
    if HEADING_START <= bt <= HEADING_END:
        return bt - 2
    return 0


def make_heading(level, text):
    bt = level + 2
    key = f"heading{level}"
    return {"block_type": bt, key: {"elements": [{"text_run": {"content": text}}]}}


def make_bullet(text):
    return {"block_type": BULLET, "bullet": {"elements": [{"text_run": {"content": text}}]}}


def period_titles(day):
    iso = day.isocalendar()
    return {
        "quarter": f"{day.year}年{(day.month - 1) // 3 + 1}季度",
        "month": f"{day.year}年{day.month}月",
        "week": f"{iso.year}年第{iso.week}周",
        "date": f"{day.year}年{day.month}月{day.day}日 星期{WEEKDAYS[day.weekday()]}",
    }


def find_heading(blocks, level, text):
    for b in blocks:
        if heading_level(b) == level and block_text(b).strip() == text:
            return b
    return None


def insert_after_section(doc, blocks, heading_block, level, new_children):
    """把新块插到某标题所在区域的末尾（遇到同级或更高级标题为止）。"""
    page = doc.page(blocks)
    children = page.get("children", [])
    by_id = {b["block_id"]: b for b in blocks}
    start = children.index(heading_block["block_id"])
    pos = start + 1
    while pos < len(children):
        b = by_id.get(children[pos], {})
        lv = heading_level(b)
        if lv and lv <= level:
            break
        pos += 1
    doc.insert(pos, new_children)
    return pos


def ensure_heading(doc, level, text, parent_block, parent_level):
    blocks = doc.blocks()
    found = find_heading(blocks, level, text)
    if found:
        return found
    doc_blocks = blocks
    insert_after_section(doc, doc_blocks, parent_block, parent_level, [make_heading(level, text)])
    blocks = doc.blocks()
    found = find_heading(blocks, level, text)
    if not found:
        raise RuntimeError(f"创建标题失败: {text}")
    return found


def ensure_today(doc, day):
    titles = period_titles(day)
    blocks = doc.blocks()
    log_h1 = find_heading(blocks, 1, "📅 行知日志")
    if not log_h1:
        raise RuntimeError("未找到「📅 行知日志」板块")

    quarter = find_heading(blocks, 2, titles["quarter"])
    if not quarter:
        insert_after_section(doc, blocks, log_h1, 1, [make_heading(2, titles["quarter"])])
        blocks = doc.blocks()
        quarter = find_heading(blocks, 2, titles["quarter"])

    month = find_heading(blocks, 3, titles["month"])
    if not month:
        insert_after_section(doc, blocks, quarter, 2, [make_heading(3, titles["month"])])
        blocks = doc.blocks()
        month = find_heading(blocks, 3, titles["month"])

    week = find_heading(blocks, 4, titles["week"])
    if not week:
        insert_after_section(doc, blocks, month, 3, [make_heading(4, titles["week"])])
        blocks = doc.blocks()
        week = find_heading(blocks, 4, titles["week"])

    date_block = find_heading(blocks, 5, titles["date"])
    if not date_block:
        insert_after_section(doc, blocks, week, 4, [make_heading(5, titles["date"])])
        blocks = doc.blocks()
        date_block = find_heading(blocks, 5, titles["date"])

    return date_block, titles


def following_items(doc, blocks, date_block):
    page = doc.page(blocks)
    children = page.get("children", [])
    by_id = {b["block_id"]: b for b in blocks}
    start = children.index(date_block["block_id"])
    items = []
    for cid in children[start + 1:]:
        b = by_id.get(cid, {})
        if heading_level(b):
            break
        items.append((cid, block_text(b)))
    return items


def cmd_today(doc, day):
    tokens = doc.tokens
    tokens.refresh_if_needed()
    date_block, titles = ensure_today(doc, day)
    blocks = doc.blocks()
    items = following_items(doc, blocks, date_block)
    if any(t.strip() == "开工" for _, t in items):
        print(f"{titles['date']} 已有开工，跳过")
        return
    page = doc.page(blocks)
    children = page.get("children", [])
    index = children.index(date_block["block_id"]) + 1
    doc.insert(index, [make_bullet("开工")])
    after = following_items(doc, doc.blocks(), find_heading(doc.blocks(), 5, titles["date"]))
    print(f"{titles['date']} 已写入开工，当前条目：{[t for _, t in after]}")


def cmd_append(doc, day, text):
    tokens = doc.tokens
    tokens.refresh_if_needed()
    date_block, titles = ensure_today(doc, day)
    blocks = doc.blocks()
    items = following_items(doc, blocks, date_block)
    if any(text.strip() == t.strip() for _, t in items):
        print(f"{titles['date']} 已存在相同内容，跳过")
        return
    page = doc.page(blocks)
    children = page.get("children", [])
    index = children.index(date_block["block_id"]) + 1 + len(items)
    doc.insert(index, [make_bullet(text)])
    print(f"{titles['date']} 已追加：{text}")


def cmd_show(doc, day):
    doc.tokens.refresh_if_needed()
    blocks = doc.blocks()
    titles = period_titles(day)
    date_block = find_heading(blocks, 5, titles["date"])
    if not date_block:
        print(f"{titles['date']} 尚未创建")
        return
    print(titles["date"])
    for _, t in following_items(doc, blocks, date_block):
        print(f"- {t}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["today", "append", "show"])
    parser.add_argument("text", nargs="?", default="")
    parser.add_argument("--date", default="")
    args = parser.parse_args()

    cfg = load_env()
    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    doc = Doc(Tokens(), cfg["FEISHU_DOC_TOKEN"])

    if args.action == "today":
        cmd_today(doc, day)
    elif args.action == "append":
        if not args.text:
            parser.error("append 需要内容")
        cmd_append(doc, day, args.text)
    else:
        cmd_show(doc, day)


if __name__ == "__main__":
    main()
