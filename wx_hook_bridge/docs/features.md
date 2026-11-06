# 功能清单

本文按**用户可感知能力**整理，并与源码模块 / outbox 类型号对齐。  
实现入口：`native/wx_hook_bridge/src/modules/outbox_dispatch.c`（命令分发）及各域 `src/modules/*.c`。

---

## 一、消息收发

| 功能 | Outbox 类型 | 命令名 / 接口 | 主要模块 |
|------|-------------|---------------|----------|
| 发送文本 | `1` | `send_text` | `send_api.c` |
| 发送图片 | `3` | `send_image` | `send_api.c` |
| 引用回复 | `5` | `send_quote` / `send_quote_v2` | `send_api.c` |
| 发送 AppMsg XML | `14` | `send_appmsg_xml` | `card_send.c` |
| 发送卡片（链接/音乐等） | `12` | `send_card` | `card_send.c` |
| 原生转发 | `6` | `forward_text` | `forward_*.c` |
| 转发 probe | `7` | `forward_probe` | `forward_*.c` |
| 高入口转发 | `10` | `forward_high_entry` | `forward_*.c` |
| 高入口转发 probe | `11` | `forward_high_probe` | `forward_*.c` |
| 接收消息 | （Hook） | receive pipeline → inbox | `receive_hooks.c` |
| 异步队列 Hook | （Hook） | async queue | `receive_hooks.c` |

### 图片媒体

| 功能 | Outbox 类型 | 命令名 | 主要模块 |
|------|-------------|--------|----------|
| 内部下载图片 | `8` | `download_image_internal` | `media_ops.c` |
| 内部解码图片 | `9` | `decode_image_internal` | `media_ops.c` |
| 微信侧下载 probe | `27` | `download_image_wechat_probe` | `media_ops.c` |
| 任务式下载 probe | `29` | `download_image_task_probe` | `media_ops.c` |

---

## 二、群与联系人

| 功能 | Outbox 类型 | 命令名 | 主要模块 |
|------|-------------|--------|----------|
| 拉取群成员列表 | `4` | `room_members` / `get_room_list` | `room_contacts.c` |
| 修改好友备注 | `18` | `modify_remark` | `a8key_task.c` |
| 修改群内昵称 | `19` | `modify_room_nickname` | `a8key_task.c` |
| 修改群名称 | `20` | `modify_room_name` | `a8key_task.c` |
| 发布群公告 | `21` | `publish_room_announcement` | `a8key_task.c` |
| 添加群成员 | `22` | `add_room_member` | `chatroom_member.c` |
| 删除群成员 | `23` | `del_room_member` | `chatroom_member.c` |
| 退出群聊 | `24` | `leave_chatroom` | `chatroom_member.c` |
| 创建群聊 | `45` | `create_chatroom` / `/api/createChatRoom` | `chatroom_member.c` |
| 联系人/群昵称查询 | （API） | display-name lookup | `contact_lookup.c` |
| 自动扫群/联系人 | （后台） | auto room/contact scan | `room_contacts.c` |

创建群聊支持以 JSON 数组或逗号分隔字符串传入 `memberIds`，群名称兼容 `name`、`topic` 和 `chatRoomName`。有效唯一成员须为 2 至 40 个，单个 ID 最长 255 字节；空项或包含 `@chatroom` 的 ID 会被拒绝。原生链路使用 CGI 类型 `119` 和 `/cgi-bin/micromsg-bin/createchatroom`，成功结果的 `roomid` 必须包含 `@chatroom`。

---

## 三、防撤回与缓存

| 功能 | Outbox 类型 | 命令名 | 主要模块 |
|------|-------------|--------|----------|
| 开关防撤回 | `13` | `anti_revoke` | `anti_revoke.c` |
| 撤回入口/分发/真实 Hook | （Hook） | revoke hooks | `revoke_hooks.c` |
| 撤回 XML 解析与分类 | （内部） | revoke protocol | `revoke_protocol.c` |
| 撤回通知事件 | （桥接） | synthetic revoke notice | `revoke_notice_event.c` |
| 消息缓存 / 待办引用 / filehelper 匹配 | （内部） | revoke/forward cache | `revoke_forward_cache.c` |

---

## 四、朋友圈（SNS）

| 功能 | Outbox 类型 | 命令名 | 主要模块 |
|------|-------------|--------|----------|
| 拉取时间线 | `26` | `get_sns_timeline` | `sns_timeline.c` |
| 发文字动态 | `28` | `publish_sns_text` | `sns_publish.c` |
| 发图动态 | `32` | `publish_sns_image` | `sns_publish.c` |
| 发视频动态 | `34` | `publish_sns_video` | `sns_publish.c` |
| 评论 | `35` | `sns_comment` | `sns_comment.c` |
| 点赞 | `36` | `sns_like` | `sns_comment.c` |
| 删除动态 | `39` | `sns_delete` | `sns_comment.c` |
| 设置可见性/私密 | `40` | `sns_set_visibility` | `sns_comment.c` |
| 下载 SNS 媒体 URL | `30` | `download_sns_media_url` | `sns_*.c` / media |
| 下载 SNS 媒体任务 | `31` | `download_sns_media_task` | 同上 |
| 下载 SNS 媒体完整任务 | `33` | `download_sns_media_full_task` | 同上 |
| 本地朋友圈查看与 ZIP 备份 | Web API | `/api/sns`、`/api/sns/backups` | `wechat-db-web-panel/backend/app/sns_backup.py` |

---

## 五、视频号 / H5 / 浏览器

| 功能 | Outbox 类型 | 命令名 | 主要模块 |
|------|-------------|--------|----------|
| GetA8Key | `15` | `get_a8key` | `a8key_task.c` |
| H5ExtTransfer | `16` | `h5ext_transfer` | `h5ext_transfer.c` |
| Finder 用户页（旧 model outbox） | `17` | `finder_userpage_model` | `a8key_task.c` |
| Finder 用户页（当前 H5 cmd 6624） | HTTP | `finder_userpage_h5` | `finder_ops.c` / `h5ext_transfer.c` |
| Finder 用户页 raw 捕获 | （Hook/API） | finder userpage capture | `a8key_task.c` |
| Finder 搜索 | `42` | `finder_search` | `finder_ops.c` |
| 进入 Finder 直播间 | `43` | `finder_live_enter` | `finder_ops.c` |
| Finder 直播发言 | `44` | `finder_live_comment` | `finder_ops.c` / `h5ext_transfer.c` |
| 打开 URL | `25` | `open_url` | `open_url.c` |
| 浏览器打开事件 | （Hook） | browser event hook | `browser_events.c` |

通用 H5ExtTransfer 的 `jsapiControlByte` 默认 `400`。request `+0xA8` 由 manager 作为 `pub_key_token` 覆盖，`+0xC8` 是动态路由的 `cgi_id`；两者都不是调用方的 H5 auth/version 参数。

Finder 搜索同时返回 `transportOk` 和 `businessOk`，仅当 H5 传输、外层 `jsapi_resp.ret` 与内层 `BaseResponse.Ret` 均成功时总状态才成功。直播卡片可能使用 `export/...` encrypted ID；解析器保留该值并生成 `eid=...` 直播 URL，数字 `oid/nid` 则使用 uint64 大端 Base64URL 编码。

直播发言先按需调用 `pc_findergetcommentdetail`（`5259`）把 export token/基础 nonce 解析为数字对象 ID 和 Join nonce，再执行 `pc_finderinit`（`5831`）→ `pc_finderjoinlive`（`4233`）→ `pc_finderpostlivemsg`（`4145`）。Init 同时提取当前账号 username 和 role：role `3` 的 Join/Post 使用 `myacct[0].username`；role `1` 且 `myacct=[]` 时省略 `finder_username`，默认消息 ID 使用 `pc_undefined_<唯一后缀>`，不能用 `wx_username_for_h5` 代替。HTTP 调用优先；文件 outbox 中评论正文和 cookie 不能包含制表符。

---

## 六、红包 / 转账 / 语音

| 功能 | 触发方式 | 说明 | 主要模块 |
|------|----------|------|----------|
| 红包接收路由与分类 | Hook | task-factory / route / classifier | `hongbao_receive_route.c` 等 |
| 红包运行时状态 | 内部 | 收包快照、延迟打开、上下文 | `hongbao_runtime.c` |
| 红包状态序列化 | 内部 | 状态写出 / 候选日志 | `hongbao_status.c` |
| 红包 trace | 内部 | JSON 字段、URL/query、向量探测 | `hongbao_trace.c` |
| 红包控制轮询 | 控制位 | auto / one-shot / parser | `hongbao_control.c` |
| **冷抢（cold）** | **默认且唯一** | 对齐 `frida_cold_hongbao_auto.py`；**延迟等配置见 [hongbao-cold.md](hongbao-cold.md)** | `hongbao_cold.c` |
| 免点击（noclick） | **已禁用** | 历史实验，代码保留不安装 | `hongbao_noclick.c` |
| closed-loop / UI auto-open | **已禁用** | 历史 page_ctx / route 探针，不安装 | `hongbao_closed_loop.c` / `hongbao_control.c` |
| Sentinel / route probe | **已禁用** | 不安装 | `hongbao_sentinels.c` 等 |
| 接受转账 | `37` | `accept_transfer` | `pay_transfer.c` |
| 拒绝转账 | `38` | `refuse_transfer` | `pay_transfer.c` |
| 语音转文字 | `41` | `voice_transcribe` | `voice_transcribe.c` |
| 语音 probe | 内部 | voice probe | `voice_probe.c` |

控制开关（协议层可读）：`hb_auto`、`hb_auto_one_shot`、`hb_noclick`、`hb_noclick_one_shot`、`hb_noclick_direct`（见 `wx_bridge_protocol.h`）。

冷抢默认开启。完整说明（含 **延迟配置**、开关、日志）见：

→ **[hongbao-cold.md](hongbao-cold.md)**（推荐阅读）

摘要：

| 文件（`bridge/control/`） | 作用 |
|---------------------------|------|
| `hb_cold.disable` | 关冷抢 |
| `hb_cold_open.disable` | 只 1581，不开 1685 |
| `hb_cold_delay.json` | 延迟（值一律字符串）：`{"delay_ms":"200"}` 固定，或 `{"delay_ms":"10-200"}` 区间；**无文件=0 不延迟** |

日志：`bridge/control/hb_open_debug.txt`。

---

## 七、桥接协议与进程运行时

| 功能 | 说明 | 主要模块 |
|------|------|----------|
| Outbox 轮询分发 | 解析 `bridge/outbox` 命令行并分发 | `outbox_dispatch.c` |
| 结果与状态文件 | `write_result` / `write_forward_result` / `write_status` | `protocol.c` |
| 推送到 Python | HTTP JSON → Python 侧 | `protocol.c` |
| 本地实时 relay | `DoAddMsg` 事件预分配入队、原子持久化、回环 HTTP 重试 | `receive_hooks.c` / `realtime_relay.c` |
| 文件协议 IO | 目录创建、读写 bridge 文件 | `file_io.c` |
| Worker 循环 | 所有权 token、清理 outbox | `worker_control.c` |
| 进程/模块/self-wxid | 运行时上下文 | `state.c` / `wx_bridge_runtime.h` |
| 安全内存读写 | SEH 保护读写、近代码分配 | `memory.c` |
| 微信字符串读写 | std-string / wx 字符串 | `wechat_string.c` |
| 生命周期与导出 | `DllMain`、hook 安装/卸载编排 | `lifecycle_exports.c` |
| 诊断日志 | quote / forward / hongbao debug 日志 | `debug_log.c` |
| 模块边界检查 | 构建前静态约束 | `verify_boundaries.py` |

---

## 八、Python 层功能

### `python/inject/` — 注入与进程

| 功能 | 文件 |
|------|------|
| 枚举微信进程 / 主窗口 | `wechat_native.py` |
| 选择注入目标 | `wechat_native.py` |
| 远程内存读 / pattern scan | `wechat_native.py` |
| 扫描自身资料 / 联系人 | `wechat_native.py` |
| 调用 MSVC 编译 DLL | `wechat_inject.py` |
| `LoadLibrary` 远程注入 | `wechat_inject.py` |
| 更新锁定（可选） | `wechat_update_lock.py` |

### `python/message/` — 消息与载荷

| 功能 | 文件 |
|------|------|
| 消息规范化（类型、@、摘要） | `message_normalizer.py` |
| 引用 / 卡片 / 媒体 XML 解析 | `message_normalizer.py` |
| 构造引用回复 appmsg | `quote_builder.py` |
| 解析/校验 Finder 载荷 | `finder_payload.py` |
| 构造 Finder feed XML | `finder_payload.py` |
| 从 URL/搜索/文本解析视频号 | `finder_payload.py` |

### `python/tools/wx_message_bridge.py` — CLI

| 子命令 | 功能 |
|--------|------|
| `serve` | 本地 HTTP 收消息服务 |
| `send` | 经 HTTP 发文本 |
| `watch` | 打印 inbox 日志 |
| `selftest` | HTTP mock 全链路自测 |
| `file-serve` | 监视 `bridge/inbox` 文件 |
| `file-send` | 写入 `bridge/outbox` 发送（文本/图片类型） |
| `file-selftest` | 临时目录文件队列自测 |
| `file-test` | 使用真实 `bridge/` 回环测试 |

---

## 九、Outbox 类型号速查

| 类型 | 能力 |
|------|------|
| 1 | 发文本 |
| 3 | 发图片 |
| 4 | 群成员列表 |
| 5 | 引用回复 |
| 6 / 7 | 转发 / 转发 probe |
| 8 / 9 | 图片内部下载 / 解码 |
| 10 / 11 | 高入口转发 / probe |
| 12 | 发卡片 |
| 13 | 防撤回开关 |
| 14 | AppMsg XML |
| 15 | GetA8Key |
| 16 | H5ExtTransfer |
| 17 | Finder 用户页旧 model（HTTP 默认不走此路径） |
| 18–24 | 备注 / 群昵称 / 群名 / 公告 / 拉人 / 踢人 / 退群 |
| 25 | 打开 URL |
| 26 | 朋友圈时间线 |
| 27 | 图片下载 probe |
| 28 | 发朋友圈文字 |
| 29 | 图片任务下载 probe |
| 30–33 | SNS 媒体下载（URL / task / full）+ 发图朋友圈相关 |
| 32 / 34 | 发朋友圈图 / 视频 |
| 35 / 36 / 39 / 40 | 评论 / 点赞 / 删除 / 可见性 |
| 37 / 38 | 接受 / 拒绝转账 |
| 41 | 语音转文字 |
| 42 | Finder 搜索 |
| 43 | 进入 Finder 直播间 |
| 44 | Finder 直播发言 |
| 45 | 创建群聊 |

> 类型号以 `outbox_dispatch.c` 中 `str_eq(parts[1], "…")` 分支为准；未列出的编号表示当前未实现。

Finder outbox 字段顺序：

```text
42: seq, 42, query, offset, scene, businessType, timeout, subType, currentPage, sessionId, requestId
43: seq, 43, objectId, nonceId, contextId, entranceId, subEntranceId, muted, url
44: seq, 44, content, liveId, objectId, objectNonceId, liveCookies, identityUsername,
    clientMsgId, contextId, entranceId, subEntranceId, h5Url, userverForH5,
    liveIdentity, timeout
```

创建群聊 outbox 使用 TAB 分隔，下式中的 `\t` 表示一个制表符：

```text
seq\t45\tmemberIds\ttopic\twait_ms
```
