# Verification

- 变更：`harden-source-counts-missing-db`
- PR：https://github.com/ssq123123/jqr/pull/1（分支 `fix/missing-db-source-counts`）
- 日期：2026-10-13
- 环境：Windows 11 x64 · Python 3.12（`wechat-db-web-panel/.venv`）· 数据根 `runtime/db_decrypt/all`（账号 `wxid_example_bccc`，微信 4.1.9.23）

## 一、缺陷复现（修复前）

构造数据根副本并删除 `message/media_0.db`，重算缓存：

```text
File "...\backend\app\repository.py", line 284, in ensure_cache
    fingerprint = self._source_fingerprint()
File "...\backend\app\repository.py", line 266, in _source_fingerprint
    stat = path.stat()
FileNotFoundError: [WinError 2] 系统找不到指定的文件。: '...\message\media_0.db'
ERROR:    Application startup failed. Exiting.
```

结论：缺库时**服务无法启动**，不是接口 500。

旧 `_source_counts()` 逻辑单独复现同样抛错：

```text
旧逻辑抛异常 -> FileNotFoundError: ...\message\media_0.db
```

## 二、修复后验证

### 2.1 单元（临时数据根缺 `media_0.db`）

```text
== 修复后 _source_counts() ==
{"avatar_count":"4","chatroom_count":"2","contact_count":"3","resource_detail_count":"9",
 "resource_info_count":"7","session_count":"5","voice_info_count":"0"}
key 数量: 7 (期望 7)
== 修复前逻辑复现（直接读取缺失的 media_0.db）==
旧逻辑抛异常 -> FileNotFoundError
修复后计数完全符合预期: True / 修复前缺库确实抛错: True
```

### 2.2 端到端（缺库数据根启动实例）

```text
数据根缺 media_0.db = True | 缓存已清: True
启动就绪 3.1s /api/health -> 200 {"status":"ok",...,"message_count":1422,...}
/api/overview -> 200 {"kpis":{"messages":1422,"contacts":23574,"sessions":103,...}}
INFO:     Application startup complete.
```

修复前同一数据根在启动阶段退出，修复后正常就绪并返回 200。

### 2.3 回归（来源库齐全）

对真实数据根运行修复后的 `_source_counts()`，与修复前构建的缓存 `meta` 逐项比对：

```text
{"avatar_count":"115","chatroom_count":"93","contact_count":"23574",
 "resource_detail_count":"51","resource_info_count":"18","session_count":"103","voice_info_count":"0"}
与修复前缓存基线完全一致: True
```

## 三、环境差异说明

端到端实例日志含 `SNS snapshot refresh failed: SQLCipher key manifest is missing: None`。这是该验证实例未配置 SQLCipher 密钥清单导致，属既有降级行为（不阻断启动），与本变更无关。

## 四、复现命令

```powershell
$py = "C:\Users\A\jqr\wx_hook_bridge\wechat-db-web-panel\.venv\Scripts\python.exe"
# 单元 + 对照
& $py C:\Users\A\jqr\_setup\_verify_source_counts.py
# 回归比对
& $py C:\Users\A\jqr\_setup\_verify_regression.py
# 端到端：构造缺库数据根后以 8767 端口启动
#   环境变量 WECHAT_DB_ROOT / WECHAT_DECRYPTION_REPORT / WECHAT_ANALYTICS_CACHE
```
