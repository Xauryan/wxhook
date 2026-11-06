# Design

## 背景

`ensure_cache()` 在应用 lifespan 中执行，先算指纹再建缓存。两处按来源库无容错地访问解密产物：

```python
# _source_fingerprint()
path = self.database(relative)
stat = path.stat()                      # 缺文件 -> FileNotFoundError

# _source_counts()
with open_readonly(self.database(relative)) as source:
    counts[key] = str(source.execute(sql).fetchone()[0])   # 缺库/缺表 -> 异常
```

任一来源缺失都会中断整次快照构建，并在启动阶段表现为 `Application startup failed. Exiting.`。

## 决策

两级容错，就地处理，不引入新的状态字段：

1. `_source_fingerprint()`：`path.stat()` 包 `try/except OSError`，缺失时 `size` 与 `mtime_ns` 记为 `None`。指纹仍由全部来源（含缺失项）参与计算，因此"补上缺失库"会自然改变指纹并触发重建。
2. `_source_counts()`：外层 `try/except (FileNotFoundError, sqlite3.OperationalError)` 覆盖"库不存在/无法打开"；内层 `except sqlite3.OperationalError` 覆盖"库在但表不存在"。两种情况都把该来源的计数记为 `0`，同库其他计数继续。

计数键集合保持 7 项不变，缺失来源表现为 `0`，调用方 `metadata.get(key, 0)`、`data_quality` 与前端展示无需改动。

## 备选与否决

| 方案 | 否决原因 |
|---|---|
| 先用 `sqlite_master` 预探测表是否存在 | 多一次查询与分支，且覆盖不了"库文件本身缺失"，外层容错仍不可少 |
| 在面板侧自动创建占位库 | 面板对数据根应是只读语义，写用户数据目录超出职责；占位库应由解密工具或用户决定 |
| 指纹直接跳过缺失来源 | 会让"缺失"与"存在"产生相同指纹，补库后不触发重建，缓存变陈旧 |
| 只修 `_source_counts()` | 实测无效：`ensure_cache` 先算指纹，服务仍在启动阶段失败 |

## 影响面

- 库齐全时：指纹逐项值与计数逐项值均与修复前一致（已用真实数据根与修复前缓存基线比对）。
- 库缺失时：服务可启动，缺失来源计数为 `0`，缺失来源指纹项为 `null`，其余数据不受影响。

## 未覆盖

- 语音详情、头像、SNS 快照等其他读取路径的缺失处理不在本次范围。SNS 快照依赖 SQLCipher 密钥清单，缺失时已有独立降级日志，不受本改动影响。
- 解密工具是否应产出 `media_0.db`、或在报告中标记缺失来源，另案讨论。
