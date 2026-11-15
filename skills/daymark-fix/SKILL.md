---
name: daymark-fix
description: 用户说“改一下那条”“删掉刚才那条”“记错了”时，修改或删除当天已记录的工作条目
channel: chat
---
# 修正记录

修改或删除已经写进飞书的条目。删除必须先复述并等待用户明确确认。

## 修改

1. 先看当天内容，确认原文一字不差：

```bash
python tools/feishu/feishu_client.py show
```

2. 向用户复述“原文 → 新文”，得到同意后执行：

```bash
python tools/feishu/feishu_client.py update --old "原文" --new "新文"
```

3. 输出 `更新成功` 才算完成；`未找到内容` 说明原文不匹配，重新核对；`匹配到 N 条` 说明有重复，请用户补充更多字。

## 删除

1. 先用 `show` 找到要删的那一条。
2. 回复用户：

```text
将删除：<原文>
确认后我再执行。
```

3. 用户明确说“确认删除”“删吧”之后，才执行：

```bash
python tools/feishu/feishu_client.py delete --text "原文" --yes
```

4. 输出 `删除成功` 才算完成。

## 补记到其他日期

用户说“前天那条”时，先换算成具体日期，再用 `--date`：

```bash
python tools/feishu/feishu_client.py show --date 2026-11-14
python tools/feishu/feishu_client.py update --date 2026-11-14 --old "原文" --new "新文"
```

## 必须遵守

- 没有用户明确确认，不执行 `delete --yes`。
- 一次只改一条；用户说“都删了”时，逐条确认。
- 不删除日期标题、速览、季度标题，只改日期下的条目。
- 修改后要回读一次，确认新内容已在文档里。
- 原文必须精确匹配，不做模糊替换。

## 不能做

- 不修改“开工”以外的历史总结。
- 不因用户一句“整理一下”就批量重写。
- 不在未确认的情况下删除任何内容。
