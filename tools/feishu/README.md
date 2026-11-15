# DayMark 飞书工具

用**用户身份**读写飞书日志文档，不需要机器人、不需要把应用加为文档协作者。

## 首次授权

```bash
python tools/feishu/login.py
```

打开打印出的链接，同意授权。令牌保存到 `.secrets/tokens.json`（已被 `.gitignore` 忽略）。

## 日常命令

```bash
# 开工：确保今天五级标题存在，写入“开工”，重复执行不会重复写
python tools/feishu/feishu_client.py today

# 追加一条工作记录
python tools/feishu/feishu_client.py append "完成结营作业"

# 查看今天已记录的内容
python tools/feishu/feishu_client.py show

# 修改一条记录（原文需精确匹配）
python tools/feishu/feishu_client.py update --old "原文" --new "新文"

# 删除一条记录（必须显式加 --yes）
python tools/feishu/feishu_client.py delete --text "原文" --yes

# 按月汇总，供复盘使用
python tools/feishu/feishu_client.py month --month 2026-11
python tools/feishu/feishu_client.py month --month 2026-11 --json

# 写入月度速览，--file 为四类归纳后的内容
python tools/feishu/feishu_client.py review --month 2026-11 --file .tmp/review-2026-11.md

# 重写已有速览（会先清空旧内容）
python tools/feishu/feishu_client.py review --month 2026-11 --file .tmp/review-2026-11.md --force

# 指定日期（补记或测试）
python tools/feishu/feishu_client.py show --date 2026-11-15
```

## 结构规则

按 `📅 行知日志` 下的层级定位，缺哪层建哪层：

```text
H1 📅 行知日志
  H2 2026年4季度
    H3 2026年11月
      H4 2026年第46周
        H5 2026年11月15日 星期日
          - 开工
```

- 周编号使用 ISO 8601
- 月份不补零
- 日期是唯一锚点，周标题允许重复
- 写入后回读确认，相同内容不重复写

## 配置

项目根目录 `.env`：

```text
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_DOC_TOKEN=
```

飞书开放平台需要为应用开通**用户身份**权限：

- `docx:document`
- `wiki:wiki:readonly`
- `drive:drive:readonly`

## 令牌有效期

`user_access_token` 约 2 小时，`refresh_token` 30 天。客户端在到期前 5 分钟自动刷新。若 refresh_token 也过期，重新执行一次 `login.py`。
