# 数据模型与来源

## 快照根目录

默认数据库根目录为 `runtime/db_decrypt/all`。账号 ID 不从代码或 UI 常量读取，而是来自 `runtime/db_decrypt/decryption_report.json.account`，再使用该 ID 查询联系人资料。

当前报告的完整性基线：

| 项目 | 报告值 |
|---|---:|
| 已解密数据库 | 20 |
| 未解密数据库 | `message/weclaw.db` |
| 消息表 | 195 |
| 消息行 | 48,092 |
| 联系人 | 2,508 |
| 会话摘要 | 289 |
| 群聊 | 46 |
| 资源索引 | 5,402 |
| 本地头像 | 1,198 |

这些数字只用于开发和测试的快照核对。运行时 API 必须查询真实数据或验证过源指纹的派生缓存，不能把表格中的数字当成页面常量。

## 连接约束

所有源库连接使用：

```python
uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
connection = sqlite3.connect(uri, uri=True)
connection.execute("PRAGMA query_only=ON")
```

连接使用 context manager 并显式关闭。不得在源库上执行 schema 变更、写入、`VACUUM`、checkpoint 或 attach 可写库。

## 联系人与账号

数据库：`contact/contact.db`

### `contact`

当前行数：2,508。

| 字段 | 类型 | 用途 |
|---|---|---|
| `id` | INTEGER PK | 内部联系人 ID，供成员和发送者关联 |
| `username` | TEXT | 微信账号或群聊 username，稳定业务键 |
| `local_type` | INTEGER | 联系人类别和本地状态 |
| `alias` | TEXT | 微信号/别名 |
| `encrypt_username` | TEXT | 加密 username 元数据 |
| `flag` / `delete_flag` | INTEGER | 联系人状态；删除记录不能默认算活跃联系人 |
| `verify_flag` | INTEGER | 认证/公众号等类型元数据 |
| `remark` | TEXT | 用户备注，显示名优先级最高 |
| `remark_quan_pin` / `remark_pin_yin_initial` | TEXT | 备注拼音字段 |
| `nick_name` | TEXT | 昵称 |
| `pin_yin_initial` / `quan_pin` | TEXT | 昵称拼音字段 |
| `big_head_url` / `small_head_url` | TEXT | 远程头像 URL 元数据 |
| `head_img_md5` | TEXT | 头像摘要 |
| `chat_room_notify` / `is_in_chat_room` | INTEGER | 群相关状态 |
| `description` | TEXT | 联系人描述 |
| `extra_buffer` | BLOB | 未完全解码的扩展字段 |
| `chat_room_type` | INTEGER | 群类型 |

显示名优先级：`remark` -> `nick_name` -> `alias` -> `username`。资料卡的“联系人总数”当前定义为该表的全部行数，不额外应用 `delete_flag` 过滤；它与有真实消息的 session 数是两个不同指标。

### `chat_room`

当前行数：46。字段为 `id`、`username`、`owner`、`ext_buffer`。`username` 与联系人和消息 session 的 username 对齐。

### `chatroom_member`

当前行数：2,437。字段为 `room_id` 和 `member_id`，分别关联 `chat_room.id` 与 `contact.id`。群成员显示名仍应通过联系人表解析。

### `contact_label`

当前行数：5。字段为 `label_id_`、`label_name_` 和 `sort_order_`。只有能从联系人扩展字段可靠解析标签绑定关系时才展示标签。

## 会话摘要

数据库：`session/session.db`

### `SessionTable`

当前行数：289。

| 字段 | 用途 |
|---|---|
| `username` | 会话 username，主键 |
| `type` | 会话类别 |
| `unread_count` | 快照时未读数，不等同历史消息数 |
| `is_hidden` | 隐藏状态 |
| `summary` | 最近消息摘要 |
| `draft` | 本地草稿，不纳入消息统计 |
| `status` | 会话状态 |
| `last_timestamp` / `sort_timestamp` | 最近活动和排序时间 |
| `last_msg_locald_id` | 最近本地消息 ID，字段名以数据库为准 |
| `last_msg_type` / `last_msg_sub_type` | 最近消息类型 |
| `last_msg_sender` / `last_sender_display_name` | 最近发送者元数据 |
| `last_msg_ext_type` | 最近消息扩展类型 |

`SessionTable` 适合最近会话和摘要，不是历史统计的权威来源。历史消息数必须来自 `Msg_*` 表。

## 历史消息

数据库：`message/message_0.db`

### `Name2Id`

当前行数：670。`user_name` 是 session username，`is_session` 表示映射类别。

消息表名必须按以下规则验证：

```text
expected_table = "Msg_" + md5(user_name.encode("utf-8")).hexdigest()
```

只有 `expected_table` 实际存在于 `sqlite_master` 时才能读取。当前有 195 张 `Msg_*` 表。

### `Msg_<md5>`

所有消息表共享以下列：

| 字段 | 类型 | 说明 |
|---|---|---|
| `local_id` | INTEGER | 会话内本地 ID |
| `server_id` | INTEGER | 服务端 ID，引用回连的首选键 |
| `local_type` | INTEGER | 64 位组合消息类型，禁止截成 32 位 |
| `sort_seq` | INTEGER | 会话内排序序列 |
| `real_sender_id` | INTEGER | 真实发送者内部 ID，群聊尤为重要 |
| `create_time` | INTEGER | Unix 时间，按配置时区转换 |
| `status` | INTEGER | 本地消息状态 |
| `upload_status` / `download_status` | INTEGER | 传输状态 |
| `server_seq` | INTEGER | 服务端序列 |
| `origin_source` | INTEGER | 来源类型 |
| `source` | TEXT | 来源/扩展内容，可能被压缩 |
| `message_content` | TEXT | 文本或二进制字节载荷 |
| `compress_content` | TEXT | WCDB 压缩内容 |
| `packed_info_data` | BLOB | 资源和扩展信息 |
| `WCDB_CT_message_content` | INTEGER | WCDB 压缩类型元数据 |
| `WCDB_CT_source` | INTEGER | `source` 压缩类型元数据 |

消息唯一定位键应包含 session：`(session_username, local_id)`；`local_id` 不能假设跨表唯一。引用回连优先使用 `(session_username, server_id)`，必要时扩大到全局 server ID 搜索并记录歧义。

### 组合消息类型

```text
base_type   = local_type & 0xffffffff
app_subtype = local_type >> 32
```

API 同时返回三者。未知值归类为 `other` 并保留数值，不能丢弃高 32 位。

### Zstandard 内容

SQLite 驱动可能把实际字节返回为 `bytes` 或可逆字符串。内容以 Zstandard magic `28 B5 2F FD` 开头时执行真实解压。输出状态至少区分：

- `plain`：原始明文
- `zstd_decoded`：成功解压和解码
- `zstd_error`：识别为 Zstandard 但解压失败
- `text_decode_error`：字节可解压但字符解码失败
- `empty`：字段确实为空

失败状态保留原始长度、类型和错误代码，不构造占位消息正文。

### 群发送者

群文本可能使用 `sender_username:\n正文` 前缀。解析顺序：

1. 识别 session 是否为群聊。
2. 尝试提取前缀 username。
3. 使用联系人 `username` 或 `real_sender_id` 解析显示名。
4. 无法解析时保留 username 或数值 ID，并标记 `sender_resolved=false`。

### app message 与引用

`base_type=49` 的 XML 解析常见字段：

- 标题、描述和 URL
- 文件名、扩展名、大小和资源标识
- 图片/视频元数据
- app subtype
- `<refermsg>` 引用结构

`app_subtype=57` 时重点提取：

| XML 字段 | 规范化字段 |
|---|---|
| `svrid` | `reference.server_id` |
| `fromusr` | `reference.from_username` |
| `chatusr` | `reference.chat_username` |
| `displayname` | `reference.display_name` |
| `content` | `reference.content` |

XML 必须使用禁用外部实体和网络访问的安全解析配置。

## 头像

数据库：`head_image/head_image.db`

### `head_image`

当前行数：1,198。字段为 `username`、`md5`、`image_buffer`、`update_time`。

头像优先级：

1. `head_image.image_buffer`
2. 联系人数据库头像 URL 的元数据
3. 由真实显示名生成的稳定本地占位头像

占位图的颜色和首字母由稳定 hash 生成，不能使用参考图头像。

## 消息资源

数据库：`message/message_resource.db`

### `MessageResourceInfo`

当前行数：5,402。核心字段：`message_id`、`chat_id`、`sender_id`、`message_local_type`、`message_create_time`、`message_local_id`、`message_svr_id`、`message_origin_source`、`packed_info`。

### `MessageResourceDetail`

当前行数：13,016。核心字段：`resource_id`、`message_id`、`type`、`size`、`create_time`、`access_time`、`status`、`data_index`、`packed_info`。

`MessageResourceDetail.message_id` 关联 `MessageResourceInfo.message_id`。缓存中的 `message_resources` 再优先使用 `MessageResourceInfo.message_svr_id` 回连规范化消息；服务端 ID 不可用时使用 `(chat_id, message_local_id)`。当前 13,016 条 detail 中有 12,969 条完成消息关联。

`packed_info` 的 protobuf length-delimited 字段只进行本地 UTF-8 元数据提取，不猜测未知字段语义。硬链接候选使用真实文件大小做 best-effort 匹配。资源索引存在不等于磁盘文件存在，只有解析路径确实可读时 `resource_available=true`；文件存在但格式不能由浏览器直接预览时 `browser_supported=false`。

## 语音

数据库：`message/media_0.db`

### `VoiceInfo`

当前行数：48。字段为 `chat_name_id`、`create_time`、`local_id`、`svr_id`、`voice_data`、`data_index`。语音数据只在浏览器支持且格式能够可靠识别/转换时提供播放；否则保留索引和不可播放状态。

## 硬链接与真实文件名

数据库：`hardlink/hardlink.db`

### `dir2id`

当前行数：129，`username` 为主键。SQLite rowid 可作为硬链接表中 `dir2` 的会话目录映射候选，使用前必须通过真实路径验证。

### `image_hardlink_info_v4`

当前行数：5,235。

### `video_hardlink_info_v4`

当前行数：127。

### `file_hardlink_info_v4`

当前行数：398。

三个表共享主要字段：`md5_hash`、`md5`、`type`、`file_name`、`file_size`、`modify_time`、`dir1`、`dir2`、`_rowid_`、`extra_buffer`。

文件解析使用解密报告的 `source_root` 推导账号原始目录，再根据硬链接目录和文件名枚举有限候选。解析后的绝对路径必须位于报告账号目录内。不能把请求参数直接拼成文件系统路径。

## 朋友圈 SNS

数据库：`sns/sns.db`

### `SnsTimeLine`

当前开发快照为 892 行。字段：`tid`、`user_name`、`content`、`pack_info_buf`。

- `tid` 是 SQLite signed int64；`content` 中 `<TimelineObject><id>` 是同一位模式的 uint64 十进制。API 同时保留字符串 `sns_id` 和 `raw_tid`，禁止交给 JavaScript `number`。
- `content` 是 `<SnsDataItem>` XML，包含正文、创建时间、隐私、位置、内容类型、媒体、评论、点赞、Finder 和 LivePhoto 等节点。
- `pack_info_buf` 和未知 XML 节点不做有损迁移；原始数据库快照与 `raw_xml` 一并进入私有备份。
- 列表 API 不返回 `raw_xml`，详情和 ZIP 才按需保留，避免每页传输完整原始载荷。

### `SnsMessage_tmp3`

当前开发快照为 23 行。它保存互动通知与原始 protobuf BLOB，主要字段包括 `feed_id`、`from_username`、`to_username`、`content`、`comment_id`、`comment64_id`、`serialized_comment_buf` 和 `serialized_ref_buf`。

`feed_id` 使用 signed int64，与 `SnsTimeLine.tid` 关联；导出时同时保留 signed 字符串和 uint64 `post_id`。BLOB 在 `interactions.jsonl` 中以 Base64、hex 和字节数三种信息保存。

### 本机媒体缓存

账号根目录来自解密报告 `source_root` 的父目录。备份可复制：

- `cache/<月份>/Sns/**`：图片、视频、缩略图和临时文件。
- `business/sns/**`：朋友圈背景等业务缓存。

这些文件按原始字节和相对路径归档，并生成 SHA-256。无扩展名图片可能是微信私有 v2 容器，不能伪装成 JPEG；远程媒体 URL 也不能视为本地资源已存在。

## 收藏

数据库：`favorite/favorite.db`

### `fav_db_item`

当前行数：23。主要字段为 `local_id`、`server_id`、`type`、`update_time`、`content`、`source_id`、`fromusr`、`realchatname` 和同步/资源状态字段。

收藏搜索结果可以作为 `/search` 的独立类别，不能与消息数重复相加。

## 全文搜索

数据库：

- `contact/contact_fts.db`
- `message/message_fts.db`
- `favorite/favorite_fts.db`

每个连接都单独加载 `MMFtsTokenizer`。当前 FTS 表：

| 数据集 | FTS 表 | 返回方式 |
|---|---|---|
| 联系人原文 | `contact_fts_v4` | 可请求 `rank` |
| 联系人拼音 | `contact_fts_pinyin_v4` | 只读取 rowid |
| 拼音内容 | `contact_fts_v4_content` | 根据 rowid 二阶段回表 |
| 消息 | `message_fts_v4_0` 到 `_3` | 四个 shard 分别查询后按 rank 合并 |
| 收藏 | `fav_fts_v1` | 直接 FTS 查询 |

联系人拼音索引使用外部 content。不得从 `contact_fts_pinyin_v4` 请求 content 列、`rank` 或 `bm25()`；必须先取 rowid，再从 `contact_fts_v4_content(id, c0, c1)` 获取搜索内容和类型。

## 派生缓存

缓存文件：`wechat-db-web-panel/.cache/analytics.sqlite`。

当前缓存表：

- `meta`：schema 版本、源指纹、构建统计和快照元数据
- `contacts`：规范化联系人、显示名、群/系统标记
- `messages`：跨 195 张表的规范化消息、引用和质量分
- `sessions`：会话聚合、响应中位数、双向比例和关系强度
- `episodes`：30 分钟间隔切分后的时间线条目
- `daily` / `hourly`：日期和小时聚合
- `message_resources`：资源 detail 到规范化消息的关联
- `resources`：硬链接候选、路径、大小、可用和浏览器支持状态

缓存 schema 可以演进，但必须能从源库完全重建，且不能写入 `runtime/db_decrypt/all`。

## 实时事件账本

账本文件：`wechat-db-web-panel/.cache/realtime.sqlite`。它不是微信源数据库，只保存本机 Hook 到达后尚未进入下一份解密快照的增量事实和投递状态。

`realtime_events` 主要字段：

| 字段 | 说明 |
|---|---|
| `sequence` | 账本内单调递增游标 |
| `scope_key` | `account + database_key` 的稳定哈希 |
| `database_key` | 解密数据库根目录绝对身份的哈希 |
| `account` / `snapshot` | 账号和事件接收时的快照校准点 |
| `session_key` | 会话标识，参与两层幂等约束 |
| `event_id` / `identity_key` | relay 事件 ID 和原生 `msgId/localId` 身份 |
| `status` | `accepted`、`pending`、`applied` 或兼容迁移状态 |
| `quality_json` | 解码状态、错误和警告 |
| `payload_json` | 原始本地 envelope |
| `normalized_json` | 可物化的规范化消息；信息不足时为 `null` |

唯一索引分别覆盖 `(scope_key, session_key, event_id)` 和 `(scope_key, session_key, identity_key)`。账本可跨同账号的新快照复用，但不同账号或不同数据库根目录互相隔离。只有 `applied` 事件进入 SSE；analytics 缓存仍可删除并由只读源库加此账本重建。

## 数据质量字段

所有聚合响应应提供或继承以下字段：

| 字段 | 说明 |
|---|---|
| `snapshot_time` | 解密报告的快照时间 |
| `metric_basis` | 指标使用的记录范围和算法 |
| `decoded` | 相关文本是否成功解码 |
| `resource_available` | 相关媒体是否能从本地读取 |
| `partial` | 是否因缺失库、解析失败或筛选而不完整 |
| `quality_notes` | 面向 UI 的具体降级说明 |

空值、未知值和零值必须区分。数据库没有来源的字段返回 `null` 和原因，不用 `0` 或虚构文本替代。
