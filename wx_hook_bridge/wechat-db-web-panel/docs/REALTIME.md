# 本地实时事件契约

## 目标与边界

实时层把 `wx_hook_bridge` 捕获的新消息覆盖到只读解密快照之上。微信数据库始终使用 `mode=ro&immutable=1`；原始事件、状态和派生数据只写入项目 `.cache/`。API 只监听并接受回环访问，不上传正文、联系人或媒体。

## 接收端点

`POST /api/realtime/ingest` 是规范端点，`POST /api/bridge/messages` 为旧 bridge 兼容路径。两者要求：

- 客户端地址为 `127.0.0.1`、`::1` 或测试回环地址。
- `Content-Type` 的媒体类型必须是 `application/json`；允许 charset 参数。
- 若请求带 `Origin`，scheme 必须为 `http`/`https`，hostname 必须是 `127.0.0.1`、`::1` 或 `localhost`。
- 请求体最大 256 KiB，服务端流式计数并在超限时返回 `413`。
- body 必须是 JSON object；无效 JSON 返回 `400`，无效事件返回 `422`。

新事件先提交到 `.cache/realtime.sqlite`，随后立即返回 `201` 和 `status: "pending"`。analytics 物化由后台串行 reconciliation 完成，不占用原生 relay 的 250 ms HTTP 预算。重复事件返回 `200`、`duplicate: true` 和 `dedupe_basis`。

## Envelope

规范字段与兼容别名：

| 语义 | 字段 |
|---|---|
| 事件 | `event_id`, `event_type`, `source`, `schema_version` |
| 原生身份 | `message_id`/`msgId`, `local_id`/`localId` |
| 类型 | `message_type`/`type`, `message_subtype`/`subType` |
| 时间 | `timestamp_unix`，兼容 `timestamp` 和 ISO 时间 |
| 方向 | `direction`, `is_outgoing`/`isSendMsg`, `is_incoming`/`isIncoming` |
| 会话 | `peer_id`/`peerWxId`, `room_id`/`roomId` |
| 发送者 | `sender_id`/`senderWxId`, `self_id`/`selfWxId` |
| 原始路由 | `raw_from`/`rawFrom`/`from`, `raw_to`/`rawTo` |
| 内容 | `content`, `signature` |

也接受嵌套 `payload`，顶层同名字段覆盖嵌套字段。`event_id` 最长 200 字符，控制字符被拒绝。缺少 `event_id` 时，只有在消息身份和时间存在的情况下才基于 source、session、消息身份、时间和 signature 稳定派生。

## 幂等与隔离

scope 由账号 ID 和解密数据库根目录身份生成。`snapshot` 是校准元数据，不参与 scope，因此同一账号产生新解密快照后仍能重放旧账本并通过 `message_key` 与新基线去重。

两层唯一约束均包含 session：

1. `(scope, session, event_id)` 防止 relay 重投同一 envelope。
2. `(scope, session, identity_key)` 防止 event ID 改变后重复计数；identity 基于 source、session、`msgId` 和 `localId`。

这避免数据库中相同 `server_id/local_id` 出现在不同会话时被错误合并。

## 状态机

- `accepted`：内部 ledger 已提交、尚未开始或完成 analytics reconciliation。对外响应映射为 `pending`。
- `pending`：缺少可物化字段，或之前物化遇到可重试错误。
- `applied`：analytics 事务已提交；此时消息、聚合、搜索合并层和 SSE 均可见。
- `duplicate`：仅保留用于旧账本兼容；当前可确认 analytics 中已存在的规范化事件会标记为 `applied`，响应仍可带 `duplicate: true`。

后台使用 trailing debounce 合并 Hook 突发，再按 ledger sequence 批量物化。服务启动时和运行期间都会重试 normalized pending。重复投递 pending 事件也会唤醒 reconciliation。

## SSE 与重连

`GET /api/realtime/events` 返回 `text/event-stream`，支持：

- 查询参数 `after=<sequence>`。
- 请求头 `Last-Event-ID`；与 `after` 同时存在时取较大值。
- 测试/一次性读取参数 `once=true`。
- 15 秒 keepalive 注释帧。

只有 `status=applied` 的事件可读取。若更早的 normalized 事件仍为 `accepted/pending`，它形成发布屏障，后续 applied sequence 暂不发送；analytics commit 后按原 ledger 顺序释放。因此浏览器保存较大游标时不会跳过迟到消息。

SSE 数据包含 `sequence`、`cursor`、`event_id`、`event_type`、`source`、`status`、`quality`、原始 `payload` 和错误字段。若请求游标早于账本保留范围，服务发送 `realtime.reset`，客户端应清除局部游标并重新拉取 API read models。

## 数据质量

无法确定 session、timestamp 或 message type 时，事件保留在 ledger，`quality.state` 为 `pending`，并给出结构化 `errors`。发送者缺失等非阻塞问题进入 `warnings`。WCDB 解压或 XML 解析失败保留真实错误，不生成替代正文。

`snapshot_time` 始终表示解密报告时间，不因 Hook 到达而改变。实时 freshness 只能从 `/api/realtime/status` 的 counts、sequence 和浏览器 SSE 连接状态联合判断。

## 性能校验

在 48,092 条基线、最大真实会话 15,428 条消息的临时 analytics 副本上，100 次顺序 POST 的观测值为 p50 13.4 ms、p95 18.5 ms、p99 19.6 ms、max 21.6 ms；100 条、8 并发突发为 p50 104.1 ms、p95 199.3 ms、p99 206.2 ms、max 207.6 ms。所有请求均在 durable ledger commit 后返回，后台最终将 200 条事件全部置为 `applied`。该结果用于回归预算，不替代目标机器上的持续测量。
