# 交接：微信 4.1.9.23 本地视频朋友圈首次发布

## 0. 目标与边界

目标是在注入式 bridge（`native/wx_hook_bridge`）中，把本地 MP4 作为一条新的朋友圈发布，并以 CGI 209 `/cgi-bin/micromsg-bin/mmsnspost` 的官方结果作为成功依据。

只分析和实现正常首次发布链，明确排除：

- failed-post resend/retry。
- 主动调用 `sub_184CAFF00`（`POST_CONTINUE`）。
- 强制调用 `POST_RUN(post, 2)` 或轮询推进 phase。
- 强制写 `post+576`。
- UI/桌面自动化。

## 1. 已确认结论

媒体模型由 bridge 设置 `PublishModel+24 = 1`。`sub_185559170` 精确返回该字节，因此正常首次发布必须走非零分支：

```text
sub_183E43E80(PublishController)
  -> sub_185559170(controller) 读取 *(model+24)
  -> sub_185558B70(controller)
       -> controller virtual +0x78
       -> 解析 publish context
       -> sub_183B1B290(context, shared_ptr<PublishModel>*)
            -> 构造 off_18870D648 immediate closure
            -> 调度 sub_183B26140(closure)
                 -> 读取 context 的 kSnsTimeLineSessionKey
                 -> sub_182BE2D70(...)
                 -> sub_182BB0F00(...)
                 -> sub_182BE2100(manager, shared_ptr<PublishModel>*)
                      -> 新建并注册 SnsPostObject
                      -> sub_184CAD900(post, 1)
                      -> sub_184CAD900(post, 2)
                      -> CDN 上传完成回调进入 sub_184CAD900(post, 3)
                      -> sub_184CB0920(post, out)
                      -> sub_184CB2CC0(... CGI 209 ...)
```

`sub_183B1AEB0 -> sub_183B542D0 -> sub_183B547B0` 属于 `model+24 == 0` 的可观察分支，不是当前媒体模型的首次发布入口。源码只保留了这组函数的可选 trace hook，没有主动调用它们。

`sub_184CAFF00` 属于已有 post 的 continue 路径。bridge 没有调用它，也没有通过重发工作流制造可继续的失败 post。

## 2. 静态证据

### 2.1 分支与 controller

- `sub_185559170(a1)` 返回 `*(BYTE *)(*(a1+88)+24)`。
- `sub_183E43E80` 在该返回值非零时调用 `sub_185558B70`。
- `sub_185558B70` 先调用 controller 虚表 `+0x78`，再通过全局对象虚表 `+0x60`、下一层虚表 `+0x30` 和 `sub_1801A7A30` 取得 publish context，最后调用 `sub_183B1B290`。
- 当前 `model+52 == 0` 时，UI adapter 由 `sub_183E30040` 构造，vtable 是 `off_188764F08`；其 `+0x78` 精确指向 `sub_183E38F50`。
- `sub_183E38F50` 只把 adapter 字段同步到 PublishModel、创建媒体对象并替换 `model+280` 媒体向量，没有发布服务注册或 context 初始化副作用。

bridge 已使用官方 PublishModel 构造函数并直接填充上述模型字段和媒体对象，所以不需要伪造 controller，也不需要额外调用虚表 `+0x78`。

### 2.2 immediate closure

`sub_183B1B290` 构造的 closure 使用 `off_18870D648`：

```text
+0x00  vptr = VA 0x18870D648
+0x08  PublishModel*
+0x10  PublishModel control block*
+0x18  publish context*
+0x20  auxiliary object*
+0x28  auxiliary control block*
```

`off_18870D648` 的前三项是：

```text
sub_183B26080
sub_183B260E0
sub_183B26140
```

因此虚表 `+0x10` 是 immediate worker。`sub_183B1B290` 会复制并持有传入的 model/context，调用返回后 bridge 可以释放自己取得的 context 强引用。

### 2.3 immediate worker

`sub_183B26140` 的关键调用点：

- 从 `*(context+264)` 取得状态对象并读取 `kSnsTimeLineSessionKey`。
- 调用 `sub_182BE2D70` 和 `sub_182BB0F00` 完成发布前注册准备。
- 在 `0x183B26BD4` 调用 `sub_182BE2100`。

已核对的 hook prologue：

```text
183B1B290: 55 41 57 41 56 41 55 41 54 56 57 53
            48 81 EC F8 09 00 00
            48 8D AC 24 80 00 00 00

183B26140: 55 41 56 56 57 53
            48 81 EC 80 02 00 00
            48 8D AC 24 80 00 00 00
```

## 3. bridge 实现

相关文件：

- `native/wx_hook_bridge/src/include/wx_bridge_offsets.h`
- `native/wx_hook_bridge/src/modules/sns_publish.c`

首次发布使用的新增偏移：

```c
WX_OFF_SNS_PUBLISH_IMMEDIATE_SCHEDULE   0x3B1B290
WX_OFF_SNS_PUBLISH_IMMEDIATE_WORKER     0x3B26140
WX_OFF_SNS_PUBLISH_IMMEDIATE_CLOSURE_VT 0x870D648
```

当前启动逻辑：

1. 使用官方构造函数创建 PublishModel，并为本地视频设置 `model+24 = 1`。
2. 按官方 `sub_185558B70` 的虚表和 interface-tag 调用序列解析 publish context。
3. 调用 `sub_183B1B290(context, task->shared_model)`，不手工构造 closure。
4. `sub_183B26140` hook 按 closure 中的 model 指针匹配当前任务，并在 worker 返回时记录 `videoWorkerCompleted`。
5. `sub_182BE1BA0` hook 只为匹配 model 的官方 post 补齐输入视频、缩略图和最终输出 MP4 三个宽字符串，并保留该 post 的 shared_ptr。
6. 官方状态机自行推进 phase 1/2/3；bridge 只观察，不调用 phase 2/continue。

builder hook 不再写 `post+576`，也不承担 context 注册。注册副作用由 `sub_183B26140` 在进入 `sub_182BE2100` 前完成。

## 4. 完成判定

completion hooks 观察：

- `sub_182BE1BA0`：捕获本次 model 对应的 post。
- `sub_184CAD900`：记录 phase 1/2/3。
- `sub_184CB0920`：记录官方 build/send 结果和 response。
- `sub_184CB2CC0`：确认 CGI 209 路径被调用并记录同步错误。
- `sub_183B26140`：记录 immediate worker 返回。

结果 JSON 诊断字段包括：

- `videoWorkerScheduled`
- `videoWorkerCompleted`
- `videoWorkerContext`
- `phase1Seen`、`phase2Seen`、`phase3Seen`
- `postBuildSendSeen`、`cgi209Seen`、`cgiSyncError`
- `officialResultSeen`、`officialResultCode`、`responseSeen`
- `videoOutputPath`、`postOutputAttr`

本地视频只有在 matched phase 3 返回后同时满足以下条件才置为成功：

```text
postBuildSendSeen == true
cgi209Seen == true
cgiSyncError == false
officialResultSeen == true
officialResultCode == 0
responseSeen == true
```

`videoWorkerCompleted` 是 worker 生命周期诊断，不代替 CGI 209 的服务端结果判据。

## 5. 已排除的旧实现

旧实验曾直接调用 `sub_182BE2100`，随后强制 phase 2 并循环 `sub_184CAFF00`。该路径虽然触发过 CDN 上传，但它绕过首次发布 worker，仍然不能作为正常发布实现。后续静态和运行时证据已经证明其中的 `postError460=-93` 是输出路径错误，不能再用它推断 context/session 缺失。

另一版误把 `sub_183B542D0 -> sub_183B547B0` 当成媒体模型首发 worker，并手工构造 `off_188712548` closure 和 232 字节 completion bundle。`model+24` 分支证据已否定该方向，相关主动调用和 bundle 已从源码删除。

以下旧产物来自错误中间状态，禁止注入：

```text
SHA-256 07AD2E4EB93D5F4DFCD0B37D3B648A976EBF93B1C6B62491243D119618A6CA0C
```

## 6. 当前构建产物

MSVC 全量构建已通过：

```text
文件    dist/wx_hook_bridge.dll
大小    893440 bytes
时间    2026-07-26 20:01:16
SHA-256 ACD8366F72E8E37B3DF28576C1C951C5E35C55BF000A1FD2C0C5CFE3F93C9BF8
```

该 DLL 包含 `post+384` 输出文件语义修正。2026-07-26 20:06:43 已通过热交接注入微信 PID `39368`；注入副本
`wx_hook_bridge.1785067602631.dll` 的 SHA-256 与该构建产物一致。

## 7. 修正前首次发布实测

用户允许后只提交了一条新帖子，没有调用 resend/retry，也没有第二次提交：

```text
content  视频发布测试 2026-07-26 19:26:40
result   bridge/control/result_12501118739369.json
post     0x1ca10096dc0
```

正常首发链全部命中到 phase 1：

```text
sub_183B1B290 -> sub_183B26140 -> sub_182BE2100 -> sub_184CAD900(post, 1)
```

结果为 `postError460=-93`、`phase2Seen=false`、`cgi209Seen=false`。这次失败没有发送 CGI 209，因此不是服务端拒绝。

## 8. `-93` 精确根因与修正

`sub_184CB02B0` 调用：

```text
sub_185431B20(out, post+320, post+384, options)
```

参数语义已经由静态调用者和运行时文件行为确认：

- `post+320` 是输入 MP4。
- `post+384` 是 phase 1 转码完成后的最终输出 MP4 文件，不是目录。
- `sub_185431B20` 在输出文件旁生成 `*._tmp.mp4`，异步转码完成后调用 `MoveFileExW(temp, output, 3)`。
- `0x1854374FE` 在临时文件存在但 `MoveFileExW` 失败时回写 `-93`。

旧 bridge 把 staging 目录写入 `post+384`。本次运行实际产生了：

```text
C:\Users\苏世奇\AppData\Roaming\Tencent\xwechat\sns_publish_bridge\
publish_1785065204_1785065200_39368_1250111871785065200594404._tmp.mp4
```

临时文件大小为 `193312` 字节，证明转码已经完成；随后将它移动到一个已存在的目录路径时失败，因而返回 `-93`。当前源码修正为：

```text
post+320        = staged input MP4
media+40        = staged input MP4
post+384        = unique final output MP4
media+72        = same final output MP4
```

最终输出路径在模型扫描时尚不存在，因此仍会进入 fresh-source 上传分支；phase 1 完成后，phase 2 可从 `media+72` 使用转码结果。

## 9. 修正后首次发布验证

用户明确允许后，只提交了一条全新的首次发布测试；没有重发失败帖，没有调用 resend/retry、`POST_CONTINUE`、强制 phase 或 UI 自动化：

```text
提交时间  2026-07-26 20:08:03
content   视频发布测试 2026-07-26 20:08:03
result    bridge/control/result_12749431239369.json
clientId  publish_1785067687_1785067683
耗时      约 1.9 秒
```

正常首次发布链完整命中：

```text
sub_183B1B290 -> sub_183B26140 -> sub_182BE2100
-> sub_184CAD900 phase 1/2/3 -> sub_184CB0920 -> CGI 209
```

成功判据全部满足：

```text
phase1Seen=true
phase2Seen=true
phase3Seen=true
postBuildSendSeen=true
cgi209Seen=true
cgiSyncError=false
officialResultSeen=true
officialResultCode=0
responseSeen=true
postError460=0
phase1ResultCode=0
phase2ResultCode=0
phase3ResultCode=0
```

phase 1 生成的最终输出文件为：

```text
C:\Users\苏世奇\AppData\Roaming\Tencent\xwechat\sns_publish_bridge\
publish_1785067687_1785067683_39368_127494312\sns_video_test.mp4.publish.mp4
```

该文件大小为 `193312` 字节，phase 2 的 CDN 上传参数使用的主文件正是此路径，说明 `post+384`/`media+72` 修正生效。官方 CGI 209 返回 `0`，服务端发布确认成功。

## 10. 当前运行状态

`127.0.0.1:19088` 当前由微信 PID `39368` 中 base `0x7FFF9C1C0000` 的修正版 worker 接管。进程仍映射若干旧 DLL 文件，但其 worker 已被热交接机制 supersede，当前 owner 记录为：

```text
39368:0:7fff9c1c0000
```

不要回到 resend、`POST_CONTINUE`、强制 phase 或 `183B542D0` 链。
