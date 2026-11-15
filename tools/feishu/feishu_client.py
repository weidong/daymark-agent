"""DayMark 飞书客户端：定位日志结构、写入记录、读写校验。

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
import re
import sys
import time
import urllib.error
import urllib.parse
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
        self._cache = None

    def blocks(self):
        # 同一次操作里多处需要块列表，缓存一次，减少飞书接口往返。
        if self._cache is not None:
            return self._cache
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
        self._cache = items
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
        self._cache = None
        return r

    def delete(self, start, end):
        r = api("DELETE", f"/docx/v1/documents/{self.doc_id}/blocks/{self.doc_id}/children/batch_delete",
                token=self.tokens.access, payload={"start_index": start, "end_index": end})
        if r.get("code") != 0:
            raise RuntimeError(f"删除失败: {r}")
        self._cache = None
        return r

    def patch(self, block_id, elements):
        r = api("PATCH", f"/docx/v1/documents/{self.doc_id}/blocks/{block_id}",
                token=self.tokens.access, payload={"update_text_elements": {"elements": elements}})
        if r.get("code") != 0:
            raise RuntimeError(f"更新失败: {r}")
        self._cache = None
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
        text = block_text(b).strip()
        if text:
            items.append((cid, text))
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


def section_lines(doc, blocks, block):
    """某标题下、下一个同级或更高级标题之前的非标题文本行。"""
    children, by_id, start, end = section_span(doc, blocks, block)
    lines = []
    for i in range(start, end):
        b = by_id.get(children[i], {})
        if heading_level(b):
            continue
        text = block_text(b).strip()
        if text:
            lines.append(text)
    return lines


def sub_headings(doc, blocks, parent):
    """父标题下紧邻一级的子标题，按文档顺序。"""
    level = heading_level(parent) + 1
    children, by_id, start, end = section_span(doc, blocks, parent)
    out = []
    for i in range(start, end):
        b = by_id.get(children[i], {})
        if heading_level(b) == level:
            out.append(b)
    return out


MONTH_TITLE = re.compile(r"^(\d{4})年(\d{1,2})月$")
QUARTER_TITLE = re.compile(r"^(\d{4})年(\d)季度$")


def month_summary(doc, blocks, month_block):
    for b in sub_headings(doc, blocks, month_block):
        if "速览" in block_text(b):
            return section_lines(doc, blocks, b)
    return []


def month_stats(doc, blocks, year, month):
    _, days = collect_month(doc, blocks, year, month)
    if days is None:
        return 0, 0
    work_days = len([d for d in days if any(i != "开工" for i in d["items"])])
    total = sum(1 for d in days for i in d["items"] if i != "开工")
    return work_days, total


def collect_period(doc, year, m1, m2):
    """按月份区间汇总季度、半年、年度数据，只读已有内容，不做推断。"""
    blocks = doc.blocks()
    result = {"work_days": 0, "total_items": 0, "quarters": [], "months": [], "halves": []}
    h1 = find_heading(blocks, 1, "📅 行知日志")
    if not h1:
        return result

    for b in sub_headings(doc, blocks, h1):
        text = block_text(b).strip()
        if re.match(rf"^{year}年(上|下)半年总结$", text):
            result["halves"].append({"title": text, "summary": section_lines(doc, blocks, b)})
            continue

        qm = QUARTER_TITLE.match(text)
        if not qm or int(qm.group(1)) != year:
            continue
        qnum = int(qm.group(2))
        if qnum * 3 < m1 or qnum * 3 - 2 > m2:
            continue

        entry = {"title": text, "summary": []}
        for hb in sub_headings(doc, blocks, b):
            htext = block_text(hb).strip()
            if "总结" in htext or "复盘" in htext:
                entry["summary"] = section_lines(doc, blocks, hb)
                continue
            mm = MONTH_TITLE.match(htext)
            if not mm or int(mm.group(1)) != year:
                continue
            mnum = int(mm.group(2))
            if not (m1 <= mnum <= m2):
                continue
            wd, total = month_stats(doc, blocks, year, mnum)
            result["months"].append({
                "month": htext,
                "work_days": wd,
                "total_items": total,
                "summary": month_summary(doc, blocks, hb),
            })
            result["work_days"] += wd
            result["total_items"] += total
        result["quarters"].append(entry)
    return result


def parse_period(period, at):
    """返回 (区间标题, 年, 起月, 止月)。"""
    if period == "quarter":
        year_s, q_s = at.split("-Q")
        q = int(q_s)
        return f"{year_s}年{q}季度", int(year_s), q * 3 - 2, q * 3
    if period == "half":
        year_s, h_s = at.split("-H")
        h = int(h_s)
        return f"{year_s}年{'上' if h == 1 else '下'}半年", int(year_s), 1 if h == 1 else 7, 6 if h == 1 else 12
    return f"{at}年度", int(at), 1, 12


def cmd_period(doc, period, at, as_json):
    doc.tokens.refresh_if_needed()
    title, year, m1, m2 = parse_period(period, at)
    data = collect_period(doc, year, m1, m2)
    data = {"period": title, "range": f"{year}-{m1:02d} ~ {year}-{m2:02d}", **data}

    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print(f"{title}（{data['range']}）")
    if not data["months"] and not data["quarters"] and not data["halves"]:
        print("该区间暂无记录")
        return
    for h in data["halves"]:
        print(f"[半年总结] {h['title']}：{len(h['summary'])} 段")
    for q in data["quarters"]:
        print(f"[季度总结] {q['title']}：{len(q['summary'])} 段")
    for m in data["months"]:
        print(f"[月份] {m['month']} 有记录 {m['work_days']} 天，{m['total_items']} 条")
        for line in m["summary"]:
            print(f"  - {line}")
    print(f"合计：有记录 {data['work_days']} 天，{data['total_items']} 条（不含开工）")


def summary_target(period, at):
    """返回 (父标题文字, 输出标题层级, 输出标题)。"""
    title, _, _, _ = parse_period(period, at)
    if period == "quarter":
        return title, 3, f"{title}总结"
    return "📅 行知日志", 2, f"{title}总结"


def ensure_summary_heading(doc, parent_text, level, title):
    blocks = doc.blocks()
    found = find_heading(blocks, level, title)
    if found:
        return found, False

    for b in blocks:
        if heading_level(b) == level:
            text = block_text(b).strip()
            if text.startswith(f"{title}（") or text.startswith(f"{title}("):
                return b, True

    parent = find_heading(blocks, level - 1, parent_text)
    if not parent:
        raise RuntimeError(f"未找到父标题: {parent_text}")
    insert_at_section_top(doc, blocks, parent, level, [make_heading(level, title)],
                          skip_keywords=("总结", "复盘", "速览"))
    blocks = doc.blocks()
    found = find_heading(blocks, level, title)
    if not found:
        raise RuntimeError(f"创建标题失败: {title}")
    return found, False


def write_into(doc, heading, title, lines, force, is_placeholder):
    blocks = doc.blocks()
    page = doc.page(blocks)
    children = page.get("children", [])
    by_id = {b["block_id"]: b for b in blocks}
    level = heading_level(heading)

    start = children.index(heading["block_id"])
    end = start + 1
    while end < len(children):
        lv = heading_level(by_id.get(children[end], {}))
        if lv and lv <= level:
            break
        end += 1

    if end > start + 1 and not force and not is_placeholder:
        print(f"{title} 已有内容，未覆盖；确需重写请加 --force")
        return False

    if end > start + 1:
        doc.delete(start + 1, end)
        blocks = doc.blocks()
        page = doc.page(blocks)
        children = page.get("children", [])
        start = children.index(heading["block_id"])

    payload = [
        {"block_type": 2, "text": {"elements": [{"text_run": {"content": line}}]}}
        for line in lines
    ]
    doc.insert(start + 1, payload)
    if is_placeholder:
        renamed = rename_heading(doc, heading["block_id"], title)
        print(f"占位标题已改为 {title}" if renamed else "占位标题改名失败，内容已写入")
    print(f"{title} 已写入 {len(payload)} 段")
    return True


def read_draft(path):
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def link_run(text, url):
    return {"text_run": {"content": text,
                         "text_element_style": {"link": {"url": urllib.parse.quote(url, safe="")}}}}


def tenant_domain(tokens):
    """从云盘列表里取一次企业域名，用于拼接文档地址。"""
    r = api("GET", "/drive/v1/files?page_size=1", token=tokens.access)
    for f in (r.get("data") or {}).get("files", []):
        url = f.get("url") or ""
        if "://" in url:
            return url.split("/")[2]
    return "feishu.cn"


def create_child_doc(doc, title, lines):
    r = api("POST", "/docx/v1/documents", token=doc.tokens.access, payload={"title": title})
    if r.get("code") != 0:
        raise RuntimeError(f"创建子文档失败: {r}")
    sub_id = r["data"]["document"]["document_id"]
    sub = Doc(doc.tokens, sub_id)
    sub.insert(0, [{"block_type": 2, "text": {"elements": [{"text_run": {"content": line}}]}}
                   for line in lines])
    return sub_id


def cmd_split(doc, day, min_chars):
    """把当天超过 min_chars 的条目搬到子文档，主文档只留链接。"""
    doc.tokens.refresh_if_needed()
    blocks, _, title = locate_day(doc, day)
    if not blocks:
        print(f"{title} 尚无记录")
        return
    date_block = find_heading(blocks, 5, title)
    targets = [(bid, text) for bid, text in following_items(doc, blocks, date_block)
               if len(text) >= min_chars and "已外置" not in text]
    if not targets:
        print(f"{title} 无超过 {min_chars} 字的长记录")
        return

    for i, (block_id, text) in enumerate(targets, 1):
        sub_title = f"{day.isoformat()} 长记录" + (f" {i}" if len(targets) > 1 else "")
        sub_id = create_child_doc(doc, sub_title, [text])
        url = f"https://{tenant_domain(doc.tokens)}/docx/{sub_id}"
        doc.patch(block_id, [link_run(f"长文已外置：{sub_title}", url)])
        print(f"已外置：{sub_title} → {url}")
        time.sleep(0.3)
    print(f"{title} 共外置 {len(targets)} 条")


def cmd_review(doc, year, month, path, force):
    doc.tokens.refresh_if_needed()
    view, title, is_placeholder = ensure_quickview(doc, year, month)
    write_into(doc, view, title, read_draft(path), force, is_placeholder)


def cmd_review_period(doc, period, at, path, force):
    doc.tokens.refresh_if_needed()
    parent_text, level, title = summary_target(period, at)
    heading, is_placeholder = ensure_summary_heading(doc, parent_text, level, title)
    write_into(doc, heading, title, read_draft(path), force, is_placeholder)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "today", "append", "show", "update", "delete",
                                           "month", "period", "review", "split"])
    parser.add_argument("text", nargs="?", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--month", default="")
    parser.add_argument("--period", default="", choices=["", "quarter", "half", "year"])
    parser.add_argument("--at", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--old", default="")
    parser.add_argument("--new", default="")
    parser.add_argument("--text", dest="del_text", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-chars", type=int, default=120)
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
    elif args.action == "split":
        cmd_split(doc, day, args.min_chars)
    elif args.action == "period":
        if not args.period or not args.at:
            parser.error("period 需要 --period quarter|half|year 和 --at，如 --at 2026-Q4 / 2026-H2 / 2026")
        cmd_period(doc, args.period, args.at, args.json)
    elif args.action == "review":
        if not args.file:
            parser.error("review 需要 --file")
        if args.period:
            if not args.at:
                parser.error("review --period 需要 --at，如 --at 2026-Q4 / 2026-H2 / 2026")
            cmd_review_period(doc, args.period, args.at, args.file, args.force)
        else:
            month = args.month or day.strftime("%Y-%m")
            year, mon = month.split("-")
            cmd_review(doc, int(year), int(mon), args.file, args.force)
    else:
        cmd_show(doc, day)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        # 失败就如实报错并退出，不输出“已写入”这类成功提示。
        print(f"执行失败：{e}")
        sys.exit(1)
