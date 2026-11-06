# 仓库分层说明

## 设计原则

1. **按语言分层**：`native/` 为 C 源码，`python/` 为 Python 源码。
2. **按职责再分**：Python 分为 inject / message / tools。
3. **产物与源码分离**：编译结果输出到 `dist/`（`.gitignore` 忽略）。
4. **文档独立**：说明文档放在 `docs/`，根 `README` 提供导航。

## 顶层

| 路径 | 职责 |
|------|------|
| `native/` | 原生 DLL 工程 |
| `python/` | Python 辅助与 CLI |
| `docs/` | 文档 |
| `dist/` | 构建输出（忽略提交） |

## native/

```text
native/wx_hook_bridge/
├── src/
│   ├── wx_hook_bridge.c    入口编译单元
│   ├── include/            对外头文件
│   └── modules/            各业务域 .c 实现
├── build.ps1 / build.py    编译
└── verify_boundaries.py    模块边界检查
```

详细模块表见 [native-architecture.md](native-architecture.md)。

## python/

| 子目录 | 职责 | 文件 |
|--------|------|------|
| `inject/` | 找微信进程、编译/注入 DLL、更新锁定 | `wechat_inject.py`, `wechat_native.py`, `wechat_update_lock.py` |
| `message/` | 消息规范化与载荷构造 | `message_normalizer.py`, `quote_builder.py`, `finder_payload.py` |
| `tools/` | 独立 CLI | `wx_message_bridge.py` |

### 路径约定

- 仓库根目录 = 项目根（`PROJECT_ROOT`）。
- `python/inject/wechat_inject.py` 通过 `Path(__file__).parents[2]` 回退到仓库根。
- 默认 DLL 路径：`dist/wx_hook_bridge.dll`。
- 默认源码入口：`native/wx_hook_bridge/src/wx_hook_bridge.c`。
- 运行时目录：`bridge/`、`runtime/`（位于仓库根，已列入 `.gitignore`）。

## docs/

| 文件 | 内容 |
|------|------|
| `quick-start.md` | 端到端使用说明（优先阅读） |
| `features.md` | 完整功能清单与 outbox 类型号 |
| **`hongbao-cold.md`** | **冷抢配置：开关、延迟（固定/区间 ms）、日志** |
| `structure.md` | 本文件：目录分层 |
| `native-architecture.md` | 原生模块拆分与构建说明 |
