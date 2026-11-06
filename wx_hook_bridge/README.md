# wx_hook_bridge

Windows 微信原生桥接：C 语言 DLL 在微信进程内完成消息收发与能力调用，Python 侧提供注入、消息规范化与文件/HTTP 桥接工具。

**运行环境**：Windows x64 · 微信 PC · MSVC · Python 3.11+

---

## 目录结构

```text
.
├── native/wx_hook_bridge/   原生 C DLL（核心能力）
├── python/
│   ├── inject/              进程查找、编译、注入
│   ├── message/             消息规范化 / 引用 / Finder 载荷
│   └── tools/               文件队列 + HTTP 消息 CLI
├── docs/                    文档（快速开始、功能清单、架构）
├── examples/                可运行示例（发文本等）
├── dist/                    编译输出目录（见 .gitignore）
├── README.md
├── LICENSE
└── .gitignore
```

目录约定见 [docs/structure.md](docs/structure.md)。

---

## 功能总览

能力分两层：

| 层级 | 位置 | 做什么 |
|------|------|--------|
| **原生 DLL** | `native/wx_hook_bridge` | 注入后在微信进程内：收消息、发消息、转发、朋友圈、群管理、红包/转账相关、防撤回等 |
| **Python 辅助** | `python/` | 编译与注入 DLL；规范化 inbox 消息；构造引用/视频号载荷；文件 outbox 与 HTTP 桥 |

详细对照表见 **[docs/features.md](docs/features.md)**。

### 1. 消息收发（原生）

| 能力 | 说明 |
|------|------|
| 接收消息 | Hook `DoAddMsg`，经本地持久队列推送面板；失败事件兼容镜像到 bridge inbox |
| 发送文本 | outbox 类型 `1` → `send_text` |
| 发送图片 | outbox 类型 `3` → `send_image` |
| 引用回复 | outbox 类型 `5` → `send_quote` |
| AppMsg / XML | outbox 类型 `14` → `send_appmsg_xml` |
| 卡片（链接/音乐等） | outbox 类型 `12` → `send_card` |
| 原生转发 | outbox 类型 `6/7/10/11`（普通 / 高入口 / probe） |
| 图片下载与解码 | 内部下载/解码及多种 probe 路径 |

### 2. 群与联系人（原生）

| 能力 | 说明 |
|------|------|
| 拉取群成员 | outbox 类型 `4` → `get_room_list` / room_members |
| 联系人显示名缓存 | 联系人 / 群成员昵称查询 |
| 修改备注 | `modify_remark` |
| 修改群内昵称 | `modify_room_nickname` |
| 修改群名 | `modify_room_name` |
| 发布群公告 | `publish_room_announcement` |
| 拉人 / 踢人 / 退群 | `add_room_member` / `del_room_member` / `leave_chatroom` |
| 创建群聊 | `/api/createChatRoom` 或 TAB 分隔的 outbox 类型 `45` → `create_chatroom`；接受 2 至 40 个有效唯一成员，成功返回包含 `@chatroom` 的 `roomid` |
| 自动扫群与联系人 | 后台 room/contact 扫描调度 |

### 3. 防撤回与消息缓存（原生）

| 能力 | 说明 |
|------|------|
| 防撤回开关 | outbox 类型 `13` → `anti_revoke` |
| 撤回事件检测 | 接收侧 sysmsg / revoke hook |
| 撤回通知桥接 | 合成 revoke notice（JSON/XML） |
| 消息环形缓存 | 供转发、引用、filehelper 匹配等使用 |

### 4. 朋友圈 SNS（原生）

| 能力 | 说明 |
|------|------|
| 拉取时间线 | `get_sns_timeline` |
| 发文字 | `publish_sns_text` |
| 发图片 / 视频 | `publish_sns_image` / `publish_sns_video` |
| 评论 / 点赞 / 删除 | `sns_comment` / `sns_like` / `sns_delete` |
| 可见性 / 私密 | `sns_set_visibility` |
| SNS 媒体下载 | URL / task / full_task 多条路径 |
| 朋友圈备份面板 | `/sns` 查看本地 `sns.db`，生成含 SQLite、JSONL、互动、媒体缓存和 SHA-256 manifest 的 ZIP |

### 5. 视频号 / H5 / 浏览器（原生）

| 能力 | 说明 |
|------|------|
| GetA8Key | 打开链接鉴权任务 |
| H5ExtTransfer | H5 扩展传输，`jsapiControlByte` 默认 `400` |
| Finder 搜索 | `/api/searchFinder`，解析账号、视频和直播结果 |
| Finder 直播间 | `/api/enterFinderLive` 打开真实直播页 |
| Finder 直播发言 | `/api/sendFinderLiveComment`，调用官方 PC 直播评论 CGI |
| Finder 主页 | `/api/getFinderUserPage`，使用当前 Web Finder 的 H5 cmd `6624` |
| 打开 URL | `open_url` + 浏览器事件 hook |

### 6. 红包 / 转账 / 语音（原生）

| 能力 | 说明 |
|------|------|
| **冷抢红包** | 默认开启；配置见 **[docs/hongbao-cold.md](docs/hongbao-cold.md)**（含 **延迟 ms**） |
| 接受 / 拒绝转账 | `accept_transfer` / `refuse_transfer` |
| 语音转文字 | `voice_transcribe` |

### 7. 桥接协议与运行时（原生）

| 能力 | 说明 |
|------|------|
| Outbox 命令分发 | 轮询 `bridge/outbox`，按类型号执行命令 |
| 结果回写 | `write_result` / `write_forward_result` / status 文件 |
| HTTP 推送到 Python | `post_json_to_python` |
| 本地实时 relay | `DoAddMsg` 真实事件通过持久队列 POST 到 `127.0.0.1:8765/api/realtime/ingest` |
| Worker 循环与生命周期 | DLL 初始化、hook 安装/卸载、worker 所有权 |
| 模块边界校验 | `verify_boundaries.py` 防止状态跨模块泄漏 |

### 8. Python 层

| 模块 | 能力 |
|------|------|
| `python/inject/` | 查找微信进程与窗口；MSVC 编译 DLL；远程 `LoadLibrary` 注入；更新锁定辅助 |
| `python/message/message_normalizer.py` | 消息类型、@、引用、卡片、媒体 XML 规范化 |
| `python/message/quote_builder.py` | 构造引用回复 appmsg XML |
| `python/message/finder_payload.py` | 解析/校验/构造视频号 Finder 载荷与 feed XML |
| `python/tools/wx_message_bridge.py` | 文件 inbox/outbox 队列、HTTP 收发、本地自测 |

CLI 子命令（`wx_message_bridge.py`）：

- `serve` / `send` / `watch` — HTTP 收发与观察
- `file-serve` / `file-send` — 文件队列收发
- `selftest` / `file-selftest` / `file-test` — 本地回环测试

---

## 工作方式（简图）

```text
  Python / 外部工具
        │  写 bridge/outbox/next.txt
        ▼
  wx_hook_bridge.dll  ◄── 注入进 Weixin.exe
        │  Hook 收消息 / 执行 outbox 命令
        ▼
  bridge/realtime_queue + inbox + result/status
        │
        ▼
  Python 规范化 / 业务逻辑
```

---

## 快速开始

完整端到端步骤（环境 → 编译 → 注入 → 发文本 → 看结果）：

**→ [docs/quick-start.md](docs/quick-start.md)**

最短命令摘要（均在仓库根目录）：

```powershell
# 1) 编译 DLL（推荐 xmake）
xmake build    # 或直接 `xmake`

# 2) 登录微信后注入（保持该窗口运行）
python python\tools\inject_bridge.py

# 3) 另开终端：发给文件传输助手
powershell -ExecutionPolicy Bypass -File examples\send_text.ps1 -To filehelper -Content "hello from wx_hook_bridge"
```

- 默认 DLL：`dist\wx_hook_bridge.dll`
- xmake 脚本详见 `native/README.md`
- 运行时目录：`bridge/`（inbox / outbox / control，已列入 `.gitignore`）
- 本地 CLI 自测（不依赖微信）：`python python\tools\wx_message_bridge.py file-selftest`

---

## 文档

| 文档 | 内容 |
|------|------|
| [docs/quick-start.md](docs/quick-start.md) | **端到端使用说明** |
| [docs/api.md](docs/api.md) | API、文件桥接协议、请求参数与响应格式 |
| [docs/features.md](docs/features.md) | 功能清单与 outbox 类型号 |
| [docs/structure.md](docs/structure.md) | 目录分层 |
| [docs/native-architecture.md](docs/native-architecture.md) | 原生模块与构建说明 |
| [docs/realtime-transport.md](docs/realtime-transport.md) | 事件 envelope、持久重试、快照校准与断线降级 |

## License

MIT — 见 [LICENSE](LICENSE)。
