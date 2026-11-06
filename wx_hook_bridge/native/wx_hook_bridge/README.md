# wx_hook_bridge（原生工程）

可维护的 `wx_hook_bridge.dll` 源码布局。

- 源码：`src/`（入口 + `include/` + `modules/`）
- 编译：`build.ps1` / `build.py`（work root 由注入器在运行时传入）
- 边界检查：`verify_boundaries.py`
- 默认输出：仓库根 `dist/wx_hook_bridge.dll`

```powershell
# 仓库根
powershell -ExecutionPolicy Bypass -File native\wx_hook_bridge\build.ps1
# 可选：-OutFile "dist\wx_hook_bridge.dll"
```

- 使用步骤：[docs/quick-start.md](../../docs/quick-start.md)
- 模块架构：[docs/native-architecture.md](../../docs/native-architecture.md)
- 本地实时传输：[docs/realtime-transport.md](../../docs/realtime-transport.md)

## 实时 relay

`DoAddMsg` Hook 同时产生真实收到消息和已发送消息回写事件。Hook 线程只写入预分配内存 slot；专用线程将事件原子持久化到 `bridge/realtime_queue/`，再 POST 到 `http://127.0.0.1:8765/api/realtime/ingest`。失败会在本机退避重试，状态见 `bridge/control/realtime_relay_status.json`。

传输配置只接受回环地址。消息事件使用 `wechat-message:<msgId>` 作为稳定幂等键。事件级刷新、数据库 `snapshot_time` 校准和断线降级规则见 [docs/realtime-transport.md](../../docs/realtime-transport.md)。

```powershell
python -m unittest discover -s native\wx_hook_bridge\tests -v
powershell -ExecutionPolicy Bypass -File native\wx_hook_bridge\build.ps1 -SyntaxOnly
```
