# 系统架构

## 目标

面板是一个仅本机运行的只读分析应用。FastAPI 负责连接微信 SQLite 数据库、规范化消息、计算指标和提供资源流；React 负责六个参考页面、搜索、筛选、图表和导出。参考图只用于视觉回归，不参与运行时渲染。

## 目录职责

```text
wechat-db-web-panel/
|-- backend/app/          FastAPI、repository、解析器和指标服务
|-- backend/tests/        Python 数据层与 API 测试
|-- frontend/src/         React 页面、组件、查询和样式
|-- frontend/tests/       Playwright 交互与视觉测试
|-- docs/                 架构、schema、指标和测量文档
|-- scripts/              PowerShell 启动与测试入口
|-- reference-images/     只读视觉事实来源
|-- artifacts/            截图、diff 和 Playwright 报告
`-- .cache/               可删除、可重建的真实派生缓存
```

数据库和 tokenizer 位于仓库父目录：

```text
runtime/db_decrypt/all/            已解密 SQLite 主库快照
runtime/db_decrypt/decryption_report.json
dist/weixin_fts_tokenizer.dll
```

## 运行时拓扑

```text
Chromium / Browser
        |
        | HTTP 127.0.0.1
        v
React + Vite :5173
        |
        | /api proxy
        v
FastAPI + Uvicorn :8765
        |
        +--> read-only SQLite repositories
        +--> WCDB Zstandard/XML message parser
        +--> metrics and aggregation services
        +--> FTS5 connection + MMFtsTokenizer
        +--> local avatar/media resolver
        +--> read-only SNS parser + .cache/sns-backups/*.zip
        +--> .cache/analytics.sqlite (snapshot + event-derived views)
        `--> .cache/realtime.sqlite (durable event ledger)

Weixin.exe + wx_hook_bridge.dll
        |
        | DoAddMsg envelope, loopback HTTP only
        v
bridge/realtime_queue/*.json
        |
        `--> POST /api/realtime/ingest

FastAPI
        |
        `--> GET /api/realtime/events (SSE)
                  |
                  `--> React Query cache invalidation
```

两个服务默认只监听 `127.0.0.1`。前端不直接读取数据库文件，所有动态数字必须经由 API 返回。

## 后端分层

### 配置层

`backend.app.config.Settings` 从环境变量解析数据库根目录、解密报告、缓存、FTS 扩展、`Weixin.dll`、时区和指标阈值。默认路径均以仓库位置推导，不硬编码当前账号昵称。

### 数据访问层

普通数据库连接必须满足：

```text
file:///absolute/path.db?mode=ro&immutable=1
PRAGMA query_only=ON
```

连接通过 context manager 显式关闭。表名不能来自未经验证的请求参数；195 个 `Msg_*` 表只从 `sqlite_master` 和 `Name2Id` 映射中选择并使用标识符转义。

朋友圈备份是唯一需要读取潜在活跃 WAL 的例外：`sns/sns.db` 使用 `mode=ro` 和 `PRAGMA query_only=ON` 打开，然后调用 SQLite online backup 写入项目 `.cache`。结构化 JSONL 必须从该副本读取，不能再从活跃源连接读取，否则 ZIP 内数据库和 JSONL 可能不属于同一时间点。

FTS 连接还需要：

1. 设置 `WEIXIN_DLL_PATH`。
2. 在每一个新 SQLite connection 上启用扩展加载。
3. 加载 `dist/weixin_fts_tokenizer.dll`。
4. 立即关闭扩展加载能力。
5. 设置 `PRAGMA query_only=ON`。

### 规范化层

不同 `Msg_*` 表转换成统一消息模型。最低字段集合为：

| 字段 | 含义 |
|---|---|
| `session_username` | `Name2Id.user_name`，也是消息表 MD5 的输入 |
| `table_name` | 实际 `Msg_<md5>` 表 |
| `local_id` / `server_id` | 本地和服务端消息标识 |
| `local_type` | 未截断的 64 位组合类型 |
| `base_type` | `local_type & 0xffffffff` |
| `app_subtype` | `local_type >> 32` |
| `sort_seq` / `create_time` | 排序序列和 Unix 时间 |
| `real_sender_id` | 发送者 ID，结合 `Name2Id`/联系人解析 |
| `is_self` | 发送者是否为报告中的当前账号 |
| `content` | 真实解码后的文本或 XML 摘要 |
| `decoded` | 解压和字符解码是否成功 |
| `decode_error` | 可机器读取的失败原因 |
| `reference` | `<refermsg>` 结构和回连状态 |
| `resource_available` | 本地资源是否实际存在 |

规范化过程保留原始标识和错误状态，不能用示例内容填补缺失值。

### 解析层

- `message_content` 或其他压缩字段以 `28 B5 2F FD` 开头时使用 Zstandard 解压。
- 文本按真实编码结果解码；失败时返回 `decoded=false` 和错误类型。
- 群消息的 `sender_username:\n` 前缀解析为实际发送者，再从联系人或 `Name2Id` 获取显示名。
- `base_type=49` 的 app message 使用安全 XML 解析，提取文件、链接、图片、视频和引用等结构。
- `app_subtype=57` 解析 `<refermsg>` 的 `svrid/fromusr/chatusr/displayname/content`，优先按 `server_id` 回连原消息。

### 服务层

服务按页面输出已经聚合的视图模型，同时包含数据质量元数据：

- `decoded`
- `resource_available`
- `metric_basis`
- `snapshot_time`
- `snapshot_status`

主要 API 契约：

| 路径 | 用途 |
|---|---|
| `GET /api/health` | 路径、快照和服务健康状态 |
| `GET /api/profile` | 当前账号资料与头像 |
| `GET /api/overview` | KPI、趋势、类型、排行和热力图 |
| `GET /api/contacts` | 联系人指标、排行和表格 |
| `GET /api/contacts/graph` | 关系网络节点与边 |
| `GET /api/chat-list` | 按最新真实消息排序的会话列表和类型计数 |
| `GET /api/chat-list/{username}` | 会话消息、资料、文件、年度热力图和当日统计 |
| `GET /api/media` | 媒体统计、文件、链接、关键词和 OCR |
| `GET /api/sns` / `GET /api/sns/{sns_id}` | 朋友圈分页列表与按需原始详情 |
| `GET/POST /api/sns/backups` | 列举或生成朋友圈 ZIP 归档 |
| `GET/DELETE /api/sns/backups/{backup_id}` | 查询或删除本机归档 |
| `GET /api/sns/backups/{backup_id}/download` | 下载归档 ZIP |
| `GET /api/contributions` | 年度热力图、日期统计和引用 |
| `GET /api/conversations` | episode 时间线、筛选和导出数据 |
| `GET /api/search` | 消息、联系人和收藏 FTS 搜索 |
| `POST /api/realtime/ingest` | 接收本机 bridge 事件并幂等物化 |
| `GET /api/realtime/status` | 事件账本、重复投递、游标和物化状态 |
| `GET /api/realtime/events` | 支持断点续传的本机 SSE 事件流 |
| `GET /api/avatars/{username}` | 本地头像或稳定占位头像 |
| `GET /api/resources/{kind}/{rowid}` | 只读本地媒体资源 |

列表端点必须有显式 limit/offset 或 cursor 上限，避免把所有消息一次性传到浏览器。

## 前端分层

- React Router 提供 `/overview`、`/contacts`、`/media`、`/sns`、`/mp`、`/contributions`、`/conversations`、`/chat-list`、`/search` 和 `/settings`。
- TanStack Query 管理 API 请求、加载、错误、重试和缓存失效。
- ECharts 和 `echarts-wordcloud` 渲染图表；图表旁提供文本摘要。
- 长消息和会话列表使用虚拟化，日期标题保留吸顶行为。
- Lucide React 提供同一线宽的 SVG 图标，不使用 emoji 作为结构图标。
- CSS 变量保存共享视觉 token，并允许路由级覆盖侧栏宽度、主区起点和卡片间距。

页面必须处理 `loading`、`error`、`empty`、`partial` 四种数据状态。资源不存在、OCR 未运行或指标无可靠来源时显示具体空状态。

## 缓存和失效

`.cache/analytics.sqlite` 只能保存从源库和本机真实 Hook 事件派生的数据。快照缓存键至少包含：

```text
absolute_path + file_size + mtime_ns + report_sha256_or_snapshot_id
```

任一源指纹变化时，该源对应派生结果失效。缓存可以删除并由应用重建，不能成为联系人、消息或统计的唯一来源。首次扫描可以把 195 张消息表规范化到缓存，API 请求不应重复执行完整 UNION。

`.cache/realtime.sqlite` 是独立的事件账本：

- scope 由账号和解密数据库根目录身份稳定生成；快照时间单独记录，不参与 scope，因此 analytics 因新快照重建后仍会合并原事件账本。
- `(scope, session, event_id)` 唯一，重复投递返回原 sequence；同一原生 ID 出现在不同会话时不会误去重。
- `(scope, session, msgId/localId identity)` 提供第二层去重，防止 relay 更换 event id 后重复计数。
- 原始 envelope、规范化消息、解析质量和 `accepted/pending/applied/duplicate` 状态均持久化。
- POST 只等待 realtime ledger durable commit，并对新事件返回 `201 pending`；后台将突发事件合并成批次，避免超过原生 relay 的 250 ms 请求预算。
- 成功解析的事件在 analytics 事务内插入 `messages` 并刷新派生聚合；事务提交后才标记 `applied` 并进入 SSE 可见集合。
- 进程启动时执行 reconciliation，把账本中已规范化但尚未进入 analytics 的事件重新物化。
- SSE 只读取 `applied`，且不会越过更早的可物化 pending sequence；因此迟到应用不会被 `Last-Event-ID` 跳过。
- source fingerprint 变化导致 analytics 重建时，会重新合并事件账本；已经进入新快照的消息由稳定 `message_key` 忽略。

静态 `message_fts.db` 不包含 Hook 后到达的事件，因此搜索把只读 FTS 命中与 analytics cache 的正文 LIKE 命中合并，并按消息 ID 去重。

`.cache/sns-backups/` 保存用户主动生成的朋友圈归档，不属于可自动重建的 analytics cache。每份归档以 `backup_id.zip` 和同名元数据文件组成；ZIP 以不覆盖既有文件的原子方式发布，读取和删除只接受经过 ID 正则、符号链接拒绝和 `resolve()` 边界校验的路径。

当前资源链路先按 `message_svr_id`，缺失时按 `(chat_id, message_local_id)` 将 `MessageResourceInfo/Detail` 回连到规范化消息。当前快照 13,016 条 detail 中有 12,969 条成功关联。protobuf length-delimited 字段只做本地 UTF-8 元数据提取，硬链接候选再按真实文件大小 best-effort 匹配。API 分别返回 `resource_available` 和 `browser_supported`，不能把“有索引”“磁盘存在”和“浏览器可预览”混成同一状态。

## 快照语义

`decryption_report.json.created_utc` 是历史基线的快照时间。报告中的部分原始 WAL 含有效提交帧，因此 UI 不能把数据状态描述为无条件“实时”。Hook 事件只形成快照之上的本地增量覆盖层，不会改写 `snapshot_time`。状态卡至少区分：

- 报告时间
- 主库快照/可能未合并 WAL
- `quick_check` 和 `integrity_check`
- 未解密文件列表
- 浏览器到 FastAPI 的 SSE 连接状态
- 事件账本最新 sequence、pending/duplicate 数量

当前应用不会自行解密正在运行的微信数据库，也不会合并微信 WAL。校准发生在外部解密流程产生新快照之后：source fingerprint 变化触发 analytics 重建，再与事件账本去重合并。relay 的 `dropped` 或首次持久化前崩溃窗口只能通过该后续快照补齐。

## 隐私与资源访问

- API 不提供任意文件路径读取；资源路由只接受类型和数据库 rowid，再由服务端解析白名单路径。
- realtime ingest 只接受回环客户端和 `application/json`，拒绝非本地 `Origin`，流式读取请求并在 256 KiB 处中止。
- 解析后的路径必须 `resolve()`，并验证位于报告的账号根目录或允许的缓存/导出目录内。
- 响应中不暴露数据库密钥、`keys.json` 内容或任意绝对路径。
- 导出内容来自当前筛选，默认写到浏览器下载或项目导出目录，不回写微信目录。
- SNS API 仅接受回环请求；备份下载和删除不接受任意路径，只接受服务生成的 backup ID。
- 本机 SNS 缓存按报告中的账号根目录枚举并复制到归档。远程 URL 不由服务端请求，原始 XML 和临时签名只存在于本机私有 ZIP。
- 不发送网络头像请求作为数据分析前提。本地头像优先，URL 仅作为不可用时的元数据。

## 性能边界

当前快照约 48,092 条消息，适合在首次启动时完成一次扫描。目标约束：

- 聚合 API 使用派生索引或单次扫描结果，不在每次请求 UNION 195 张表。
- API 分页并限制排序字段白名单。
- 图表只传聚合点，消息正文按需加载。
- 头像和缩略图设置合理的本地缓存头，但不写回源数据库。
- 50 条以上的前端时间线启用虚拟化。
- SSE 使用单调 sequence 作为游标，浏览器通过 `Last-Event-ID` 或 `after` 恢复；断点超出保留范围时发送 `realtime.reset`。

## 可访问性与视觉回归

- 键盘焦点顺序与视觉顺序一致，图标按钮有 `aria-label`。
- 图表不能只依靠颜色表达含义，并提供可读摘要。
- 尊重 `prefers-reduced-motion`。
- 视觉回归使用每张参考图的原始尺寸、DPR 1 和浅色 Chromium：前五页为 `1448x1086`，会话列表为 `1536x1024`，参考 `docs/MEASUREMENTS.md`。
