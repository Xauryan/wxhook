# 新对话任务提示词

将以下内容完整作为新 Codex 对话的首条消息：

---

你现在位于仓库 `D:\苏世奇的AI日记\4.1.9.23`。请直接在 `wechat-db-web-panel` 目录内完成一个可运行的“微信本地数据库可视化”Web 面板。不要只输出计划或静态原型，必须持续实现、启动、测试、截图比对和修正，直到五个页面全部可用。

## 第一优先级要求

1. 五张参考图是视觉唯一事实来源，目标是固定桌面视口下达到截图级、一比一复刻：布局、尺寸、字体、颜色、圆角、阴影、边框、图标、图表、间距、对齐和交互状态都要逐项匹配。
2. 正式界面必须默认使用当前工作区内已解密的真实微信数据，不得把参考图里的“韩亮”、842、56 等示例姓名、头像或数字硬编码进最终页面。
3. 不得把参考图直接设为背景，不得切图覆盖页面，不得用 canvas 绘制整张截图冒充实现。所有区域必须由真实 DOM、SVG、图表组件和真实数据组成。
4. 数据库只能以只读方式打开。不得修改、迁移、vacuum、checkpoint 或删除 `runtime/db_decrypt/all` 下的任何文件。派生索引和缓存必须写入项目自己的 `.cache/`。
5. 应用只监听 `127.0.0.1`，不得上传联系人、消息、头像或媒体，不得接入遥测、云分析或第三方数据服务。
6. 遇到参考图没有定义的细节，先保证 1448×1086 桌面基线；桌面完成后再补响应式。不要用通用后台模板、默认 shadcn 外观或“差不多”的组件替代原图。
7. 除非存在无法自行解决的真实阻塞，不要停下来询问用户。先检查本地文件、数据库 schema 和现有工具，自主完成实现与验证。

## 开始前必须读取

- 仓库根目录的 `AGENTS.md`
- `wechat-db-web-panel/REFERENCE_MANIFEST.md`
- 五张原图：`wechat-db-web-panel/reference-images/*.png`
- 解密报告：`runtime/db_decrypt/decryption_report.json`
- FTS 查询实现：`python/tools/wechat_fts_query.py`
- 数据库解密与校验实现：`python/tools/wechat_db_decrypt.py`
- 原生 tokenizer：`native/weixin_fts_tokenizer/weixin_fts_tokenizer.c`

如果环境提供 `ui-ux-pro-max` skill，使用它做测量、交互、无障碍和最终 UX 检查，但参考图始终覆盖通用设计建议。

## 参考图与路由

- `01-界面一参考图.png` -> `/overview`，概览页
- `02-界面二参考图.png` -> `/contacts`，联系人页
- `03-界面三参考图.png` -> `/media`，媒体页
- `04-界面四参考图.png` -> `/contributions`，贡献页
- `05-界面5参考图.png` -> `/conversations`，会话页

共享导航还包含搜索和设置。实现 `/search` 和 `/settings` 的可用页面或抽屉，视觉保持同一体系，但五张参考路由优先。

## 推荐技术栈

优先采用以下本地双端架构；只有经过验证能更可靠地读取 FTS 扩展时才允许调整：

### 前端

- React + TypeScript + Vite
- React Router
- TanStack Query
- Apache ECharts + `echarts-wordcloud`
- TanStack Virtual，用于长消息列表
- Lucide React，统一 1.75–2px 线宽；禁止用 emoji 充当结构图标
- 自定义 CSS variables/CSS modules 或受控 Tailwind utilities。不要套通用组件库主题
- 字体优先 `Noto Sans SC`，回退 `Microsoft YaHei UI`, `Microsoft YaHei`, `PingFang SC`, sans-serif
- Playwright 做固定视口截图和交互测试

### 后端

- Python FastAPI + Uvicorn
- Python 标准库 `sqlite3`，因为当前 64 位 Python SQLite 3.45.1 已验证能加载 tokenizer 扩展
- `zstandard` 解压 WCDB 压缩消息
- `lxml` 或安全 XML 解析器解析 app message、文件、链接和引用结构
- Pillow 处理本地头像和缩略图
- `jieba`/TF-IDF 生成真实关键词与主题分布
- 可选 `rapidocr-onnxruntime` 对真实图片执行本地 OCR；未识别时显示真实空状态，不能生成假 OCR 文本
- pytest 做数据层和指标公式测试

提供根级启动脚本，例如 `scripts/dev.ps1`，使用户能在 PowerShell 一条命令启动 API 与 Web。最终 README 必须给出安装、启动、构建和测试命令。

## 现有真实数据

数据库根目录：`runtime/db_decrypt/all`

当前快照已确认：

- 20 个数据库已解密，唯一未解的是 `message/weclaw.db`，不影响这五个核心页面
- 195 个 `Msg_*` 会话消息表
- 48,092 条消息
- `contact.contact` 有 2,508 条联系人记录
- `session.SessionTable` 有 289 条会话记录
- `contact.chat_room` 有 46 个群记录
- `message_resource.MessageResourceInfo` 有 5,402 条资源索引
- `head_image.head_image` 有 1,198 个本地头像
- 三个 FTS 库的 `quick_check` 和 `integrity_check` 均为 `ok`

这些数字只能作为开发时的完整性基线，页面显示必须由查询实时计算，不能直接写死。

### 数据源映射

- 账号及联系人：`contact/contact.db`
  - `contact`
  - `chat_room`
  - `chatroom_member`
  - `contact_label`
- 当前会话摘要：`session/session.db`
  - `SessionTable`
- 历史消息：`message/message_0.db`
  - `Name2Id`
  - `Msg_<md5(username)>`
- 文件、图片、视频资源索引：`message/message_resource.db`
  - `MessageResourceInfo`
  - `MessageResourceDetail`
- 语音：`message/media_0.db`
  - `VoiceInfo`
- 本地头像：`head_image/head_image.db`
  - `head_image(username, md5, image_buffer, update_time)`
- 真实文件名与硬链接索引：`hardlink/hardlink.db`
  - `image_hardlink_info_v4`
  - `video_hardlink_info_v4`
  - `file_hardlink_info_v4`
- 收藏：`favorite/favorite.db`、`favorite/favorite_fts.db`
- 全文搜索：
  - `message/message_fts.db`
  - `contact/contact_fts.db`
  - `favorite/favorite_fts.db`

账号 ID 从 `runtime/db_decrypt/decryption_report.json` 的 `account` 字段动态读取，再到 `contact.contact` 查询昵称、别名和头像。不要硬编码当前账号昵称。

### 数据库和消息解析规则

1. 所有连接使用 URI：`mode=ro&immutable=1`，连接生命周期显式关闭。
2. `message_0.db.Name2Id.user_name` 对应消息表名：`Msg_` + `md5(user_name UTF-8)`。只处理真实存在的表。
3. 消息基本字段包括 `local_id/server_id/local_type/sort_seq/real_sender_id/create_time/source/message_content/compress_content/packed_info_data`。
4. `local_type` 是 64 位组合值：保留原值，同时计算 `base_type = local_type & 0xffffffff`、`app_subtype = local_type >> 32`。不要把高位子类型丢失。
5. `message_content` 可能是文本，也可能以 Zstandard magic `28 B5 2F FD` 开头。必须真实解压、解码；失败时保留结构化错误状态，禁止伪造内容。
6. 群消息文本可能带 `sender_username:\n` 前缀。解析发送者并通过联系人表或 `Name2Id` 解析显示名。
7. app message/XML 要提取文件、图片、视频、网页链接、引用消息等真实字段，并保留无法解析的原始类型信息。
8. 引用消息重点识别 `base_type=49` 且 `app_subtype=57`，解析 `<refermsg>` 内的 `svrid/fromusr/chatusr/displayname/content`，通过 `server_id` 回连原消息。
9. 头像优先读取 `head_image.image_buffer`；不存在时使用数据库中的头像 URL；网络不可用时显示由真实姓名生成的稳定占位头像。
10. FTS 扩展：`dist/weixin_fts_tokenizer.dll`。加载前设置：
   - `WEIXIN_DLL_PATH=D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll`
   - 每个 SQLite connection 都需要单独加载扩展
11. 联系人拼音查询必须沿用 `wechat_fts_query.py` 的两阶段方案：拼音 FTS 只取 rowid，再从 `contact_fts_v4_content` 读取内容；不得对 `contact_fts_pinyin_v4` 请求 `rank/bm25`。
12. 不要使用本机 32 位 `sqlite3.exe` 加载 x64 扩展。使用已验证的 64 位 Python。

## 指标真实性规则

所有指标都必须在后端有明确、可测试的计算公式，并在文档 `docs/METRICS.md` 中记录。没有可靠来源时显示空状态或改变标签，不得编造。

- 消息总数：所有真实 `Msg_*` 行数或当前筛选范围内行数。
- 活跃天数：按本地时区转换 `create_time` 后的 distinct date 数。
- 最近活跃：最大真实 `create_time`。
- 常聊联系人：按 session 聚合真实消息数，排除系统账号的规则要写入文档。
- 消息类型分布：依据 `base_type/app_subtype` 的真实分类。
- 联系人增长：数据库缺少明确添加时间时，使用“首次互动日期”并把 UI 标签写成“首次互动联系人增长”，不能误称真实新增联系人。
- 响应时长：优先只统计一对一会话；在对方消息后找到下一条本人回复，排除超长离线间隔，阈值写入文档。
- 会话次数：同一 session 相邻消息间隔超过 30 分钟切分 episode；阈值可配置。
- 会话轮次：连续同一发送者的消息合并为一轮。
- 高频时段：按小时真实聚合，可计算连续 2 小时窗口。
- 关系强度：综合消息数、活跃天数、最近互动和双向比例，公式必须确定且可解释。
- 有效贡献/高质量消息：只有在实现透明的确定性评分后才显示；评分必须记录在文档和 tooltip 中。
- 被引用：统计被其他消息真实 `<refermsg>` 指向的原消息，支持跳转引用链。
- 关键词/主题：只基于真实可解码文本，停用词和算法写入文档。
- OCR：只展示真实 OCR 结果及置信度，不能沿用参考图票据内容。

## 五页视觉与功能要求

### 1. 概览 `/overview`

复刻参考图一：

- 当前账号资料卡和四项 KPI
- 近 30 天消息柱状图，具备原图式 hover tooltip
- 常聊联系人排行，真实头像、摘要和消息量
- 消息类型环图和精确图例
- 最近消息预览
- 12 个月年度活跃热力图，支持选中日期并下钻会话页
- 顶栏搜索、时间筛选和侧栏状态卡均可用

### 2. 联系人 `/contacts`

复刻参考图二：

- 当前账号资料/KPI 卡
- 以当前账号为中心的真实关系网络，节点、连线颜色和粗细编码关系强度
- 高频联系人 TOP6
- 双向互动强度环图
- 首次互动联系人增长趋势
- 响应速度分布
- 关键联系人表，包含强度条、消息数、互动频率、最近活动和真实可判断的互动渠道
- 网络节点、表格行可点击进入对应会话筛选

### 3. 媒体 `/media`

复刻参考图三：

- 图片、视频、文件、链接真实数量
- 真实媒体缩略图预览；资源缺失时显示缺失状态，不得用参考图素材
- 视频播放、图片灯箱、文件打开位置或下载只读副本
- 高频关键词词云/列表切换
- 内容主题环图
- 文件列表
- 内容搜索与上下文片段
- 链接分享记录
- 对真实图片执行本地 OCR，显示识别时间、置信度和复制操作

通过 `hardlink.db`、`message_resource.db` 和解密报告的原始账号目录查找实际媒体文件。只读访问；找不到时保留索引信息。

### 4. 贡献 `/contributions`

复刻参考图四：

- 年度发言量/有效贡献/被引用切换
- 53 周年度热力图、月份与星期标签、六级色阶、选中格双描边
- 年度 KPI：消息数、活跃日、最长连续、被引用次数
- 年份前后切换和今天按钮
- 选中日期的活跃时间段、发言、轮次、有效贡献、被引用、主动引用
- 当天消息摘要和当天洞察
- 点击“查看当天消息”跳到会话页并带日期过滤参数

### 5. 会话 `/conversations`

复刻参考图五。注意这是账号级会话总览，不是某一个联系人的详情页：

- 顶部账号画像和真实会话 KPI
- 搜索、日期范围、会话范围、内容类型、排序和导出筛选条
- 按日期分组的 episode 时间线：时间、轨道绿点、头像、会话名、摘要、媒体预览和轮次 pill
- 右侧消息活跃时段热度条
- 近 30 天会话频次柱状图
- 高频被引用消息 TOP5
- 点击 episode 打开完整消息上下文抽屉
- 点击图表反向筛选时间线
- 引用消息可跳到原消息并显示引用链
- 长列表虚拟化，日期标题吸顶
- 导出当前筛选为 CSV/JSON/HTML

## 像素级复刻流程

1. 逐张用原始分辨率查看参考图，建立 `docs/MEASUREMENTS.md`，记录每个页面：外壳、顶栏、侧栏、主内容起点、每张卡的 x/y/w/h、内边距、字体、颜色、阴影和圆角。
2. 先固定 `1448×1086`、DPR 1、浏览器缩放 100% 完成桌面版。各页面可以用路由级 CSS variables 保留参考图之间的细微壳层差异。
3. 使用 Playwright 对每个路由截图，输出到 `artifacts/screenshots/`。
4. 使用像素 diff/半透明 overlay 与对应参考图比较，输出 `artifacts/diffs/`。动态真实文本不同不构成硬编码示例数据的理由；重点迭代几何、样式、图表位置和视觉密度。
5. 不能只比较肉眼。每次调整后重新截图，直到没有明显布局漂移、卡片错位、颜色偏差、字体层级偏差或图表比例错误。
6. 桌面基线完成后实现响应式：1024、768、375 宽度，无横向滚动，移动端保留核心功能。
7. 尊重 `prefers-reduced-motion`，键盘可达，图标按钮有 aria-label，图表有文字摘要。无障碍改进不能破坏桌面基线视觉。

## 数据服务与性能

- 首次启动可扫描 195 个消息表并生成项目内 `.cache/analytics.sqlite`，但缓存只能保存真实派生结果。
- 缓存键必须包含源数据库路径、大小、mtime 和 SHA/快照标识；源变化时自动失效。
- 48,092 条消息规模不大，但不要在每个请求重复 UNION 195 张表。
- 提供统一 normalized message model 和可测试的 repository/service 层。
- API 至少提供 profile、overview、contacts、relationship graph、messages timeline、media、contributions、search、avatar/resource 等端点。
- API 返回明确的数据质量字段，例如 `decoded`, `resource_available`, `metric_basis`, `snapshot_time`。
- 页面底部状态卡不能无条件写“实时”。从解密报告显示真实的快照时间、完整性状态和 WAL/base-only 状态。

## 必须交付

- 完整前后端源码
- `README.md`：安装、启动、构建、测试、数据路径和隐私说明
- `docs/ARCHITECTURE.md`
- `docs/DATA_SCHEMA.md`
- `docs/METRICS.md`
- `docs/MEASUREMENTS.md`
- PowerShell 一键开发脚本
- pytest、Vitest 和 Playwright 测试
- 五页最终截图及 diff 产物
- 不修改五张参考图
- 不修改解密数据库

## 完成判定

只有同时满足以下条件才算完成：

1. 五个参考页面均有对应可访问路由。
2. 页面默认读取真实数据库，刷新后数据仍来自 API，不存在示例数组或硬编码统计。
3. FTS 中文和空格拼音搜索都能工作。
4. 头像、消息、联系人、资源和图表能从真实数据链路追溯。
5. 三个 FTS 库仍通过 `quick_check` 和 `integrity_check`。
6. 1448×1086 截图与参考图达到可审计的像素级结构和样式匹配。
7. 所有测试通过，前后端生产构建成功。
8. 最终回复列出运行命令、测试结果、真实数据统计、截图路径、仍然无法从数据库可靠推导的字段；不要用“已搭好框架”作为结束。

现在开始直接实施。

---
