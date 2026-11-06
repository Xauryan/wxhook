# 指标定义

## 统一约定

- 时间先从 Unix 时间转换到 `WECHAT_TIMEZONE`，默认 `Asia/Shanghai`，再计算日期和小时。
- 默认数据集为实际存在的 195 张 `Msg_*` 表中的规范化快照消息，加上已通过本机 Hook 验证并物化的实时消息覆盖层。
- 快照消息与实时消息按稳定 `message_key = sha1(session_username, local_id, server_id)` 去重；同一 `event_id` 或同一 bridge 消息身份不会重复计数。
- 页面筛选存在时，公式中的集合 `M` 表示筛选后的消息集合；否则表示全量可读取消息。
- 无法解压的消息仍可参与数量、日期和类型统计，但不参与关键词、主题或正文质量评分。
- 空集合返回 `null` 或空数组并附 `metric_basis`，不能用参考图数值填充。
- 所有阈值由后端设置返回，UI tooltip 不复制一套不同定义。
- `snapshot_time` 始终表示解密快照时间，不因实时事件到达而改变。事件状态和最新 sequence 由 realtime status/SSE 单独表达。

## 消息总数

```text
message_count = |M|
```

没有实时覆盖层时，全量值等价于所有真实 `Msg_*` 行数之和。有实时覆盖层时，再加尚未进入快照且已成功物化的去重消息。不能使用 `SessionTable.unread_count`、FTS 行数、SSE 通知次数或资源数代替。

## 活跃天数

```text
active_days = count(distinct local_date(message.create_time))
```

只有时间戳可转换的消息纳入。年度页面先限制到所选自然年。

## 最近活跃

```text
last_active_at = max(valid message.create_time)
```

显示为本地时区。快照中没有消息时为 `null`。

## 联系人数

当前实现中的“联系人总数”是 `SELECT count(*) FROM contact.contact`，即快照表全部行数；不应用 `delete_flag`、`verify_flag` 或类型过滤。“有互动联系人”来自消息 session 的 distinct username。两者必须使用不同标签，不能混用。

## 系统会话过滤

联系人排行和关系网络排除当前账号、`sys_` 前缀以及以下固定 username：

```text
brandsessionholder, fmessage, filehelper, floatbottle, medianote,
newsapp, notifymessage, notification_messages, officialaccounts,
qmessage, qqmail, tmessage, weixin
```

当前实现不基于 `verify_flag` 额外过滤公众号，也不从关系排行中排除群聊。响应时长单独排除群聊。规则集中维护在 `backend/app/repository.py:SYSTEM_USERNAMES` 和 `_is_system()`；修改集合时必须同步测试和本文。

## 常聊联系人

先按 `session_username` 聚合筛选后的真实消息：

```text
contact_message_count(s) = count(m in M where m.session_username = s)
```

按 `contact_message_count` 降序，最近消息时间降序，username 稳定排序。摘要来自该 session 最新一条可展示消息，头像来自真实头像链路。

## 消息类型分布

首先计算：

```text
base_type   = local_type & 0xffffffff
app_subtype = local_type >> 32
```

最低分类映射：

| 分类 | 判定 |
|---|---|
| 文本 | `base_type=1` |
| 图片 | `base_type=3` |
| 语音 | `base_type=34` |
| 视频 | `base_type=43` |
| 表情 | `base_type=47` |
| 好友请求 | `base_type=37` |
| 联系人名片 | `base_type=42` |
| 位置 | `base_type=48` |
| 通话 | `base_type=50` |
| 状态 | `base_type=51` |
| 链接 | `base_type=49` 且 XML app type/高位 subtype 为 `3, 5, 33, 36` |
| 文件 | `base_type=49` 且 XML app type/高位 subtype 为 `6` |
| 引用 | `base_type=49 and app_subtype=57` |
| 聊天记录 | `base_type=49` 且 subtype 为 `19, 24` |
| 转账 | `base_type=49` 且 subtype 为 `2000, 2001` |
| app | 其他 `base_type=49` |
| 系统 | `base_type in (10000, 10002)` |
| 其他 | 未识别组合，保留原始数值 |

每类数量为匹配集合大小，占比为 `category_count / message_count`。图例数量之和必须等于总数。

## 近 30 天趋势

以本地自然日分桶，结束日期为筛选结束日或快照中最后一天：

```text
daily_count[d] = count(m where local_date(m.create_time) = d)
```

没有消息的日期补零；未来日期不补。tooltip 显示日期和真实数量。

## 年度热力图

年度发言量模式使用 `daily_count[d]`。六级颜色通过固定边界或数据分位数映射，但图例必须返回实际边界。无活动日为 0，不等同无数据。

## 首次互动联系人增长

数据库没有可靠的联系人添加时间，因此只能定义：

```text
first_interaction(s) = min(local_date(m.create_time)) for session s
growth[d] = count(s where first_interaction(s) = d)
cumulative_growth[d] = sum(growth[x] for x <= d)
```

UI 标签必须写“首次互动联系人增长”，不能写“新增联系人”。

## 响应时长

默认只统计能够可靠识别双方的一对一会话。对每条对方消息 `r`，寻找同 session 中第一条时间更晚的本人消息 `s`：

```text
response_seconds = s.create_time - r.create_time
```

规则：

- 连续多条对方消息只让最后一条等待中的消息形成一次响应样本，避免重复计算同一回复。
- `response_seconds < 0` 的样本丢弃；同时间戳的 0 秒回复是有效样本。
- 超过 `WECHAT_RESPONSE_MAX_SECONDS` 的样本视为离线间隔并丢弃，默认 86,400 秒。
- 群聊、系统会话、发送者不明的消息不纳入。

分桶：`<=5 分钟`、`5 分钟至 1 小时`、`1 至 24 小时`；如阈值低于 24 小时，最后区间随配置缩短。超过阈值单独显示“未纳入”，不能塞进最快或最慢桶。

联系人 API 展示响应时长中位数：

```text
response_median_seconds = median(valid response_seconds)
```

中位数降低长尾响应对展示的影响。样本为 0 时返回 `null` 并显示空状态。

## 会话 episode

同一个 session 按 `(create_time, sort_seq, local_id)` 排序。相邻消息间隔严格大于 `WECHAT_EPISODE_GAP_SECONDS` 时开始新 episode，默认 1,800 秒：

```text
new_episode(i) = i == 0 or message[i].time - message[i-1].time > 1800
```

会话次数是 episode 数，不是消息数。每个 episode 的摘要、媒体预览和时间范围都来自其真实消息。

## 会话轮次

在一个 episode 内，按规范化发送者合并连续消息：

```text
turns = 1 + count(i > 0 where sender[i] != sender[i-1])
```

发送者未知时使用稳定的 unknown sender key，不能让每条未知消息都自动新建一轮。

## 高频时段

```text
hour_count[h] = count(m where local_hour(m.create_time) = h), h in [0, 23]
```

两小时峰值窗口：

```text
window_count[h] = hour_count[h] + hour_count[(h + 1) mod 24]
peak_start = argmax(window_count[h])
```

并列时选择更早的起始小时。API 返回 24 个小时桶和峰值窗口，热度条不能用静态渐变代替数据。

## 活跃时间段

选中日期的开始和结束时间：

```text
active_start = min(local_time(message.create_time))
active_end   = max(local_time(message.create_time))
span_seconds = active_end - active_start
```

只有一条消息时跨度为 0；UI 不应把它表述为持续活动了一整天。

## 最长连续活跃

将年度活跃日期排序，相邻日期差 1 天视为连续：

```text
longest_streak = max(length of consecutive-date run)
```

年度边界外的日期不用于延长所选年度的 KPI。

## 双向比例

一对一 session 中：

```text
self_count  = number of messages sent by current account
other_count = number of messages not sent by current account
balance = 2 * min(self_count, other_count) / (self_count + other_count)
```

`balance` 范围 `[0,1]`，越接近 1 越双向。发送者不可解析时该 session 的双向比例为 `null`。

## 关系强度

关系强度必须是确定性结果而不是模型猜测。推荐统一归一化后计算 0 到 100 分：

```text
volume  = log1p(message_count) / log1p(max_session_message_count)
days    = log1p(active_days) / log1p(max_session_active_days)
age_days = max(0, (latest_message_time - session_last_time) / 86400)
recency = exp(-age_days / 180)
balance = 2 * min(sent_count, received_count) / (sent_count + received_count)

relationship_strength = round(100 * (
    0.35 * volume +
    0.25 * days +
    0.20 * recency +
    0.20 * balance
), 2)
```

最大值和 `latest_message_time` 在同一真实 session 集合上计算。总方向数为 0 时 balance 为 0，不重新分配该权重。

## 被引用与主动引用

成功解析 `<refermsg>` 且能按 `server_id` 回连原消息时创建引用边：

```text
reference_edge = quoting_message -> original_message
```

- 被引用次数：指向某原消息的有效边数量。
- 主动引用次数：当前账号发送的引用消息数量。
- 无法回连但 XML 结构有效的引用计入“引用消息（未解析）”，不计入某条原消息的被引用排行。
- 同一 quoting message 对同一 original message 最多计一次。

## 有效贡献/高质量消息

当前实现使用以下确定性评分，只读取真实规范化正文和消息分类：

```text
score = 0
+1  normalized text length >= 20
+1  normalized text length >= 80
+1  category in {file, link, image, video}
+1  normalized text contains a newline or HTTP(S) URL

effective_contribution = score >= 2
```

同一条件只加一次，分数范围为 0 到 4。年度/日期“有效贡献”只统计当前账号发送且 `score >= 2` 的消息。该分数不等同人工内容质量判断，tooltip 必须展示上述规则。

## 关键词

输入集合只包含 `decoded=true` 的真实消息。当前快照得到 38,484 个关键词文档。处理流程：

1. 文本消息使用清洗后的 `content`；结构化消息只使用 `title/description/reference_content/summary`，全部缺失时才回退 `content`。
2. 对 HTML entity 解码两轮，保留 CDATA 可见文字，然后移除 XML/HTML 标签、URL、`wxid_*`、`gh_*`、`*@chatroom`、控制字符和 5 位以上纯数字。
3. 使用 Unicode NFKC 规范化，压缩空白；清洗结果不足 2 字的不构成文档。
4. 安装 jieba 时使用 `jieba.analyse.extract_tags` TF-IDF，候选量为最终 limit 的两倍，并用 `jieba.cut` 统计 token 的真实次数。
5. 移除 `repository.py:STOP_WORDS` 技术停用词 v2 和长度不大于 1 的 token。
6. jieba 不可用时，用正则提取英文 token 和 2 到 8 字中文片段；大于 4 字的中文片段拆成二元组并按频次排序。
7. 返回前 limit 个结果。

jieba 路径同时返回 `count` 真实 token 次数和 `weight` 六位小数 TF-IDF 权重；fallback 路径返回真实频次。API 的 `keyword_basis` 返回算法、文档数、来源字段和 `stop_words_version=2026-08-local-v2`。词云字号使用权重或明确标记的计数，不能混淆两者。

## 实时搜索合并

微信只读 `message_fts.db` 是快照索引，不包含 Hook 后到达的消息。消息搜索因此使用：

```text
message_results = dedupe(
    immutable_message_fts_hits
    union
    analytics_content_or_summary_like_hits
)
```

去重键为规范化消息 ID。FTS 的 rank 只适用于静态 FTS 命中；实时 LIKE 命中的 `rank` 为 `null`，不能伪造相关度分数。

## 内容主题

主题只有在确定性词表或可解释聚类存在时显示。基于词表时：

```text
topic_score(t, message) = count(tokens in configured vocabulary[t])
topic(message) = highest score topic, stable-order tie break
```

所有分数为 0 时归入“其他”。主题图例数量总和等于实际参与主题分析的消息数，不等于全部消息数时必须显示基数。

## 媒体数量

图片、视频、文件、链接按规范化消息类型和资源索引去重。建议去重键：

```text
(session_username, message_local_id, resource_kind, resource_id_or_index)
```

“索引数量”和“本地可用数量”分别返回。文件大小来自硬链接/资源索引真实字段，不从扩展名推算。

## OCR

OCR 只对本地可读真实图片运行。每条结果包含引擎、识别时间、置信度和源资源 ID。没有结果、引擎不可用或图片缺失时显示原因；不得显示参考图中的票据文字。

## 测试要求

pytest 至少覆盖：

- 本地时区日期边界
- 64 位 `local_type` 拆分
- 30 分钟 episode 边界（等于阈值不切分，大于阈值切分）
- 连续发送者轮次合并
- 响应时长配对和离线阈值
- 关系强度范围、权重、对数归一化和 180 天衰减
- 引用去重和未解析引用
- 关键词只读取可解码正文
- 空数据返回 `null`/空数组而不是示例值
- realtime envelope 字段兼容、`local_type` 组合和解析质量
- 同 `event_id` 与同消息身份的双层幂等
- 事件物化后消息、日、小时、session 和 episode 聚合一致
- 服务重启后的 pending 事件恢复和 SSE sequence 续传
- 256 KiB 请求限制、回环来源限制和 malformed payload
