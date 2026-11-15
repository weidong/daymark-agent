"""DayMark 飞书用户授权：一次授权，令牌保存在 .secrets/tokens.json。

用法：
    python tools/feishu/login.py

浏览器会打开授权页，同意后自动回调 http://localhost:8080/callback。
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feishu_client import TOKEN_FILE, api, load_env  # noqa: E402

SCOPES = "docx:document wiki:wiki:readonly drive:drive:readonly space:document:delete"
REDIRECT = "http://localhost:8080/callback"
HOLD = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [""])[0]
        HOLD["code"] = code
        body = (
            "<html><head><meta charset='utf-8'><title>DayMark</title></head>"
            "<body style='font-family:sans-serif;padding:40px'>"
            "<h2>授权完成</h2><p>可以关闭此页面。</p></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_env()
    app_id = cfg["FEISHU_APP_ID"]
    redirect = quote(REDIRECT, safe="")
    scope = quote(SCOPES, safe="")
    url = (
        "https://open.feishu.cn/open-apis/authen/v1/authorize"
        f"?app_id={app_id}&redirect_uri={redirect}&scope={scope}&state=daymark"
    )
    print("请打开下面的链接并同意授权：")
    print(url)
    print("等待回调 ...")

    server = HTTPServer(("127.0.0.1", 8080), Handler)
    while "code" not in HOLD:
        server.handle_request()
    server.server_close()

    code = HOLD["code"]
    tr = api("POST", "/auth/v3/tenant_access_token/internal", payload={
        "app_id": app_id, "app_secret": cfg["FEISHU_APP_SECRET"],
    })
    if tr.get("code") != 0:
        print(f"获取 tenant_access_token 失败: {tr}")
        sys.exit(1)

    ur = api("POST", "/authen/v1/access_token", token=tr["tenant_access_token"], payload={
        "grant_type": "authorization_code", "code": code,
    })
    if ur.get("code") != 0:
        print(f"换取 user_access_token 失败: {ur}")
        sys.exit(1)

    data = ur["data"]
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expires_in"),
            "saved_at": int(time.time()),
        }, f, ensure_ascii=False, indent=2)
    print(f"授权成功，令牌已保存到 {TOKEN_FILE}")


if __name__ == "__main__":
    main()
