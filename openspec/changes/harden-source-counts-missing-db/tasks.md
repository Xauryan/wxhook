## 1. 定位与修复

- [x] 1.1 复现：数据根缺 `message/media_0.db` 时，`ensure_cache()` → `_source_fingerprint()` 抛 `FileNotFoundError`，服务启动阶段失败（`Application startup failed. Exiting.`）。
- [x] 1.2 `_source_fingerprint()`：来源文件缺失时 `size` / `mtime_ns` 记为 `null`，指纹仍由全部来源参与计算。
- [x] 1.3 `_source_counts()`：库缺失/无法打开（外层）与表缺失（内层）分别容错，该来源计数记 `0`，其余照常。

## 2. 验证

- [x] 2.1 单元验证：临时数据根缺 `media_0.db` 时，`_source_counts()` 返回完整 7 项，`voice_info_count` 为 `0`，其余计数与实际一致。
- [x] 2.2 对照验证：同一场景下旧逻辑读取 `media_0.db` 抛出 `FileNotFoundError`。
- [x] 2.3 端到端验证：以缺库数据根启动后端实例，3.1s 就绪，`GET /api/health`、`GET /api/overview` 均返回 `200`。
- [x] 2.4 回归验证：来源库齐全时，7 项统计与修复前缓存基线（`contact_count=23574`、`chatroom_count=93`、`session_count=103`、`resource_info_count=18`、`resource_detail_count=51`、`voice_info_count=0`、`avatar_count=115`）完全一致。

## 3. 交付

- [x] 3.1 记录 `verification.md` 的实测命令与结果。
- [x] 3.2 提交到分支 `fix/missing-db-source-counts` 并推送，PR #1 已开，等待评审。
