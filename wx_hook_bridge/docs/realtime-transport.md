# 本地实时消息传输

`wx_hook_bridge.dll` 将微信真实消息事件发送给本机 Web 面板。后端把事件写入项目内事件账本，并作为解密快照之上的增量覆盖层参与查询；该链路不改变微信数据库只读约束。

## 事件来源

原生侧 Hook `DoAddMsg` 的消息模型写入路径。该路径同时观察：

- 收到并进入微信消息模型的消息。
- 已实际发送并回写消息模型的消息。

因此桥不会在发送请求发起时额外生成一条推测事件。发送消息只有在微信真实回写消息模型后才产生 `outgoing` 事件，避免发送失败或重试造成假事件。

## 本地端点

默认目标固定为：

```text
POST http://127.0.0.1:8765/api/realtime/ingest
Content-Type: application/json
```

HTTP 配置只接受 `127.0.0.1` 或 `::1`，TCP 配置只接受 `127.0.0.1`。非回环地址会被拒绝。联系人、消息正文、签名和账号标识不得通过该传输离开本机。

WinHTTP 的默认解析、连接、发送和接收超时均为 250 ms。连续失败会触发短路保护，relay 还会在后台执行 1 秒到 30 秒的指数退避。

## 消息 Envelope

`event_id` 是后端幂等键。消息事件使用服务器消息 ID 构造：

```text
wechat-message:<msgId>
```

示例结构：

```json
{
  "event_id": "wechat-message:123456789",
  "event_type": "message",
  "source": "wx_hook_bridge",
  "schema_version": 1,
  "message_id": "123456789",
  "local_id": "42",
  "message_type": 1,
  "message_subtype": 0,
  "timestamp_unix": 1760000000,
  "direction": "incoming",
  "is_incoming": true,
  "is_outgoing": false,
  "peer_id": "wxid_peer",
  "room_id": "",
  "sender_id": "wxid_peer",
  "self_id": "wxid_self",
  "raw_from": "wxid_peer",
  "raw_to": "wxid_self",
  "content": "真实消息内容",
  "signature": ""
}
```

Envelope 同时保留 `msgId`、`localId`、`type`、`subType`、`isSendMsg`、`peerWxId` 等旧字段别名。接收端可以忽略未知字段。首次应用返回 `201`，重复 `event_id` 返回 `200` 且 `duplicate=true`。

后端接收体最大 256 KiB，并在流式读取阶段中止超限请求。除 `event_id` 外，后端还按 bridge 消息身份执行第二层去重。

## Hook 线程边界

`process_message` 只完成 JSON 构造，并将事件复制进 256 个预分配 slot 组成的无锁队列。relay 会把 SLIST 的 LIFO 批次反转后按 Hook 入队顺序处理。Hook 线程不会执行该实时链路的 HTTP、磁盘写入、重试、等待或同步降级。

队列满、relay 未运行或单条 JSON 超限时，事件会被计入 `dropped`，不会退回 Hook 线程同步发送。运行状态写入：

```text
bridge/control/realtime_relay_status.json
```

状态包含 `running`、`enqueued`、`persisted`、`delivered`、`failed`、`dropped`、`retried`、`pending_memory` 和 `pending_disk`。

## 持久队列与重试

relay 专用线程先把事件写入：

```text
bridge/realtime_queue/<sanitized-event-id>.json
```

写入流程为临时文件、`FlushFileBuffers`、`MoveFileExW` 原子替换。只有完成持久化后才尝试 HTTP。发送失败时文件保留，后台按退避策略重新扫描；进程重新启动后也会继续处理已有文件。成功返回任意 `2xx` 后删除队列文件。

持久化后的交付语义是至少一次。网络成功但队列文件删除失败时可能再次投递，后端必须依靠稳定 `event_id` 去重。

Hook 入队到 relay 完成首次落盘之间存在一个很短的内存窗口。进程在该窗口内被强制终止时，尚未持久化的 slot 无法恢复。正常停止会先卸载接收 Hook，再让 relay 将内存队列落盘。`dropped` 和 `pending_memory` 用于审计该边界。

## 事件与快照校准

实时消息事件不等同于已解密数据库快照已经更新。面板以只读快照作为历史基线，把成功解析的事件持久化到：

```text
wechat-db-web-panel/.cache/realtime.sqlite
```

随后按稳定消息键物化到 `.cache/analytics.sqlite`，刷新消息、日、小时、session 和 episode 派生视图。`snapshot_time` 仍只表示解密报告时间，不会因事件到达而改变。

- `message` 事件表示微信消息模型出现了真实消息；后端规范化、幂等物化后再通过 SSE 通知前端刷新。
- 页面显示的统计由“只读快照 + 已物化事件”的真实消息集合重新计算，不直接拿 SSE 数量累加。
- 静态 `message_fts.db` 不包含新事件，后端会把 FTS 命中与 analytics 正文命中合并去重。
- UI 的“实时”状态必须同时区分事件连接状态和当前 `snapshot_time`，不能把旧快照标成实时数据。

后端接口：

```text
POST /api/realtime/ingest
GET  /api/realtime/status
GET  /api/realtime/events
```

SSE 使用后端单调 sequence 作为 `id`。浏览器按 `Last-Event-ID` 或 `after=<sequence>` 恢复；游标不可恢复时收到 `realtime.reset` 并重新查询活动页面。

## 断线降级

后端暂时不可用时，原生 relay 保留磁盘队列并退避重试，不阻塞微信线程。面板 SSE 断开时继续显示最后一次成功读取的快照，并明确显示断开状态，不生成示例数据或推测统计。

恢复后执行以下收敛流程：

1. relay 重放磁盘队列。
2. 后端按 `event_id` 和消息身份去重，恢复 pending 事件并物化到 analytics cache。
3. 后端通过 SSE sequence 发布事件，前端使对应查询失效并重新读取 API。
4. 外部解密流程以后产生新快照时，source fingerprint 触发 analytics 重建，再与事件账本按稳定消息键去重合并。

当前 Web 面板不会自行解密正在运行的微信数据库，也不会主动合并微信 WAL。relay 队列溢出或首次持久化前的强制崩溃窗口，只能由后续解密快照校准；系统不会猜测或补造消息。

## 验证

```powershell
python native\wx_hook_bridge\verify_boundaries.py
python -m unittest discover -s native\wx_hook_bridge\tests -v
powershell -ExecutionPolicy Bypass -File native\wx_hook_bridge\build.ps1 -SyntaxOnly
```

测试会检查回环限制、固定端点、稳定 `event_id`、Hook 线程无同步传输降级、原子队列、persist-before-send 顺序、生命周期和构建清单。
