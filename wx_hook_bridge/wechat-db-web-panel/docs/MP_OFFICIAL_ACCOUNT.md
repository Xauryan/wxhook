# 公众号工具链

公众号接口只监听本机 API，统一前缀为 `/api/mp`。动态凭据只保存在
`.cache/mp_tokens.json`，状态接口只返回脱敏值。

## 本地测试

在工作台目录启动前后端：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1 -NoBrowser
```

浏览器打开 `http://127.0.0.1:5173/mp`。停止服务时按 `Ctrl+C`；也可以用
`-ExitAfterSeconds 30` 做短时冒烟。API 默认在 `http://127.0.0.1:8765`，只接受
回环请求。

建议按以下顺序验证：

1. 在微信客户端打开或刷新一篇公众号文章，等待工作台的 Token 状态更新；
   `key`、`uin`、`pass_ticket` 是核心字段，`appmsg_token` 是阅读指标辅助字段。
   使用 `/s/<slug>` 短链接时，同时确认动态 Token 卡片显示“短链身份 已捕获”。
2. 在“单篇指标”粘贴完整文章 URL 或 `https://mp.weixin.qq.com/s/<slug>` 短链接，点击“采集指标”。
3. 在“竞品监控”每行粘贴一个文章 URL，点击“开始批量采集”，检查成功/失败隔离和历史快照。
4. 在“无损备份”选择 Markdown、HTML 或 PDF，点击“开始备份”，下载生成的 ZIP 并检查 `manifest.json`。

短链接会先从文章 HTML 解析 `biz/mid/idx`。如果微信只返回会话空壳，服务会回退到 Native
`batchgetappmsgh5` 事件中与该短链接精确匹配的公开文章身份；身份写入内存后保留 15 分钟，
最多 256 条，不保存原始事件、Cookie 或 Token。如果工作台仍显示“短链身份
待捕获”，先在微信客户端重新打开或刷新目标文章再试。若返回 HTTP 200 但没有
`appmsgstat`，说明微信上游只返回了元数据
壳，通常是会话已过期、`appmsg_token` 尚未更新或上游策略降级；刷新文章后重试，或将
客户端拦截到的 `getappmsgext` JSON 通过单篇接口的 `appmsgext` 字段提交。

## 指标采集

单篇接口 `POST /api/mp/articles/metrics` 优先解析调用方拦截的文章 HTML、
`getappmsgext` JSON/JSONP 和评论响应；缺少拦截数据时，传入文章 URL 后会使用
当前有效会话自动请求微信接口。响应中的核心字段为 `read_count`（真实阅读量）、
`wow_count`（在看）、`like_count`（点赞）和 `comment_count`（评论总数），并附带
`metric_basis`、`sources` 和 `warnings` 说明数据来源与降级情况。

```powershell
$body = @{
  url = 'https://mp.weixin.qq.com/s?__biz=...&mid=...&idx=...&sn=...'
  fetch_missing = $true
  include_comments = $true
  comment_limit = 100
  max_comment_pages = 3
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/mp/articles/metrics `
  -ContentType 'application/json' -Body $body
```

竞品监控使用 `POST /api/mp/articles/metrics/batch`，一次最多 50 个 URL，
并发范围为 1-8（默认 4）。每个 `items` 元素保留输入序号；失败项只影响自身，
错误放在结构化的 `error` 字段中。成功数据放在 `data` 中，避免把带评论的文章
内容重复编码。成功的批量快照可从 `GET /api/mp/articles/history` 按 URL 过滤，
当前历史索引保存在 API 进程内存中，重启后会清空。

## Token 捕获

原生浏览器事件日志 `bridge/control/browser_events.jsonl` 由后台 tailer 从启动时
的文件尾部增量读取。Token 不会重放历史日志；启动时只会额外扫描最近 2 MiB 日志中的
公开短链身份，用于恢复 `/s/<slug>` 对应的 `biz/mid/idx`，不会从该扫描持久化或更新
任何 Token。
浏览器/CDP 也可以把 `Network.requestWillBeSent` 或
`Network.requestWillBeSentExtraInfo` 原始事件投递到：

```powershell
$event = @{
  method = 'Network.requestWillBeSent'
  params = @{ request = @{ url = 'https://mp.weixin.qq.com/s?key=...&uin=...'; headers = @{ Cookie = 'pass_ticket=...' } } }
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/mp/tokens/cdp `
  -ContentType 'application/json' -Body $event
```

也可向 `/api/mp/tokens/ingest` 投递已有请求的 `url`、`headers`、`cookies` 和
`body`。除了核心 `key`、`uin`、`pass_ticket`，捕获器还会从公众号请求 URL、
Cookie 和 Native `webcgi_header` 中提取指标辅助字段 `appmsg_token`。核心完整性仍只
检查前三项；`GET /api/mp/tokens` 会在 `auxiliary.appmsg_token` 中返回是否存在及
首尾脱敏片段，不会把任何原始 Token 返回给浏览器。该状态接口还会在 `sources` 中返回
`native_article_context_available`、`native_article_context_count` 和最近捕获时间，
但不会返回文章身份值或 Native 原始载荷。

原生或 Frida hook 如果已经拿到一段内存字节，可通过
`POST /api/mp/tokens/memory` 交给有界提取器。请求只接受回环地址、
`application/octet-stream` 或 `text/plain`，单次最多 1 MiB；服务只扫描这段
调用方提供的 UTF-8/UTF-16 快照，不会遍历 API 进程地址空间：

```powershell
$snapshot = [Text.Encoding]::UTF8.GetBytes('key=...&uin=...&pass_ticket=...')
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/api/mp/tokens/memory' `
  -ContentType 'application/octet-stream' -Body $snapshot
```

## 无损导出

```powershell
$body = @{ url = 'https://mp.weixin.qq.com/s?...'; formats = @('markdown','html','pdf') } |
  ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/mp/exports `
  -ContentType 'application/json' -Body $body
```

导出服务会设置文章 Referer、Origin 和桌面 User-Agent，使用当前有效 Cookie；
图片、视频和 poster 下载到 `assets/`，同一资源去重，失败资源保留脱敏远程地址并
写入 `manifest.json`。结果同时生成 Markdown、独立 HTML、PDF（优先 Chromium，
其次 wkhtmltopdf；不可用时使用 ReportLab 文本回退，最后使用最小 PDF 文本回退）和
`backup.zip`。导出物中的动态凭据查询参数会被移除；使用文本回退时会在响应和
`manifest.json` 的 `warnings` 中明确标记。

`start_mp_services()` / `stop_mp_services()` 应在 FastAPI 应用 lifespan 中调用；
父应用可直接 include `backend.app.mp_router.router` 一次，它会聚合指标、Token 和导出路由。
