# 任务管理数据服务

界面直接在原 shadcn-admin 侧栏“任务管理”中，不是独立站点。任务服务只负责统一存储，微信数据与代理保持原样。

## 当前使用流程

1. 普通成员填写 Git 邮箱即可进入，不用密码、邀请或验证码。
2. 成员创建任务，在“我创建”查看自己的草稿，创建人或所有者编辑、发布；发布后成员认领负责人、加入协作和填写进展。
3. 负责人提交成果说明及 PR 链接。
4. 所有者自行看 Git，回到任务页面确认完成或退回。

邮箱是自填署名，不验证归属；填写别人的邮箱会使用该普通成员署名。同一个邮箱会在去空格、转小写后对应同一成员。此方式不是邮箱身份认证，也不等价于私仓权限控制。拥有页面访问能力的人可填邮箱参与，这是当前明确采用的简单协作规则。

不能凭邮箱获得 owner 权限。已有 owner 会话保留，管理员凭据单独由操作终端初始化；页面的“所有者入口”调用 `/auth/owner-session`，仅接受 owner 管理凭据。普通成员入口仍只有邮箱，未初始化 owner 时也不允许首位邮箱成员自动当管理员。

## 普通本地部署

正常启动原后台即可，在本地 `/tasks` 页面使用联网任务。Vite 开发/预览和原 Docker Nginx 均默认连接 `https://jqr.47jsy.com`，不必部署本地任务 API 或数据库；不会跳转网页，也不会上传微信数据。访问地址使用 `http://localhost:端口` 或 `http://127.0.0.1:端口`。

本地代理仅发送任务请求和任务 Cookie，先校验本地 Origin，再由中心检查来源、CSRF 和权限。中心故障时显示错误，不自动切换成本地数据。Vite 遵循已有 `HTTPS_PROXY` 出站配置，不修改系统代理。

## 隔离后端开发

前端使用原 `frontend-next/src/main.tsx` 和 `vite.config.ts`。当前完整后台开发预览使用 5195，统一任务 API 使用 8795；5174 的已有微信容器不动。

```powershell
# task-center 目录
$env:TASK_CENTER_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/task_center'
$env:TASK_CENTER_PUBLIC_URL='http://127.0.0.1:5195'
$env:TASK_CENTER_LOCAL_HTTP='1'
python -m alembic upgrade head
# 仅首次由操作终端初始化管理员，不是普通成员的前置步骤
python -m app.bootstrap
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8795 --no-access-log

# frontend-next 目录
$env:TASK_CENTER_API_PROXY='http://127.0.0.1:8795'
pnpm exec vite --port 5195
```

上述覆盖只用于隔离后端开发。正常运行时不设置该覆盖，任务连接云端；其余 `/api` 仍走微信 API 8765。普通本地用户不需要中心管理员凭据。

本地预览数据库为 `jqr-task-center-preview-pg` 的命名卷，业务库 `task_center_browser_test` 由预览保留，不在测试时清空。owner 的本地测试凭据由 `tests.seed_local` 初始化，仅用于本地预览，不用于正式环境。真正管理员凭据通过操作终端安全输入，不能把账号密码写进前端。

## 测试

专用测试库必须为回环 55439 上的 `task_center_test` 或 `task_center_unit_test`；单测会清空这些库的 `tc_*` 表，不能放实际业务数据。

```powershell
# task-center 目录，先创建专用 task_center_unit_test 数据库
$env:TASK_CENTER_DATABASE_URL='postgresql+psycopg://postgres:task_center_test@127.0.0.1:55439/task_center_unit_test'
$env:TASK_CENTER_TEST_DATABASE_URL=$env:TASK_CENTER_DATABASE_URL
python -m alembic upgrade head
python -m pytest -q
python -m alembic check

# frontend-next 目录；先确保本地 API 与完整后台预览已运行
node tests/task-center.e2e.mjs
pnpm exec vitest run --config vitest.task-center.config.ts
pnpm exec tsc -p tsconfig.app.json --noEmit
```

浏览器脚本使用三个隔离会话：测试 owner 通过管理 API 建立已有权限，另外两人只填写邮箱进入，随后验证完整协作、退回、确认、原后台导航和停用/恢复。外部请求均拒绝，截图及报告放在 `artifacts/`，不入 Git。

## 身份与迁移

- `20261011_git_email` 新增可空、唯一的 git_email 字段，不删除旧密码、邀请、任务或操作记录。
- 不根据旧用户名猜测 Git 邮箱，不自动合并历史成员，更不根据邮箱授予 owner。
- 并发填写同一邮箱只生成一个成员，名称和大小写差异不会重复创建。
- 停用该邮箱会撤销会话，重新填相同邮箱仍不能进入；恢复后可再填写进入。该限制不阻止同一个人自报其他邮箱，不能将其声称为强身份封禁。
- 保留 HttpOnly Cookie、HTTPS Secure、CSRF、来源检查和请求限流；普通邮箱会话只有成员权限。
- 成员进入接口为 `POST /api/task-center/v1/auth/identify`，body 为 `{"email":"name@example.com"}`。原 `/auth/login`、`/auth/accept-invite` 和 `/invites` 已移除。
- 任务认领、多人协作、成果提交和 owner 手动确认规则不变。
- `20261011_task_creator` 增量添加独立创建人字段，只按创建/复制事件回填历史身份。创建人不自动成为负责人或管理员；他人的范围不可编辑，成果确认、退回和成员管理仍仅限 owner。

中心已按授权部署到 `jqr.47jsy.com`，本机预览数据库独立保留；没有 GitHub API、OAuth、Webhook、自动验收或后台对账。未提交、未推送。
