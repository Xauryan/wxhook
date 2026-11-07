import type { ReplyRule } from '@/features/group-replies/api'
import type { ReportRule } from '@/features/group-reports/api'
import type {
  ModelCapabilities,
  ModelConfig,
  ModelProfile,
  ModelSettings,
  Provider,
} from './api'

const purposes: Record<string, string> = {
  'group_report.topics': '群日报 · 话题',
  'group_report.titles': '群日报 · 称号',
  'group_report.quotes': '群日报 · 金句',
  'group_reply.plan': '群问答 · 问题解析',
  'group_reply.answer': '群问答 · 回答',
  'group_reply.verify': '群问答 · 答案核验',
  'decision.classify': '消息过滤',
  web_search: '网页搜索',
  connection_test: '连接测试',
}
export const purposeName = (purpose: string) => purposes[purpose] ?? purpose

export const modelName = (profile: ModelProfile) =>
  profile.model.label ?? profile.model.model

export const decisionFormats: Partial<
  Record<ModelConfig['api_format'], string>
> = {
  openai_decisions: 'OpenAI Decisions',
  typesafe_decisions: 'Decisions state 格式',
  typesafe_systemone: 'TypeSafe System One',
}

/** Where each model is referenced, so editing or deleting it is not a blind action. */
export function modelUsage(reports: ReportRule[], replies: ReplyRule[]) {
  const usage = new Map<string, string[]>()
  const add = (id: string | null | undefined, text: string) => {
    if (id) usage.set(id, [...(usage.get(id) ?? []), text])
  }
  for (const rule of reports)
    add(rule.config.ai_profile_id, `${rule.name} 日报`)
  for (const rule of replies) {
    const config = rule.config
    add(config.ai_profile_id, `${rule.name} 问答`)
    add(config.planning_profile_id, `${rule.name} 问题解析`)
    add(config.review_profile_id, `${rule.name} 答案核验`)
    add(config.search_profile_id, `${rule.name} 网页搜索`)
    config.decision_profile_ids?.forEach((id, index) =>
      add(id, `${rule.name} 消息过滤 #${index + 1}`)
    )
  }
  return usage
}

export const suggested: Partial<Record<Provider['config']['kind'], string>> = {
  deepseek: 'deepseek-flash',
  openai: 'gpt-6-astra',
}

export function modelFormats(kind: Provider['config']['kind']) {
  const text: [ModelConfig['api_format'], string][] = [
    ['auto', '文本生成（自动选择协议）'],
  ]
  if (kind === 'compatible')
    text.push(
      ['responses', '文本生成 · OpenAI Responses'],
      ['chat_completions', '文本生成 · OpenAI Chat Completions'],
      ['anthropic', '文本生成 · Anthropic Messages']
    )
  if (kind === 'openai' || kind === 'compatible')
    for (const [value, label] of Object.entries(decisionFormats))
      if (kind === 'compatible' || value === 'openai_decisions')
        text.push([value as ModelConfig['api_format'], `消息过滤 · ${label}`])
  return text
}

export function newModel(
  model: string,
  catalog: Record<string, ModelCapabilities>,
  apiFormat: ModelConfig['api_format'] = 'auto'
): ModelSettings {
  const decision = apiFormat in decisionFormats
  return {
    label: null,
    model,
    api_format: apiFormat,
    reasoning_effort: null,
    enabled: true,
    timeout_seconds: 120,
    max_output_tokens: decision
      ? 4096
      : (catalog[model]?.max_output_tokens ?? 32768),
    context_chars: null,
    token_parameter: 'max_tokens',
    json_mode: false,
    temperature: null,
  }
}
