# 微信数据库面板融合

2026-10-15 新增[一键解密与自动更新](analysis-snapshot-automation.md)，页面无需手动执行解密命令。

来源：[ssq123123/weixin-db-decrypt-panel](https://github.com/ssq123123/weixin-db-decrypt-panel)，核对版本 `4cd561122b23f8077ecae8209069deb75ebc9aa2`，日期 2026-10-14。

主入口仍为 `frontend-next`。现有侧栏增加“数据分析”，页面继续使用本项目的 FastAPI、账号边界和只读数据库访问。重复的数据读取、联系人、消息、资源、指标和搜索保留本项目实现；补入缺失的页面入口与上游新增能力。

| 上游能力 | 融合位置 | 保留与补充方式 |
| --- | --- | --- |
| 概览、趋势、类型占比、常聊排行、活跃日历 | `/analysis/overview` | 接入已有分析 API，支持时间范围与图表钻取 |
| 联系人排行、关系图、增长、响应时长分布 | `/analysis/contacts` | 保留聊天内通讯录，新增分析视图 |
| 媒体、文件、链接、词云、主题、现有 OCR 结果 | `/analysis/media` | 保留本项目资源读取，缺失媒体如实显示 |
| 年度热力图、消息量、贡献、被引用、当日详情 | `/analysis/contributions` | 支持年份、指标、日期与会话钻取 |
| 会话片段、筛选、小时分析、引用定位 | `/analysis/conversations` | 保留本项目会话模型与引用回连 |
| CSV/JSON/HTML 导出 | 会话分析页面 | 复用导出 API，补入 CSV 公式字符转义 |
| 历史会话资料、年度活动、文件与链接 | `/analysis/chat-list` | 保留历史查询；现有 `/chats` 继续负责当前聊天和发送 |
| 消息、联系人、收藏与拼音搜索及降级说明 | `/analysis/search` | 复用本项目 FTS 和去重结果 |
| 数据源、账号、质量、快照与刷新 | `/analysis/settings` | 数据库诊断独立于外观设置 |
| 缺失/无效报告、损坏快照下的首次启动 | 分析入口、`/api/health` | 显示引导或错误；不伪造统计，独立聊天入口保留 |
| 报告校验、版本目录、快照热加载 | 现有 `WeChatRepository` | 补入上游能力，保留本项目实时事件物化 |
| 缓存原子发布、Windows 旧连接保留 | 现有分析缓存 | 重建发布独立文件，实时写入跟随活动缓存 |
| 密钥提取、SQLCipher 校验、解密、WAL 检测、FTS tokenizer | 本项目解密工具和 native tokenizer | 保留本项目版本与原生工具，不自动注入微信 |
| 目录全成功后发布、账号推断、SHA-256/SQLite 清单 | `decrypt --report ...` | 新增可选快照发布，历史不带 `--report` 的命令保留原行为 |

## 快照生成

先使用本项目已有提取器取得经校验的密钥，再对指定数据库目录执行：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
uv run --python .\wx_hook_bridge\wechat-db-web-panel\.venv\Scripts\python.exe python .\wx_hook_bridge\python\tools\wechat_db_decrypt.py decrypt '<wechat-account>\db_storage' `
  --keys .\wx_hook_bridge\runtime\db_decrypt\keys.json `
  --account '<wxid>' `
  --output-dir .\wx_hook_bridge\runtime\db_decrypt\all `
  --report .\wx_hook_bridge\runtime\db_decrypt\decryption_report.json
```

完整快照发布为 `all.<snapshot-id>`，报告指向实际目录。数据库与报告只留在忽略的运行时目录，密钥不展示或进入源码。未指定 `--report` 时继续使用原解密行为。

上游提取器和 tokenizer 面向微信 `4.1.9.23`，不能据此声明其他版本兼容。既有实时聊天和消息发送不因分析融合而改写。

## 验证边界

报告、首次启动、快照切换、解密发布和实时缓存兼容测试使用临时目录。前端分别验证真实未配置状态与隔离分析数据；测试数据不会写入真实微信数据库或成为默认展示内容。

真实快照未配置时，八个入口显示配置引导。完整真实数据验收需要版本匹配、完成校验的快照，不能用测试通过代替。

MIT 许可保存在 `frontend-next/public/licenses/weixin-db-decrypt-panel.txt`，随构建分发。
