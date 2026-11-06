# Docker Desktop 部署

当前采用混合部署，不是将 Windows 微信采集器搬入 Linux 容器：

| 组件 | 运行位置 | 地址 |
| --- | --- | --- |
| 完整 shadcn-admin 与 Nginx API 代理 | Docker Compose `wechat-chat-panel` / `web` | `http://127.0.0.1:5174/chats` |
| Python 聊天 API、历史读取与媒体解析 | Windows 宿主 | `http://127.0.0.1:8765` |
| 已有微信桥接 | Windows 微信进程 | `http://127.0.0.1:19088` |
| 共享任务 API 与任务数据库 | 云端任务中心 | `https://jqr.47jsy.com` |

任务管理直接在本地后台 `/tasks` 使用，默认联网到统一任务中心，不用另外部署任务数据库或跳转网站。成员填写 Git 邮箱后共用同一批任务；微信接口、聊天数据与本机 Cookie 不上传到任务中心。Vite 开发/预览也使用同样默认值。

活动账号核验依赖 Windows 进程与打开的文件句柄，原生图片解码也依赖该微信进程；不能用 Linux 容器的进程列表替代。后端保留原配置、缓存、回执和账号隔离。微信源目录和密钥不复制到镜像，也不挂载进容器。

## 部署与更新

先保持当前 Windows API 和微信桥接运行，在面板根目录执行：

```powershell
.\scripts\deploy-container.ps1
```

脚本检查后端、账号和端口，使用宿主已有 pnpm 依赖进行 TypeScript/Vite 生产构建，再构建并启动容器。构建失败不会主动停止已有部署。5174 若被非本 Compose 服务占用，脚本会中止而不是杀进程。

镜像只接收 `frontend-next/dist`、上游许可证和 Nginx 配置；运行时不需要 Node.js。Windows 的 node_modules 不挂载给 Linux 使用。本次选择宿主构建，是因为容器内 npm registry 连接重置；没有关闭依赖校验或改用未验证镜像源。

```powershell
docker compose ps
docker compose logs --tail 50 web
docker compose exec -T web nginx -t
docker compose stop web
```

`restart: unless-stopped` 会在 Docker 恢复后恢复容器，但不会启动 Windows API 或微信；宿主服务仍需运行。`/healthz` 只代表 Nginx 就绪，端到端连接状态请看 `/api/chat/status`。停止 web 不会删除宿主聊天数据，也不会停止微信。

## 代理与安全

- 只发布 `127.0.0.1:5174`，没有开放局域网或公网访问。
- 通过 Docker Desktop 的 `host.docker.internal` 访问宿主 8765；上游 Host 固定为 `127.0.0.1:8765`，不放宽后端 TrustedHost 限制。
- `/api/task-center/` 独立转发到云端，验证 TLS 证书、保留真实 Origin，并在本地校验写请求的来源；仅转发独立任务 Cookie。其他 `/api/` 仍走上述本机微信后端。
- 保留真实 Origin，清除客户端注入的 X-Forwarded-For，不放宽后端发送来源/账号校验。
- SSE 关闭代理缓冲并保留长连接；媒体 Range 和下载头由后端透传。
- 关闭自动上游重试，避免结果未知的发送被代理重新提交。
- 非 root 用户、只读根文件系统、移除所有 capabilities，临时文件仅放在限额 tmpfs；没有宿主数据卷。
- Nginx 基础镜像固定 digest，日志轮转上限为 3 × 10 MB。

Docker Desktop 宿主访问机制参考：https://docs.docker.com/desktop/features/networking/networking-how-tos/

## 本次验收

2026-09-22：容器 `wechat-chat-panel-web-1` 为 healthy；生产构建和 `nginx -t` 通过。容器入口的 Dashboard、Chats、Tasks、Settings 深链接返回 200；当前账号历史分页连续两页各 60 条无重复；SSE 返回事件；图片 Range 返回 206 和实际图片字节。

发送保护只使用不匹配的虚构账号验证：外部 Origin 返回 403、错误账号返回 409，均在原生发送前拒绝，没有向真实联系人发消息。浏览器已看到完整导航、真实历史、头像及“实时接收中”。本次部署没有重启宿主 Python API、微信或重新注入 DLL。
