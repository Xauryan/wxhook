# 关系分析

本机入口：`http://127.0.0.1:5173/analysis/relationships`。侧栏位于「数据分析 → 关系分析」。

显示当前微信账号的群关系图、群对交集、跨群群友与全部当前群。按群名、群友名称或微信 ID 搜索；点击连线或交集排名查看共同成员，点击成员查看共同加入的群。默认概览显示交集至少 5 人的群关系，可降低阈值或显示独立群，全部明细及导出保留完整数据。

「重新分析」重新读取当前账号；「导出群与成员」下载 ZIP，包含离线 HTML、完整 JSON、全部群、群成员、群交集、跨群群友 CSV 和来源说明。CSV 为 UTF-8 BOM，疑似公式文本加前导单引号，原值保存在 JSON。页面也支持单独导出 JSON 与当前 SVG 图。

## 数据口径

- 当前群：`username` 以 `@chatroom` 结尾、`delete_flag=0`、`is_in_chat_room=1`。包含 `local_type=1` 的实际加入群，不仅限于 `local_type=2`。
- 成员 ID：`chatroom_member.room_id/member_id` 关联 `name2id.rowid`，再取得 `username`。不按联系人表 `contact.id` 关联，不按重名合并。
- 群交集：两个当前群的本机成员 ID 集合交集，去重并排除当前账号。
- Jaccard：共同群友人数除以两群群友并集人数，均排除本人。
- 成员名称：备注、昵称、微信号、ID，依次取首个非空值。没有名称则显示原 ID。
- 所有非零交集均进入明细与导出，图的显示阈值只影响可视范围。
- 非当前群记录保留在「全部群.csv」并标明原始状态，不混入当前关系图。

成员完整性仅覆盖当前本机保存的数据，未查询服务器完整名单。界面显示读取时间；缺少名称、成员关联缺失、名单缺少本人等状态分别处理，不能把本机完整导出当成服务器同步验收。

## 读取与账号边界

复用 `ActiveWechatRoster` 核验桥接端口所属微信进程、进程创建时间、实际打开的 `contact.db` 及账号目录。解密当前源库和已提交 WAL 到 `.cache/relationship-analysis` 临时目录，通过 HMAC、源指纹前后核对与 SQLite `quick_check`，计算后删除临时明文副本。源库只读，不读取消息正文。

Windows 句柄枚举和解密计算放在独立子进程，避免阻塞 API。缓存最长 60 秒且绑定账号、微信 PID/创建时间、源库/WAL 指纹。读取、缓存复用和导出前后均核对当前账号。换号或连接丢失时，前端卸载旧图并取消账号查询。

## 接口与实现

- `GET /api/chat/relationships?account=...&refresh=true`：完整 JSON。
- `GET /api/chat/relationships/view?account=...&refresh=true`：离线交互 HTML。
- `GET /api/chat/relationships/export?account=...`：ZIP 导出。

均为本机读取接口，返回 `Cache-Control: no-store`。页面使用独立 SVG 视图，通过沙箱 frame 承载，允许脚本与本地下载，不允许同源权限；名称与脚本数据分别转义，页面不加载外部字体、头像或脚本。

后端：`backend/app/relationship_analysis.py`、`relationship_view.html`；前端：`frontend-next/src/features/relationship-analysis`。专项测试覆盖真实 ID 关联规则、保存群、退出与删除状态、本人排除、重名、重复行、空数据、缺失 ID、HTML/CSV 转义、ZIP 读回和账号隔离。前端测试覆盖导航、切换账号、断开连接、接口错误、响应类型、导出错误及成功状态。
