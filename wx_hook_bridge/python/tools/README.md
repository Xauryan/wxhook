# python/tools/ — 命令行工具

| 文件 | 作用 |
|------|------|
| `inject_bridge.py` | 编译（按需）并注入 DLL，保持 worker |
| `wx_message_bridge.py` | 文件 inbox/outbox + HTTP 发送桥 |
| `watch_chat.py` | 实时打印面板账本中收到的原生 Hook 聊天消息，默认只显示启动后的新来信 |
| `wechat_db_decrypt.py` | 从微信进程提取 SQLCipher 4 派生密钥并解密数据库副本 |
| `wechat_fts_query.py` | 加载微信原生 `MMFtsTokenizer` 并查询解密后的 FTS5 库 |

```powershell
# 在仓库根目录
python python\tools\inject_bridge.py --list
python python\tools\inject_bridge.py
python python\tools\wx_message_bridge.py --help
python python\tools\wechat_db_decrypt.py extract --pid <微信主进程PID>
python python\tools\wechat_db_decrypt.py decrypt C:\path\to\media_0.db
powershell -ExecutionPolicy Bypass -File native\weixin_fts_tokenizer\build.ps1
python python\tools\wechat_fts_query.py validate
python python\tools\wechat_fts_query.py search message 微信 --limit 20
```

数据库原件始终只读；目录模式会处理 `.db` 和 `.kvdb`，默认输出到
`runtime/db_decrypt/`。若 WAL 中存在有效提交帧，
工具会拒绝生成可能过期的主库快照；先正常关闭微信完成 checkpoint，或明确使用
`--ignore-wal` 仅解密主数据库。

## 实时打印聊天消息

保持微信已登录，`inject_bridge.py` 与面板后端已启动。在 `G:\project\code\jqr` 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\watch-chat.ps1
# 包含账本里已有的原生 Hook 消息
powershell -ExecutionPolicy Bypass -File .\watch-chat.ps1 -FromStart
# 也显示 Hook 捕获的发出消息；监听 30 秒后退出
powershell -ExecutionPolicy Bypass -File .\watch-chat.ps1 -IncludeOutgoing -Duration 30
# 调试时显示 Hook 原始正文/XML
powershell -ExecutionPolicy Bypass -File .\watch-chat.ps1 -RawContent
```

也可直接运行 `uv run --python 3.11 python\tools\watch_chat.py`（在 `wx_hook_bridge` 目录）。
脚本使用 Python 标准库，只读 `.cache/live_chat.sqlite` 的 `live_messages` 事件账本，
打印时间、会话、发送者、账号、消息类型、消息 ID 和正文。多行文本保持换行，
视频、语音、图片、表情、名片、位置、链接、文件和引用回复会解析 XML，
显示可读摘要和已有字段（例如视频时长、文件大小、链接、引用正文）。
视频的缩略图尺寸单独标注，不作为视频分辨率。文本消息保留原文；未知 XML 保留原文，
无法解析的 XML 会标记解析失败并保留原文。不会下载、解密媒体或将语音转写。
`-RawContent`（Python 参数 `--raw-content`）可显示原始正文/XML。按 `Ctrl+C` 停止。
`--from-start` 只回放已记录的 Hook 事件，不加载解密历史聊天。
脚本只打印原生 Hook 已交付的事件；当前未被 Hook 捕获的手机自身发送同步消息不会自动补入。

端到端步骤见 [docs/quick-start.md](../../docs/quick-start.md)。
