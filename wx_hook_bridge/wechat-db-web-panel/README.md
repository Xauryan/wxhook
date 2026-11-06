# 微信本地数据库可视化

## 当前前端入口

新增「报告中心」「导出中心」「本机提醒」：年度/双人/群聊/个人足迹报告、九种格式后台批量导出、联系人导出、本地修订副本、账号绑定自动导出与黑白名单提醒。重复能力沿用本项目；功能对应和验收边界见 [WeFlow 功能参考](docs/weflow-integration.md)。自动导出和本机提醒默认关闭。

数据分析页面已支持「一键生成/更新快照」、进度、取消及账号绑定的定时更新，首次启动不再要求预先解密。详见[一键解密与自动更新](docs/analysis-snapshot-automation.md)。自动更新默认关闭。

容器入口已接入：在面板根目录运行 `.\scripts\deploy-container.ps1`，访问 `http://127.0.0.1:5174/chats`。Docker 承载完整前端与同源 API 代理，依赖 Windows 的聊天后端和微信桥接保留在宿主；部署、更新及边界见 `deploy/README.md`。

新前端位于 `frontend-next/`，使用完整 shadcn-admin 项目和黑白灰主题，首页为 `http://127.0.0.1:5174/`。Chats 连接真实本地微信数据；任务管理 `/tasks` 默认连接 `jqr.47jsy.com` 的共享任务中心，成员各自在本地后台填写 Git 邮箱参与，无需本地任务数据库。旧 `frontend/` 保留；主界面的“数据分析”已接入数据库面板功能，功能对应关系、快照生成和验收边界见 `docs/weixin-panel-integration.md`。安装与启动详见 `frontend-next/WORKSPACE.md`；下文仍保留旧面板和后端说明，主界面接入范围以融合文档为准。

本项目在本机只读加载已经解密的微信数据库，提供概览、联系人、媒体、贡献、会话、朋友圈和公众号等面板，以及微信客户端式的“会话列表”、全文搜索和设置页面。页面数据来自 FastAPI，不包含参考图中的示例联系人或硬编码统计。

## 安全边界

新版面板已加入「数据分析 → 关系分析」，入口 `/analysis/relationships`。从核验后的当前微信通讯录生成群关系图、群友交集和跨群成员明细，并导出 CSV、JSON 与离线关系图。数据口径、账号边界和接口见 [docs/relationship-analysis.md](docs/relationship-analysis.md)。

- SQLite 分析源库通过 `mode=ro&immutable=1` 和 `PRAGMA query_only=ON` 打开；朋友圈备份为兼容活跃 WAL，使用 `mode=ro` 加 `query_only`，再通过 SQLite online backup 生成一致性副本。
- 不对 `runtime/db_decrypt/all` 执行迁移、写入、`VACUUM`、checkpoint 或删除。
- 派生数据和实时事件账本只能写入 `wechat-db-web-panel/.cache/`。
- API 和 Vite 默认只监听 `127.0.0.1`。
- 联系人、消息、头像和媒体均留在本机，不接入遥测、云分析或第三方数据服务。
- 媒体打开和导出操作只读取源文件；需要生成文件时写入项目自己的输出目录。

## 环境要求

- Windows PowerShell 5.1 或 PowerShell 7
- 64 位 Python 3.11+
- Node.js 20+ 和 npm
- `dist/weixin_fts_tokenizer.dll`
- 与 tokenizer 匹配的 `Weixin.dll`，默认路径为 `D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll`
- 已解密数据库，默认路径为 `../runtime/db_decrypt/all`

不要使用 32 位 `sqlite3.exe` 加载 FTS 扩展。当前快照使用的 Python SQLite 版本为 3.45.1。

## 安装

从 `wechat-db-web-panel` 目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix frontend install
```

## 一键启动

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

脚本启动以下本地服务：

- Web：`http://127.0.0.1:5173/overview`
- API：`http://127.0.0.1:8765/api`
- 健康检查：`http://127.0.0.1:8765/api/health`

按 `Ctrl+C` 后脚本会停止两个子进程。可用参数：

```powershell
.\scripts\dev.ps1 -ApiPort 9000 -WebPort 5174
.\scripts\dev.ps1 -NoBrowser
.\scripts\dev.ps1 -PythonPath C:\Python312\python.exe
.\scripts\dev.ps1 -NoBrowser -ExitAfterSeconds 10  # 自动化启动 smoke
```

## 分别启动

API：

```powershell
$env:WECHAT_DB_ROOT = (Resolve-Path ..\runtime\db_decrypt\all)
$env:WECHAT_DECRYPTION_REPORT = (Resolve-Path ..\runtime\db_decrypt\decryption_report.json)
$env:WECHAT_FTS_EXTENSION = (Resolve-Path ..\dist\weixin_fts_tokenizer.dll)
$env:WEIXIN_DLL_PATH = 'D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll'
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765 --reload
```

Web：

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Vite 开发服务器将 `/api` 代理到本地 API。直接连接其他本地 API 时可设置 `VITE_API_BASE`。

## 朋友圈备份

打开 `http://127.0.0.1:5173/sns` 可查看 `sns/sns.db` 中的朋友圈动态，并生成本机 ZIP 备份。默认归档包含：

- 一致性 `sns.db` 快照，以及动态、评论、点赞、媒体和原始互动 JSONL。
- `manifest.json`、`schema.json`、逐文件 SHA-256 和源表行数。
- 当前账号目录中已经存在的 SNS 图片、视频和背景缓存；微信私有图片格式按原字节保留。
- 有大小上限的原生 SNS 捕获记录。单文件超过 16 MiB 或累计超过 64 MiB 时只在 manifest 标记跳过，避免归档异常膨胀。

备份不会联网下载缺失媒体。远程 URL 只作为本机私有归档元数据保存，不能视为媒体文件已备份。产物位于 `.cache/sns-backups/`，可从页面下载或删除。完整契约见 `docs/SNS_BACKUP.md`。

## 配置

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `WECHAT_DB_ROOT` | `../runtime/db_decrypt/all` | 解密数据库根目录 |
| `WECHAT_DECRYPTION_REPORT` | `../runtime/db_decrypt/decryption_report.json` | 快照、账号和完整性元数据 |
| `WECHAT_ANALYTICS_CACHE` | `.cache/analytics.sqlite` | 只保存真实派生结果的缓存 |
| `WECHAT_FTS_EXTENSION` | `../dist/weixin_fts_tokenizer.dll` | 微信 FTS5 tokenizer 扩展 |
| `WEIXIN_DLL_PATH` | `D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll` | tokenizer 对应的微信 DLL |
| `WECHAT_TIMEZONE` | `Asia/Shanghai` | 指标日期边界和显示时区 |
| `WECHAT_EPISODE_GAP_SECONDS` | `1800` | 会话 episode 切分间隔 |
| `WECHAT_RESPONSE_MAX_SECONDS` | `86400` | 响应时长最大纳入间隔 |

实时事件账本默认与 analytics cache 同目录，文件名为 `.cache/realtime.sqlite`。它保存本机 Hook 投递的原始 envelope、解析质量、幂等键和应用状态；不会写入微信源库。

## 事件级实时

面板现在使用两层数据：

1. `decryption_report.json` 对应的只读解密快照，作为完整历史基线。
2. `wx_hook_bridge.dll` 的 `DoAddMsg` Hook 事件，幂等写入 `.cache/realtime.sqlite`，再物化到 `.cache/analytics.sqlite`。

默认链路为：

```text
Weixin DoAddMsg
  -> bridge/realtime_queue/*.json
  -> POST http://127.0.0.1:8765/api/realtime/ingest
  -> .cache/realtime.sqlite
  -> .cache/analytics.sqlite
  -> GET /api/realtime/events (SSE)
  -> TanStack Query 定向刷新
```

实时端点：

- `POST /api/realtime/ingest`：仅回环请求，只接受 `application/json`，单个事件最大 256 KiB；先持久化并返回 `201 pending`，不让原生 relay 等待 analytics 聚合。
- `GET /api/realtime/status`：事件数量、重复投递、游标和物化状态。
- `GET /api/realtime/events`：SSE，使用单调 sequence 作为 `Last-Event-ID`/`after` 游标。

启动 Web 面板不会自动注入原生 Hook。需要按仓库根目录文档构建并注入 `dist/wx_hook_bridge.dll`；默认 API 端口必须为 `8765`。若使用 `scripts/dev.ps1 -ApiPort` 改端口，需要同步重配 bridge 的本地 push 地址。

事件交付为至少一次。后端在稳定的账号/数据库 scope 内，按会话分别对 `event_id` 和原生消息身份去重。后台按 ledger sequence 串行物化，只有 analytics 事务提交并标记为 `applied` 后 SSE 才可见；较早的 pending 事件形成顺序屏障，避免断线游标跳过迟到消息。服务重启会重放尚未物化的事件。完整 envelope、幂等、状态和重连契约见 `docs/REALTIME.md`。

侧栏的“已连接”仅表示浏览器已连接本机 SSE，快照时间仍单独显示，不能据此推断 Hook 正在运行或解密快照已经更新。

## 意向单通知

页面 `/intention-notifications` 消费远端 `intention_order.created` 事件，并把带录单时间的短行战报发送到一个或多个微信群。消费者默认关闭；必须先配置令牌、选择目标群、选择启动策略并保存启用，才会领取远端事件。

每次从关闭切换为开启时必须选择一种策略：

- `仅发送新单`：以本次启用时间为截止点，更早的积压事件只落盘统计并回复完成，不调用微信发送接口。
- `补发积压单`：启动后依次发送远端当前积压事件，再继续接收新事件。

交付顺序如下：

1. WSS 优先接收事件，连接失败时使用 REST 长轮询。
2. 按 `event_id` 写入 `.cache/intention_notifications.sqlite` 后回复 `RECEIVED`。
3. 按北京时间计算个人、小组、全场今日累计；事件带完整 `team_snapshot` 时，同时计算在岗破零进度。
4. 逐群保存发送结果，重投时跳过已经成功的群。
5. 所有目标群成功后回复 `SENT`；临时失败回复可重试的 `FAILED`。

累计值默认来自本机已经接收并去重的事件，不使用系统订单 ID 代替数量。完整 `team_snapshot` 会作为权威来源覆盖累计值：仅当天拨打不同号码数量大于 10 的成员算在岗，恰好 10 个不算；小组每累计 5 单发送阶段战报，其余新单发送短版快报。另在北京时间 `12:00`、`16:00`、`18:30` 仅为 4 个购机组（呼叫中心一组、二组、四组和润汇外呼小组，不含三组）各发送一条阶段战报，`21:00` 也仅为这 4 组各发送一条收官战报，组消息之间间隔约 2.5 秒。其他小组不生成或补发定时战报；每单快报和每累计 5 单的通报范围不变。定时消息和逐群发送结果会可靠落盘，幂等键包含日期、时点、外呼组和目标微信群；快照缺失或不完整时不会推断零单名单。字段契约和消息示例见 `docs/INTENTION_NOTIFICATION_FORMAT_V2.md`。

令牌可以在页面中录入，也可以在启动 API 前通过 `YSH_ROBOT_DISPATCH_TOKEN` 注入。页面只返回 `token_configured`，不会回显令牌。可选覆盖项：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YSH_ROBOT_DISPATCH_TOKEN` | 空 | 机器人消费令牌，仅在本地缓存没有令牌时写入 |
| `YSH_ROBOT_DISPATCH_REST_URL` | 生产 REST consumer 地址 | WSS 不可用时的备用地址 |
| `YSH_ROBOT_DISPATCH_WSS_URL` | 生产 WSS consumer 地址 | 实时消费地址，不包含 `consumer_id` 查询参数 |
| `YSH_ROBOT_DISPATCH_AUTOSTART` | `1` | 设为 `0` 时只加载配置和页面，不启动远端消费者；测试环境使用此项 |

本机配置接口：

- `GET /api/intention-notifications`：公开配置状态、今日统计和最近投递，不返回令牌。
- `GET /api/intention-notifications/groups`：从本地微信桥接读取微信群。
- `POST /api/intention-notifications/config`：保存开关、稳定 `consumer_id`、令牌和目标群。

## 构建和测试

```powershell
# Python 数据层和 API
python -m pytest

# React 单元测试
npm --prefix frontend run test

# 前端生产构建
npm --prefix frontend run build

# Playwright 路由与交互测试
npm --prefix frontend run test:e2e

# 按各参考图固定视口截图并生成像素 diff
npm --prefix frontend run test:visual

# 原生实时 relay 合约和编译边界
python -m unittest discover -s ..\native\wx_hook_bridge\tests -v
powershell -ExecutionPolicy Bypass -File ..\native\wx_hook_bridge\build.ps1 -SyntaxOnly

# 汇总执行可用检查
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

Playwright 会自动启动 API 和 Vite。视觉产物写入：

- `artifacts/screenshots/`
- `artifacts/diffs/`
- `artifacts/playwright/`

视觉测试固定使用 Chromium、DPR 1、浅色主题和 100% 缩放。前五页使用 `1448x1086` 并执行像素差异检查；会话列表使用 `1536x1024`，按后来确定的共享壳层要求执行三栏结构验收。`reference-images/` 始终只读。前五页默认最大差异像素比例为 0.15，可通过 `MAX_VISUAL_DIFF_RATIO` 临时收紧；`VISUAL_ROUTE=chat-list` 可只校验新增页面。

## 数据快照

当前 `decryption_report.json` 记录的开发基线为 20 个已解密数据库、195 个消息表和 48,092 条消息；`message/weclaw.db` 未解密但不影响六个核心页面。这些数值仅用于完整性核对，页面每次都从 API 查询或从带源指纹的真实派生缓存读取。

部分源 WAL 含有效提交帧，而本项目读取的是报告对应的已解密主库快照。Hook 启动后产生的新事件会作为本地增量覆盖层参与页面统计，但不会改变 `snapshot_time`。下一份外部生成的解密快照触发 source fingerprint 变化时，analytics cache 会重建并再次合并事件账本；已进入新快照的消息按稳定消息键去重。

## 文档

- `docs/ARCHITECTURE.md`：双端架构、数据流和安全边界
- `docs/DATA_SCHEMA.md`：真实数据库表、字段和关联方式
- `docs/METRICS.md`：指标定义、公式和降级规则
- `docs/MEASUREMENTS.md`：六张参考图的固定视口测量与截图流程
- `docs/MP_OFFICIAL_ACCOUNT.md`：公众号指标采集、动态凭据捕获和文章导出
- `docs/SNS_BACKUP.md`：朋友圈读取、媒体缓存、ZIP 结构和备份 API

## 已知限制

- 数据库没有可靠的“联系人添加时间”，相关趋势只能标为“首次互动联系人增长”。
- OCR 只有在本地 OCR 依赖可用且图片可访问时显示结果；否则显示真实空状态。
- 网络头像 URL 不保证可达，本地头像缺失时使用由真实显示名生成的稳定占位图。
- 资源索引存在不代表原始媒体仍在磁盘；API 会明确返回 `resource_available`。
- 朋友圈媒体 URL 可能带临时签名；备份默认保留本机已有缓存，未命中的远程媒体只保存元数据且不会自动下载。
- 无法解压或解析的消息会保留原始类型和结构化错误，不会生成替代文本。
- 原生 relay 使用 256 个预分配 slot；队列满或进程在首次落盘前被强制终止时可能出现 `dropped`。这些缺口只能由后续解密快照校准，面板不会猜测缺失消息。
- `.cache/realtime.sqlite` 含本地消息正文。删除它会清除事件账本，并可能丢失尚未进入下一份解密快照的增量消息。
