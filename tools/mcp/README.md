# Daymark MCP 服务

把飞书的年度记录读写能力封装成 MCP 工具，供Daymark（`core-mcp`）这类客户端直接调用，不需要改客户端代码。

## 前置条件

1. 已完成 `tools/feishu/README.md` 里的 user_access_token 授权，`.env` 中有 `FEISHU_DOC_TOKEN`。
2. Python 3 可用；服务只依赖标准库。

## 启动

```bash
python tools/mcp/daymark_mcp_server.py --port 8765
```

自检：

```bash
curl http://127.0.0.1:8765/health
# {"status": "ok", "server": "daymark-feishu"}
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/mcp` | Streamable HTTP，JSON-RPC 2.0（`initialize` / `tools/list` / `tools/call`） |
| GET | `/health` | 存活检查 |

协议版本 `2025-06-18`；服务端会把 `Mcp-Session-Id` 原样回传，客户端可以携带。

## 工具

| 名称 | 读/写 | 说明 |
|---|---|---|
| `daymark_today` | 写 | 写入当天的“开工”，已开工则跳过 |
| `daymark_append` | 写 | 追加一条当日记录，重复内容自动跳过 |
| `daymark_show` | 读 | 查看某天已记录的内容 |
| `daymark_month` | 读 | 汇总某月所有日期与条目 |
| `daymark_update` | 写 | 按原文精确匹配，改一条记录 |
| `daymark_delete` | 写 | 删除一条记录，需要 `confirm: true` |

只读工具带 `annotations.readOnlyHint: true`，客户端可以据此只在写操作前请求确认。

日期/月份参数缺省为当天/当月，格式为 `YYYY-MM-DD`、`YYYY-MM`。

## 在模拟器里接Daymark

```bash
adb reverse tcp:8765 tcp:8765
```

Daymark「服务 → 其他 MCP 服务」里填 `http://127.0.0.1:8765/mcp` 即可。地址校验允许
`localhost` / `127.0.0.1` 走 http，其余必须是 https。

注意：Android 15 默认禁止明文 HTTP，所以 debug 包通过
`app/src/debug/res/xml/network_security_config.xml` 只对回环地址放开明文，
release 包不受影响。

## 已知修复

- `core-mcp` 的 `${VAR}` 占位符正则原先写成 `\$\{([A-Za-z_][A-Za-z0-9_]*)}`，
  Android 的 ICU 正则不接受未转义的 `}`，会让配置解析直接崩溃；已改为 `\\}`。
- `Doc.blocks()` 增加单次操作内的缓存，一次追加从 3～4 次全文档读取降到 1 次，
  实测耗时从 8.2s 降到 0.9s。
