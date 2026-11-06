# 朋友圈备份

## 数据范围

朋友圈页面读取 `runtime/db_decrypt/all/sns/sns.db`，不依赖 `getSNSNextPage` 的兼容调用，也不受原生 timeline 单次捕获数量限制。当前快照包含 892 条 `SnsTimeLine` 动态和 23 条 `SnsMessage_tmp3` 原始互动。

稳定业务 ID 使用 XML `<TimelineObject><id>` 的 uint64 十进制字符串。SQLite `tid/feed_id` 可能是负数，作为 `raw_tid/feed_id` 字符串原样保留，不转换为 JavaScript 数值。

## 页面与 API

- `GET /api/sns?limit=24&offset=0&q=`：分页列表，返回精简动态、总数、统计和快照时间。
- `GET /api/sns/stats`：动态、联系人、评论、点赞、媒体和原始互动计数。
- `GET /api/sns/{sns_id}`：完整动态、原始 XML 及关联互动。
- `GET /api/sns/backups`：列出 `.cache/sns-backups/` 中的归档。
- `POST /api/sns/backups`：生成归档。请求可传 `backup_id`、`include_local_media`、`include_runtime_artifacts`。
- `GET /api/sns/backups/{backup_id}/download`：下载 ZIP。
- `DELETE /api/sns/backups/{backup_id}`：删除 ZIP 与同名元数据。

所有端点仅接受回环请求。backup ID 只允许字母、数字、点、下划线和短横线，并拒绝 `..` 和路径分隔符。

## ZIP 结构

```text
backup.zip
|-- sns.db
|-- posts.jsonl
|-- comments.jsonl
|-- likes.jsonl
|-- media.jsonl
|-- interactions.jsonl
|-- manifest.json
|-- schema.json
|-- local_media/          可选，本机已有 SNS 缓存
`-- runtime_artifacts/    可选，有大小上限的原生捕获记录
```

`sns.db` 由 SQLite online backup 生成。所有 JSONL 随后只从该副本读取，因此数据库和结构化导出属于同一时间点。`interactions.jsonl` 同时包含 XML 内嵌评论/点赞和 `SnsMessage_tmp3` 原始记录；BLOB 使用 Base64 与 hex 保存。

`manifest.json` 记录账号、快照时间、源表 schema/行数、导出计数、媒体策略和逐文件 SHA-256。`source_fingerprint.sha256` 对 ZIP 内实际 `sns.db` 快照计算；解密报告中的历史哈希单独保存在 `source_fingerprint.reported_sha256`，避免活跃 WAL 或报告滞后造成误判。`schema.json` 描述字段与目录语义。

## 媒体策略

默认复制账号目录中已经存在的 `cache/<月份>/Sns/**` 和 `business/sns/**`。文件不做转码或扩展名猜测，微信私有 v2 图片保持原字节。

服务不会请求远程媒体 URL。未在本机缓存中的图片或视频只保留 XML/JSON 元数据，并标记为 `remote_only`。URL 可能包含临时签名，所以 ZIP 是本机私有归档，不应直接公开。

原生 SNS 日志单文件上限 16 MiB、累计上限 64 MiB。超过限制的日志不复制，只在 manifest 中记录 `skipped_size_limit`。

缓存和诊断目录中的符号链接不会被跟随。归档使用同目录原子发布，同一 `backup_id` 已存在时返回错误，不覆盖旧备份。

## 校验

下载后至少检查：

1. ZIP 可正常打开，且存在 `manifest.json`、`schema.json` 和 `sns.db`。
2. `manifest.files` 中每个路径的 SHA-256 与 ZIP 内容一致。
3. `source_row_counts.SnsTimeLine` 与 `counts.posts` 一致。
4. 用 SQLite 打开 `sns.db`，执行 `PRAGMA quick_check` 返回 `ok`。
5. `local_media_files` 只代表复制到的缓存文件数量，不代表每个远程媒体均已命中本地文件。
