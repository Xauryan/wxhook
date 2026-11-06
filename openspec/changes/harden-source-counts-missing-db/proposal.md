## Why

面板在选择数据根后会构建本地快照缓存，其中两处按来源库逐个（且无容错地）访问解密产物：

- `_source_fingerprint()` 对 10 个来源库逐个 `path.stat()`，用于生成缓存指纹。
- `_source_counts()` 对 5 个来源库逐个执行 `count(*)`，用于生成来源统计。

只要其中任意一个库文件缺失（URI 打开不存在的文件）或表尚未创建，`FileNotFoundError` / `sqlite3.OperationalError` 就会冒泡，导致整次缓存构建失败。`ensure_cache()` 在应用 lifespan 中执行，因此表现为**服务启动即失败**（`Application startup failed. Exiting.`），而不是某个来源暂时没有数据。

这是实际复现的缺陷：用一个新建账号解密后，该账号尚未生成 `message/media_0.db`，面板直接起不来；手工补一个含 `VoiceInfo` 表的占位库后，面板才恢复。对刚注册或长期未使用语音的账号来说，`media_0.db` 缺失是常态而非异常。

## What Changes

- `_source_fingerprint()`：来源文件缺失时按"未知大小/未知修改时间"参与指纹（记为 `null`），不再中断指纹计算。
- `_source_counts()`：按来源库独立容错——库文件缺失、库无法打开或表不存在时，该来源计数记为 `0`，其余来源照常统计并返回。

两处都沿用既有"缺失来源表现为空/0"的表现，接口返回结构与 `data_quality` 判定语义保持不变。

## Capabilities

### Modified Capabilities

- 本地快照缓存构建：单个来源库缺失不再中断整次构建，服务可正常启动，其余来源的统计、会话、消息数据照常可用。

### New Capabilities

无。

## Impact

仅影响 `wechat-db-web-panel/backend/app/repository.py` 的 `_source_fingerprint()` 与 `_source_counts()`。不改变库齐全时的指纹与统计结果，不改变任何 API 的返回字段与类型，不改变解密工具、占位库生成与前端展示。

## Non-goals

- 不新增"来源缺失"的显式标记字段，不改变 `data_quality` 的判定语义。
- 不修改解密工具是否生成 `media_0.db`，也不自动创建占位库。
- 不调整其他读取路径（如语音详情、头像、SNS 快照）的缺失处理，仅修复这两处导致的整体失败。
