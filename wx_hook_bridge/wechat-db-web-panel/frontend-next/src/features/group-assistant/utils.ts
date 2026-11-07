import type { ModelProfile } from '@/features/ai/api'

export const selectStyle =
  'h-9 w-full rounded-md border bg-background px-2 text-sm'

export const formatTime = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
  })

export const newRequestId = () => crypto.randomUUID().replace(/-/g, '')

export const profileName = (profiles: ModelProfile[], id?: string | null) =>
  id
    ? (profiles.find((profile) => profile.id === id)?.config.name ??
      '已删除的配置')
    : '未选择'

export const textProfiles = (profiles: ModelProfile[]) =>
  profiles.filter((profile) => profile.config.enabled && !profile.decision_only)

export const reasons: Record<string, string> = {
  supported: '有依据的回答',
  faq: '复用常见问题答案',
  not_question: '缺少提问信号',
  empty_content: '没有可分析的文本',
  decision_unavailable: '决策服务暂不可用',
  not_actionable: '无需知识问答',
  self_message: '自己发的普通消息',
  proactive_disabled: '主动回答已关闭',
  not_text: '非文本问题',
  missing_context: '需要补充上下文',
  group_local: '需要群内信息',
  retrieval_empty: '未取得有效资料',
  insufficient_evidence: '依据不足',
  invalid_citation: '引用未通过核验',
  invalid_answer: '回答格式未通过核验',
  cooldown: '主动回答间隔内',
  error: '查询失败',
  expired: '超过实时回复时限',
}
export const reasonText = (reason?: string | null) =>
  reasons[reason ?? ''] ?? reason ?? '等待执行'
