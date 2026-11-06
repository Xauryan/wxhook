# 端到端快速开始

从零到「编译 → 注入 → 发一条文本 → 看到结果」的最短路径。

所有命令默认在**仓库根目录**执行。

红包冷抢（含 **延迟配置**）见：[hongbao-cold.md](hongbao-cold.md)。

---

## 0. 环境要求

| 项 | 要求 |
|----|------|
| OS | Windows x64 |
| 编译器 | Visual Studio 2022（含 C++ 桌面开发 / Build Tools，`cl` + `link`） |
| Python | 3.11+（标准库即可，无需额外 pip 包） |
| 微信 | 已安装并**已登录**的 PC 微信（进程名 `Weixin.exe` / `WeChat.exe`） |
| 权限 | 建议**管理员 PowerShell**（注入远端进程时更稳） |

可选自检：

```powershell
python --version
where cl   # 若找不到，构建脚本会通过 vcvars64.bat 自动拉起 MSVC
```

---

## 1. 准备仓库与目录

```powershell
cd <你的仓库根目录>
# 例如: cd D:\苏世奇的AI日记\4.1.9.23
```

运行时目录由注入/原生层自动创建（已列入 `.gitignore`）：

```text
bridge/
  inbox/           # 收到的消息（每个文件一条 JSON）
  outbox/
    next.txt       # 你写入的命令（TAB 分隔，执行后会被清空）
  control/
    status.txt     # 最近状态文本
    heartbeat.txt  # 注入侧心跳
    result_<seq>.json
    stop.txt       # 存在则 worker / inject 退出
```

> **路径对齐**：原生 DLL 不再在编译时绑定 `WORK_ROOT`。
> Python 注入侧启动 `WxBridgeWorker` 时会把当前仓库根目录作为运行时 work root 传进 Weixin.exe。
> 因此 DLL 可以在 CI 或任意目录编译，运行时目录仍由本仓库的 `PROJECT_ROOT` 决定。

---

## 2. 编译 DLL

```powershell
powershell -ExecutionPolicy Bypass -File native\wx_hook_bridge\build.ps1
```

或：

```powershell
python native\wx_hook_bridge\build.py
```

成功时终端类似：

```text
built: D:\...\4.1.9.23\dist\wx_hook_bridge.dll
```

### 常见编译问题

| 现象 | 处理 |
|------|------|
| 找不到 `vcvars64.bat` | 安装 VS 2022 C++ 生成工具 |
| `LNK1104` 无法打开 dll | DLL 仍被微信加载：先停 inject / 写 `stop.txt` / 必要时重启微信后再编 |
| `verify_boundaries.py` 失败 | 先修模块边界再编；不要跳过校验 |

---

## 3. 注入到微信

先确认微信在跑：

```powershell
python python\tools\inject_bridge.py --list
```

应能看到至少一个 `pid=... name=Weixin.exe`（或 WeChat.exe）。

再注入（会编译过期 DLL、远程加载、启动 `WxBridgeWorker`，并**保持前台进程运行**）：

```powershell
python python\tools\inject_bridge.py
```

可选：

```powershell
python python\tools\inject_bridge.py --force-build
python python\tools\inject_bridge.py --pid <PID>
```

成功时 stderr 大致为：

```text
Target: pid=... tid=... hwnd=0x...
已远程加载 DLL: ...
已在 Weixin.exe 内启动 WxBridgeWorker: ...
保持此进程运行；写 stop.txt 后远端 worker 会自行退出。
```

**保持这个窗口不要关。** 另开一个 PowerShell 做第 4 步。

### 注入侧自检

| 检查 | 路径 / 方法 |
|------|-------------|
| 心跳在更新 | `bridge\control\heartbeat.txt` 数字应每秒变化 |
| 状态文件 | `bridge\control\status.txt` 会有最近操作摘要 |
| 停 worker | `echo stop > bridge\control\stop.txt` 或对 inject 进程 Ctrl+C |

### 常见注入问题

| 现象 | 处理 |
|------|------|
| No WeChat process | 先登录微信客户端 |
| 有 pid 无 UI thread | 确保主窗口已打开、未完全托盘隐藏后再试 |
| Remote LoadLibrary 失败 | 尝试管理员权限；检查杀软拦截；看 stderr 完整报错 |
| 模块未出现在进程中 | 重启微信后重新 inject |

---

## 4. 发送一条文本（端到端）

### 方式 A：示例脚本（推荐）

在**第二个**终端、仓库根目录：

```powershell
powershell -ExecutionPolicy Bypass -File examples\send_text.ps1 -To filehelper -Content "hello from wx_hook_bridge"
```

- `-To filehelper`：发给文件传输助手（最安全自测目标）
- 也可换成好友 / 群的 `wxid`（群 id 通常含 `@chatroom`）

脚本会：

1. 写 `bridge\outbox\next.txt`
2. 等待 `bridge\control\result_<seq>.json`
3. 打印结果 JSON

成功示例：

```json
{"ok":true,"seq":"1710000000000","command":"send_text","message":"send_text ok"}
```

同时微信里文件传输助手应出现该文本。

### 方式 B：手动写 outbox

文件：`bridge\outbox\next.txt`
编码：UTF-8
格式（字段之间是 **Tab**，不是空格）：

```text
<seq>	1	<to_wxid>	<content>
```

| 字段 | 含义 |
|------|------|
| `seq` | 唯一请求号（可用毫秒时间戳） |
| `1` | 命令类型：发送文本（见 [features.md](features.md)） |
| `to_wxid` | 目标，如 `filehelper` |
| `content` | 文本正文（尽量单行，避免 Tab） |

PowerShell 一行写入：

```powershell
$seq = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$to = "filehelper"
$content = "hello from wx_hook_bridge"
New-Item -ItemType Directory -Force -Path bridge\outbox, bridge\control | Out-Null
Set-Content -Path bridge\outbox\next.txt -Value "$seq`t1`t$to`t$content" -Encoding utf8NoBOM
```

等待结果：

```powershell
Get-Content bridge\control\result_$seq.json
Get-Content bridge\control\status.txt
```

命令被消费后，`next.txt` 会被清空或内容变空。

### 方式 C：Python CLI

```powershell
python python\tools\wx_message_bridge.py file-send --to filehelper --content "hello from wx_hook_bridge" --type 1
```

（同样要求 inject 已在跑，且 CWD 为仓库根。）

---

## 5. 接收消息（可选验证）

注入成功后，用微信给**本账号**发一条消息（或在群里有你在的会话产生消息）。

监视 inbox：

```powershell
python python\tools\wx_message_bridge.py file-serve --inbox-dir bridge\inbox
```

或直接看目录：

```powershell
Get-ChildItem bridge\inbox\*.txt | Sort-Object LastWriteTime -Descending | Select-Object -First 5
Get-Content (Get-ChildItem bridge\inbox\*.txt | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
```

每条消息一般是一个 JSON 文本文件。

---

## 6. 停止

```powershell
# 在仓库根
Set-Content -Path bridge\control\stop.txt -Value "stop" -Encoding ascii
```

或对 `inject_bridge.py` 窗口按 **Ctrl+C**。

重新编译前若 DLL 被占用，先停止再 `build.ps1`。

---

## 7. 端到端检查清单

按顺序勾选：

1. [ ] 存在 `dist\wx_hook_bridge.dll`
2. [ ] 微信已登录，`inject_bridge.py --list` 有进程
3. [ ] `inject_bridge.py` 保持运行，`heartbeat.txt` 在更新
4. [ ] `send_text.ps1 -To filehelper` 得到 `result_*.json` 且 `"ok":true`
5. [ ] 微信文件传输助手收到对应文本
6. [ ] （可选）收消息时 `bridge\inbox` 出现新文件

任一步失败：先看 `bridge\control\status.txt` 与 inject 终端输出。

---

## 8. 协议摘要（发文本）

```text
outbox/next.txt  →  seq \t 1 \t wxid \t content
                     ↓  DLL poll_outbox
                send_text(wxid, content)
                     ↓
control/result_<seq>.json  { ok, seq, command, message }
control/status.txt         人类可读状态行
```

更多命令类型与字段见 [features.md](features.md)。

---

## 9. 兼容性说明

- 偏移与 Hook 点与特定微信 PC 版本绑定；版本升级后可能失效，需同步更新 `wx_bridge_offsets.h` 等。
- 本仓库工程历史对齐版本号：**4.1.9.23**（以你本机 `Weixin.dll` 实际版本为准做验证）。
- 仅在自有环境、合法授权场景下使用。

---

## 相关文档

- [功能清单](features.md)
- [目录结构](structure.md)
- [原生架构](native-architecture.md)
