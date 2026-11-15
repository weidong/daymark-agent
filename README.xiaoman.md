# DayMark

DayMark 是一个面向个人工作记录的 Android Agent。

每天说一句“开工”，它在飞书里创建当天日志；做完一件事说“完成……”，它把这条追加到当天。月末、季末、年末，它根据平时的短记录生成复盘草稿，由人确认后定稿。

> 项目状态：开发中。需求设计、飞书结构验证、开发环境已经完成，技能与 App 改造正在进行。

## 与 xiaoman 的关系

DayMark 的 Android Agent 基础框架来自开源项目 [xiaoman](https://github.com/aduoge/xiaoman)（MIT License）。我们保留它的分层结构，替换名称与包名，并新增飞书记录能力和周期复盘技能。

原始项目说明见 [README.xiaoman.md](README.xiaoman.md)。

## 设计原则

- 每天只做两件事：开工、记录。不强制每日复盘。
- 用户只负责说事实，Agent 负责归位、核对和汇总。
- 不编造数据、成果或影响；总结中的关键事实附来源日期。
- 写入后回读确认；失败先回查再重试，避免重复。
- 原始日志不因生成总结而被修改或删除。

## 目录

| 目录 | 内容 |
|---|---|
| `app/` | Android 应用与界面 |
| `core-agent/` | 聊天编排与操作循环 |
| `core-llm/` | OpenAI 兼容模型客户端 |
| `core-memory/` | Markdown 记忆 |
| `core-skills/` | 技能解析与校验 |
| `core-sentinel/` | 操作安全策略 |
| `hands/` | 无障碍读屏与操作 |
| `skills/` | 技能目录，`daymark-*` 为自有技能 |
| `docs/` | 架构与使用文档 |

## 开发环境

- JDK 17
- Android SDK 35（platform 35、build-tools 35.0.0）
- Gradle 8.11.1（使用仓库内 wrapper）

构建：

```bash
./gradlew :app:assembleDebug
```

## 配置

复制 `.env.example` 为 `.env`，填入火山方舟的 Base URL、模型 ID 和 API Key。

`.env` 已被 `.gitignore` 排除，不要提交。仓库中不存放任何真实密钥。

## 许可证

MIT License，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
