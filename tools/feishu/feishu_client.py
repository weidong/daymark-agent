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


def api(method, path, token=None, payload=None, retries=5):
    url = path if path.startswith("http") else BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data:
        headers["Content-Type"] = "application/json; charset=utf-8"
    last = None
    delay = 2
    for attempt in range(retries):
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
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
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


def section_span(doc, blocks, parent_block):
    """返回父标题区域在页面 children 中的 [start, end)。"""
    page = doc.page(blocks)
    children = page.get("children", [])
    by_id = {b["block_id"]: b for b in blocks}
    start = children.index(parent_block["block_id"]) + 1
    parent_level = heading_level(parent_block)
    end = start
    while end < len(children):
        lv = heading_level(by_id.get(children[end], {}))
        if lv and lv <= parent_level:
            break
        end += 1
    return children, by_id, start, end


def find_in_section(doc, blocks, parent_block, level, text):
    children, by_id, start, end = section_span(doc, blocks, parent_block)
    for i in range(start, end):
        b = by_id.get(children[i], {})
        if heading_level(b) == level and block_text(b).strip() == text:
            return b
    return None


def insert_at_section_top(doc, blocks, parent_block, child_level, new_children, skip_keywords=()):
    """插到区域最前面；跳过速览、总结这类应固定在上方的同级标题。"""
    children, by_id, start, end = section_span(doc, blocks, parent_block)
    pos = start
    while pos < end:
        b = by_id.get(children[pos], {})
        lv = heading_level(b)
        text = block_text(b).strip()
        if lv == child_level and any(k in text for k in skip_keywords):
            pos += 1
            while pos < end:
                nlv = heading_level(by_id.get(children[pos], {}))
                if nlv and nlv <= child_level:
                    break
                pos += 1
            continue
        break
    doc.insert(pos, new_children)
    return pos


def locate_chain(doc, day):
    """按 板块 → 季度 → 月份 → 周 → 日期 依次定位，缺失的返回 None。"""
    titles = period_titles(day)
    blocks = doc.blocks()
    h1 = find_heading(blocks, 1, "📅 行知日志")
    quarter = find_in_section(doc, blocks, h1, 2, titles["quarter"]) if h1 else None
    month = find_in_section(doc, blocks, quarter, 3, titles["month"]) if quarter else None
    week = find_in_section(doc, blocks, month, 4, titles["week"]) if month else None
    date_block = find_in_section(doc, blocks, week, 5, titles["date"]) if week else None
    return blocks, h1, quarter, month, week, date_block, titles


def ensure_today(doc, day):
    """按倒序补齐层级：越新的周和日期越靠上。"""
    blocks, log_h1, quarter, month, week, date_block, titles = locate_chain(doc, day)
    if not log_h1:
        raise RuntimeError("未找到「📅 行知日志」板块")

    if not quarter:
        insert_at_section_top(doc, blocks, log_h1, 2, [make_heading(2, titles["quarter"])])
        blocks, log_h1, quarter, month, week, date_block, titles = locate_chain(doc, day)
        if not quarter:
            raise RuntimeError(f"创建季度标题失败: {titles['quarter']}")

    if not month:
        insert_at_section_top(doc, blocks, quarter, 3, [make_heading(3, titles["month"])],
                              skip_keywords=("总结",))
        blocks, log_h1, quarter, month, week, date_block, titles = locate_chain(doc, day)
        if not month:
            raise RuntimeError(f"创建月份标题失败: {titles['month']}")

    if not week:
        insert_at_section_top(doc, blocks, month, 4, [make_heading(4, titles["week"])],
                              skip_keywords=("速览",))
        blocks, log_h1, quarter, month, week, date_block, titles = locate_chain(doc, day)
        if not week:
            raise RuntimeError(f"创建周标题失败: {titles['week']}")

    if not date_block:
        insert_at_section_top(doc, blocks, week, 5, [make_heading(5, titles["date"])])
        blocks, log_h1, quarter, month, week, date_block, titles = locate_chain(doc, day)
        if not date_block:
            raise RuntimeError(f"创建日期标题失败: {titles['date']}")

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


def collect_month(doc, blocks, year, month):
    """读取某个月份标题下的所有日期和条目。"""
    month_title = f"{year}年{month}月"
    page = doc.page(blocks)
    by_id = {b["block_id"]: b for b in blocks}
    children = page.get("children", [])

    start = None
    for i, cid in enumerate(children):
        b = by_id.get(cid, {})
        if heading_level(b) == 3 and block_text(b).strip() == month_title:
            start = i
            break
    if start is None:
        return month_title, None

    days, current = [], None
    for cid in children[start + 1:]:
        b = by_id.get(cid, {})
        lv = heading_level(b)
        if lv and lv <= 3:
            break
        if lv == 5:
            current = {"date": block_text(b).strip(), "items": []}
            days.append(current)
        elif lv:
            continue
        elif current is not None:
            text = block_text(b).strip()
            if text:
                current["items"].append(text)
    return month_title, days


def locate_day(doc, day):
    doc.tokens.refresh_if_needed()
    blocks = doc.blocks()
    titles = period_titles(day)
    date_block = find_heading(blocks, 5, titles["date"])
    if not date_block:
        return None, None, titles["date"]
    page = doc.page(blocks)
    return blocks, page.get("children", []), titles["date"]


def find_entry(doc, blocks, children, date_block, text):
    hits = [(i, t) for i, t in following_items(doc, blocks, date_block) if t == text]
    if not hits:
        return None, 0
    if len(hits) > 1:
        return None, len(hits)
    return hits[0][0], 1


def cmd_update(doc, day, old, new):
    blocks, children, title = locate_day(doc, day)
    if not blocks:
        print(f"{title} 尚无记录")
        return
    date_block = find_heading(blocks, 5, title)
    raw, count = find_entry(doc, blocks, children, date_block, old)
    if count == 0:
        print(f"{title} 未找到内容：{old}")
        return
    if count > 1:
        print(f"匹配到 {count} 条相同内容，请写得更完整后重试")
        return

    block_id = raw if isinstance(raw, str) else children[raw]
    r = api("PATCH", f"/docx/v1/documents/{doc.doc_id}/blocks/{block_id}",
            token=doc.tokens.access,
            payload={"update_text_elements": {"elements": [{"text_run": {"content": new}}]}})
    print("更新成功" if r.get("code") == 0 else f"更新失败: {r}")


def cmd_delete(doc, day, text, confirmed):
    if not confirmed:
        print("删除需要显式确认，请加 --yes")
        return
    blocks, children, title = locate_day(doc, day)
    if not blocks:
        print(f"{title} 尚无记录")
        return
    date_block = find_heading(blocks, 5, title)
    raw, count = find_entry(doc, blocks, children, date_block, text)
    if count == 0:
        print(f"{title} 未找到内容：{text}")
        return
    if count > 1:
        print(f"匹配到 {count} 条相同内容，请写得更完整后重试")
        return

    block_id = raw if isinstance(raw, str) else children[raw]
    index = children.index(block_id)
    r = api("DELETE",
            f"/docx/v1/documents/{doc.doc_id}/blocks/{doc.doc_id}/children/batch_delete",
            token=doc.tokens.access,
            payload={"start_index": index, "end_index": index + 1})
    print("删除成功" if r.get("code") == 0 else f"删除失败: {r}")


def cmd_month(doc, year, month, as_json):
    doc.tokens.refresh_if_needed()
    title, days = collect_month(doc, doc.blocks(), year, month)
    if days is None:
        print(f"{title} 尚未创建")
        return

    work_days = [d for d in days if any(i != "开工" for i in d["items"])]
    total = sum(1 for d in days for i in d["items"] if i != "开工")

    if as_json:
        print(json.dumps({
            "month": title,
            "days": days,
            "work_days": len(work_days),
            "total_items": total,
        }, ensure_ascii=False, indent=2))
        return

    print(title)
    for d in days:
        print(d["date"])
        for item in d["items"]:
            print(f"- {item}")
    print(f"有记录 {len(days)} 天，其中有工作内容 {len(work_days)} 天，共 {total} 条（不含开工）")


def ensure_quickview(doc, year, month):
    """找到或创建月份速览标题，返回标题块、标题文字、是否为占位标题。"""
    title = f"{year}年{month}月速览"
    blocks = doc.blocks()
    found = find_heading(blocks, 4, title)
    if found:
        return found, title, False

    for b in blocks:
        if heading_level(b) == 4:
            text = block_text(b).strip()
            if text.startswith(f"{title}（") or text.startswith(f"{title}("):
                return b, title, True

    month_block = find_heading(blocks, 3, f"{year}年{month}月")
    if not month_block:
        raise RuntimeError(f"未找到 {year}年{month}月 标题")
    page = doc.page(blocks)
    index = page["children"].index(month_block["block_id"]) + 1
    doc.insert(index, [make_heading(4, title)])
    blocks = doc.blocks()
    return find_heading(blocks, 4, title), title, False


def rename_heading(doc, block_id, text):
    r = api("PATCH", f"/docx/v1/documents/{doc.doc_id}/blocks/{block_id}",
            token=doc.tokens.access,
            payload={"update_text_elements": {"elements": [{"text_run": {"content": text}}]}})
    return r.get("code") == 0


def cmd_init(doc, day):
    """只建板块、当前季度、当前月份，不建周和日期。"""
    doc.tokens.refresh_if_needed()
    blocks = doc.blocks()
    created = []

    log_h1 = find_heading(blocks, 1, "📅 行知日志")
    if not log_h1:
        page = doc.page(blocks)
        doc.insert(len(page.get("children", [])), [make_heading(1, "📅 行知日志")])
        blocks = doc.blocks()
        log_h1 = find_heading(blocks, 1, "📅 行知日志")
        if not log_h1:
            raise RuntimeError("创建「📅 行知日志」失败")
        created.append("板块「📅 行知日志」(H1)")

    titles = period_titles(day)

    quarter = find_in_section(doc, blocks, log_h1, 2, titles["quarter"])
    if not quarter:
        insert_at_section_top(doc, blocks, log_h1, 2, [make_heading(2, titles["quarter"])])
        blocks = doc.blocks()
        quarter = find_in_section(doc, blocks, log_h1, 2, titles["quarter"])
        if not quarter:
            raise RuntimeError(f"创建季度标题失败: {titles['quarter']}")
        created.append(f"{titles['quarter']}(H2)")

    month = find_in_section(doc, blocks, quarter, 3, titles["month"])
    if not month:
        insert_at_section_top(doc, blocks, quarter, 3, [make_heading(3, titles["month"])],
                              skip_keywords=("总结",))
        blocks = doc.blocks()
        month = find_in_section(doc, blocks, quarter, 3, titles["month"])
        if not month:
            raise RuntimeError(f"创建月份标题失败: {titles['month']}")
        created.append(f"{titles['month']}(H3)")

    blocks = doc.blocks()
    ready = all([
        find_heading(blocks, 1, "📅 行知日志"),
        find_heading(blocks, 2, titles["quarter"]),
        find_heading(blocks, 3, titles["month"]),
    ])
    if created:
        print("已创建：" + "、".join(created))
    else:
        print("结构已就绪，无需创建")
    print(f"回读确认：{'H1/H2/H3 均存在' if ready else '结构不完整'}")


def cmd_review(doc, year, month, path, force):
    doc.tokens.refresh_if_needed()
    view, title, is_placeholder = ensure_quickview(doc, year, month)
    blocks = doc.blocks()
    page = doc.page(blocks)
    children = page.get("children", [])
    by_id = {b["block_id"]: b for b in blocks}

    start = children.index(view["block_id"])
    end = start + 1
    while end < len(children):
        lv = heading_level(by_id.get(children[end], {}))
        if lv and lv <= 4:
            break
        end += 1

    if end > start + 1 and not force and not is_placeholder:
        print(f"{title} 已有内容，未覆盖；确需重写请加 --force")
        return

    if end > start + 1:
        r = api("DELETE",
                f"/docx/v1/documents/{doc.doc_id}/blocks/{doc.doc_id}/children/batch_delete",
                token=doc.tokens.access,
                payload={"start_index": start + 1, "end_index": end})
        if r.get("code") != 0:
            raise RuntimeError(f"清理旧内容失败: {r}")
        blocks = doc.blocks()
        page = doc.page(blocks)
        children = page.get("children", [])
        start = children.index(view["block_id"])

    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    payload = [
        {"block_type": 2, "text": {"elements": [{"text_run": {"content": line}}]}}
        for line in lines
    ]
    doc.insert(start + 1, payload)
    if is_placeholder:
        renamed = rename_heading(doc, view["block_id"], title)
        print(f"占位标题已改为 {title}" if renamed else "占位标题改名失败，内容已写入")
    print(f"{title} 已写入 {len(payload)} 段")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "today", "append", "show", "update", "delete", "month", "review"])
    parser.add_argument("text", nargs="?", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--month", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--old", default="")
    parser.add_argument("--new", default="")
    parser.add_argument("--text", dest="del_text", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cfg = load_env()
    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    doc = Doc(Tokens(), cfg["FEISHU_DOC_TOKEN"])

    if args.action == "init":
        cmd_init(doc, day)
    elif args.action == "today":
        cmd_today(doc, day)
    elif args.action == "append":
        if not args.text:
            parser.error("append 需要内容")
        cmd_append(doc, day, args.text)
    elif args.action == "update":
        if not args.old or not args.new:
            parser.error("update 需要 --old 和 --new")
        cmd_update(doc, day, args.old, args.new)
    elif args.action == "delete":
        if not args.del_text:
            parser.error("delete 需要 --text 指定内容")
        cmd_delete(doc, day, args.del_text, args.yes)
    elif args.action == "month":
        month = args.month or day.strftime("%Y-%m")
        year, mon = month.split("-")
        cmd_month(doc, int(year), int(mon), args.json)
    elif args.action == "review":
        if not args.file:
            parser.error("review 需要 --file")
        month = args.month or day.strftime("%Y-%m")
        year, mon = month.split("-")
        cmd_review(doc, int(year), int(mon), args.file, args.force)
    else:
        cmd_show(doc, day)


if __name__ == "__main__":
    main()
