# 冷抢（hongbao cold）配置说明

生产环境红包能力**仅启用冷抢**（对齐 `RE/frida_cold_hongbao_auto.py`）：

```text
newsync raw AddMsg（可选快车道）→ 微信原生 raw-to-LocalMsg
LocalMsg → FillFromLocalMsg → Service_StartReceive
  → [可选延迟] → Launch_receive (1581)
  → ParseExtra 取 timing
  → Service_StartOpen → Launch_open (1685)
```

实现模块：`native/wx_hook_bridge/src/modules/hongbao_cold.c`  
控制目录：注入时的 `work_root/bridge/control/`（仓库内常见为 `wx_hook_bridge/bridge/control/`）。

---

## 开关

| 文件 | 作用 | 默认 |
|------|------|------|
| （无文件） | 冷抢开启 | 开启 |
| `hb_cold.disable` | 关闭冷抢 | — |
| `hb_cold_open.disable` | 只发 1581，不点「开」(1685) | 不禁用 open |
| `hb_cold_fastlane.enable` | 在 newsync AddMsg 层提前构造 receive，原 source_submit 路径仍作回退 | 关闭 |

创建 / 删除控制文件后约 100ms 内由 worker 轮询生效，一般无需重启微信。

---

## newsync 快车道（实验）

快车道只适用于微信 `4.1.9.23`，入口为 `Weixin.dll+0x2AE0890`。安装时会校验 IDA 确认的 15 字节完整指令序列；字节不匹配则拒绝挂钩，不影响原冷抢。

处理链：

```text
raw AddMsg vector（元素 0x78）
  → 直接读取 content 并提取 sendid
  → sub_1809D2F00 构造临时 0x290 LocalMsg
  → 现有 FillFromLocalMsg + Service_StartReceive
  → 线程池 Launch_receive
  → 原 sub_182AE0890 继续执行
```

快车道不会构造 CGI、登录或 MMTLS 数据。`Launch_receive` 仍通过微信全局任务执行器发送，复用当前登录 session；原 `source_submit` 冷抢路径保留并由 sendid 去重。

### IDA 证据（Weixin.dll 4.1.9.23）

| 地址 | 结论 |
|------|------|
| `0x182ADC780` | 入口前 12 字节是完整的 8 条 push 指令，可安全覆盖，但收到的是未筛选 sync 批次，范围过宽，未采用 |
| `0x182AE0890` | 入口前 15 字节是完整 prologue；`a1` 为元素步长 `0x78` 的 AddMsg vector，直接同步调用 `0x18166C6A0`，采用此处 |
| `0x1809D1810` | 证明 `raw+0x20 -> holder+0x08` 是 content string；`raw+0x08/+0x18` 分别映射到 LocalMsg from/to |
| `0x1809D2F00` | 微信原生 `raw 0x78 -> LocalMsg 0x290` 构造器 |
| `0x1801CB270` | 与上述临时 LocalMsg 配对的析构函数 |
| `0x184A64BB0` | `Launch_receive` 从全局服务根取现有执行器并调用 `0x180290390` 投递任务，没有登录或握手参数 |

传输层长连接若已断开，微信自身仍可能重连；“复用 session”指快车道不实现、不触发独立登录/MMTLS 握手，不能承诺底层 TCP 永远处于已连接状态。

```powershell
# 开启实验快车道
New-Item -ItemType File bridge\control\hb_cold_fastlane.enable -Force

# 关闭快车道分支（原冷抢继续工作）
Remove-Item bridge\control\hb_cold_fastlane.enable -ErrorAction SilentlyContinue
```

命中日志：

```text
[cold:RECV-HB ...] sendId=... via=newsync
[cold:FASTLANE ...] sendId=... item=1/1 build_ms=...
```

---

## 延迟配置 `hb_cold_delay.json`

**路径：** `bridge/control/hb_cold_delay.json`

### 作用时机

在 **Fill + Service_StartReceive 完成之后**、**Launch_receive(1581) 发包之前** 等待。

- 延迟在线程池 job 里执行，**不阻塞**收消息线程。
- **无配置文件 → 不延迟（0ms）**。
- 数值 **下限 0**；上限封顶 **600000**（10 分钟）。

### 唯一格式：`delay_ms`（值始终为字符串）

**固定延迟：**

```json
{"delay_ms": "200"}
```

**区间随机（闭区间，单位 ms）：**

```json
{"delay_ms": "10-200"}
```

`-` 两侧可有空格：`"10 - 200"`。  
固定与区间都用引号，不要写数字类型（不支持 `200` 无引号）。

### 对照表

| 配置 | 效果 |
|------|------|
| 无文件 | **0ms，立即抢**（默认） |
| `{"delay_ms": "0"}` | 0ms |
| `{"delay_ms": "300"}` | 固定 300ms |
| `{"delay_ms": "10-200"}` | 每次 10～200ms 随机 |
| `{"delay_ms": "0-100"}` | 每次 0～100ms 随机 |

### PowerShell 示例

```powershell
cd Z:\wechat-pc-reverse-2\wx_hook_bridge

# 固定 300ms
Set-Content -Encoding utf8 bridge\control\hb_cold_delay.json '{"delay_ms":"300"}'

# 10~200ms 随机
Set-Content -Encoding utf8 bridge\control\hb_cold_delay.json '{"delay_ms":"10-200"}'

# 恢复最快：删文件或写 "0"
Remove-Item bridge\control\hb_cold_delay.json -ErrorAction SilentlyContinue
# 或: Set-Content -Encoding utf8 bridge\control\hb_cold_delay.json '{"delay_ms":"0"}'
```

改配置**不需要重新注入**，约 100ms 内生效。

### 日志确认

`bridge/control/hb_open_debug.txt`：

```text
[cold:FACTORY-RECV t=…] sendId=… delay_ms=173 cfg=[10,200]
```

- `delay_ms`：本次实际等待  
- `cfg=[min,max]`：当前解析出的区间  

---

## 日志

| 文件 | 内容 |
|------|------|
| `bridge/control/hb_open_debug.txt` | 冷抢主日志（`[cold:…]`） |

常见标签：`RECV-HB` → `FACTORY-RECV` → `COLD-RECEIVE` → `HB-RES` → `OPEN-BUILD` → `COLD-OPEN`。

### Fastlane 1581 分段计时

存在 `bridge/control/hb_cold_fastlane.enable` 时，还会安装三个只读计时 hook：

```text
[cold:trace-ready] receive timing installed: task 0x4A65050 | request-loop 0x4A68430 | attempt 0x4A68E80
[cold:TASK-START] sendId=... matched=1 ... launch_to_task_ms=...
[cold:REQUEST-BEGIN] sendId=... retry_count=0 timeout_ms=120000 task_to_request_ms=...
[cold:ATTEMPT-BEGIN] sendId=... attempt=1 task_to_attempt_ms=...
[cold:ATTEMPT-END] sendId=... attempt=1 total_ms=...
[cold:REQUEST-END] sendId=... total_ms=... attempts=1
[cold:TASK-END] sendId=... total_ms=... attempts=1
```

IDA 已确认 4.1.9.23 的 receive 调用点传入 `{retry_count=0, timeout_ms=120000}`。代码里虽然存在 `1000 << retry_index` 的退避分支，但当前 receive 配置不会进入该分支。

判读方法：

- `launch_to_task_ms` 很大：客户端 task 调度排队。
- `task_to_request_ms` 很大：请求构造或 request loop 前处理。
- `ATTEMPT-END total_ms` 很大：单次 CGI 发送/等待结果慢，包含长连接、网络和服务端等待。
- `TASK-START matched=0`：task 未绑定到本次 fastlane claim，后续 request/attempt 日志会被过滤，避免混入其他红包 CGI。

测试时可只看关键时间线：

```powershell
Get-Content .\bridge\control\hb_open_debug.txt -Wait |
  Select-String 'trace-ready|RECV-HB|FASTLANE|COLD-RECEIVE|TASK-|REQUEST-|ATTEMPT-|HB-RES|COLD-OPEN'
```

---

## 相关代码

| 项 | 位置 |
|----|------|
| 延迟解析 | `hongbao_cold.c` → `cold_json_get_delay_ms` / `cold_reload_delay_config` |
| 配置路径 | `paths.c` → `\\bridge\\control\\hb_cold_delay.json` |
| newsync 快车道 | `hongbao_cold.c` → `cold_newsync_addmsg_hook` / `cold_fastlane_scan_raw_vector` |
