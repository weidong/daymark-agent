---
name: daymark-review
description: 用户说“月度复盘”“季度总结”“半年总结”“年度总结”时，汇总对应周期的日志并生成复盘草稿写入飞书
channel: chat
---
# 周期复盘

把一段时间的零散记录归纳成四类速览，写成草稿。支持月、季度、半年、年度四种周期。

## 判断周期

| 用户说法 | 周期 | 读取命令 |
|---|---|---|
| 月度复盘、这个月、月报 | month | `month --month 2026-11 --json` |
| 季度复盘、这个季度、Q4 | quarter | `period --period quarter --at 2026-Q4 --json` |
| 半年总结、上半年、下半年 | half | `period --period half --at 2026-H2 --json` |
| 年度总结、今年 | year | `period --period year --at 2026 --json` |

周期说不清时先问，不要默认按当前季度处理。

## 执行

1. 读取该周期的数据：

```bash
python tools/feishu/feishu_client.py period --period quarter --at 2026-Q4 --json
```

月度改用 `python tools/feishu/feishu_client.py month --month 2026-11 --json`。

2. 如果 `total_items` 为 0，或输出“该区间暂无记录”，直接回复“该周期暂无工作记录，无法生成复盘”，停止。

3. 按四类归纳，每类 1 到 3 条，每条末尾标注来源日期（如 `11-15`）：

```text
公司工作
个人业务
个人学习
日常生活
```

没有内容的类别写“本期无相关记录”，不要凑数。

4. 周期越长，归纳口径越粗：季度加一条“季度印象”；半年、年度加“阶段主线 / 成果 / 缺口”各一条。这些只能来自 `quarters`、`months` 里已写入的下级总结，不补充推断。

5. 把内容写入临时文件 `.tmp/review-<周期>.md`，第一行固定为：

```text
【草稿】生成于 YYYY-MM-DD，待确认
```

文件名示例：`review-2026-11.md`、`review-2026-Q4.md`、`review-2026-H2.md`、`review-2026-year.md`。

6. 写入飞书：

```bash
python tools/feishu/feishu_client.py review --month 2026-11 --file .tmp/review-2026-11.md
python tools/feishu/feishu_client.py review --period quarter --at 2026-Q4 --file .tmp/review-2026-Q4.md
python tools/feishu/feishu_client.py review --period half --at 2026-H2 --file .tmp/review-2026-H2.md
python tools/feishu/feishu_client.py review --period year --at 2026 --file .tmp/review-2026-year.md
```

写入位置：月度 → 该月下的 `YYYY年M月速览`（H4）；季度 → 季度下的 `YYYY年M季度总结`（H3）；半年、年度 → `📅 行知日志` 顶部的 `YYYY年上/下半年总结`、`YYYY年度总结`（H2）。

7. 输出 `已写入 N 段` 表示成功；输出 `已有内容，未覆盖` 表示该周期已有总结，先问用户是否重写，得到明确同意后才加 `--force`。

8. 回读确认：季度、半年、年度重跑第 1 步命令，月度跑 `month --month 2026-11`。

## 写作规则

- 只使用日志和下级总结里出现的事实，不补数据、不推测影响、不编造成果。
- “推进”“计划”不能写成“完成”。
- 季度、半年、年度引用已写入的月度速览和季度总结，不从日期条目重算，避免同一件事重复计算。
- 同类事项合并成一条，保留最能说明结果的那句。
- 记录少于 3 天时，开头加一句“本期记录较少，复盘覆盖可能不足”。
- 只写草稿，不标注“已确认”；确认由用户说了才算。

## 不能做

- 不修改、不删除原始日志条目。
- 不覆盖已有总结，除非用户明确同意。
- 不臆造季度印象或年度主线；样本不足就写“样本不足，暂不判断趋势”。
- 不把账号、密钥、内部链接写进复盘。
