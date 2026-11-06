import { base } from '@/features/chats/api'

export type SearchMode = 'bwiki' | 'deepseek' | 'gpt' | 'hybrid'
export const modeNames: Record<SearchMode, string> = {
  bwiki: 'BWIKI 直连',
  deepseek: 'DeepSeek Search / Fetch',
  gpt: 'GPT Web Search',
  hybrid: 'BWIKI + DeepSeek',
}
export type PromptKey =
  | 'decision_prompt'
  | 'plan_prompt'
  | 'answer_prompt'
  | 'review_prompt'
  | 'search_prompt'
export type RetrievalConfig = {
  search_queries: number
  wiki_titles: number
  wiki_search_count: number
  deepseek_count: number
  max_documents: number
  max_wiki_documents: number
  evidence_limit: number
  chunk_chars: number
  chunks_per_document: number
  search_cache_seconds: number
  page_cache_seconds: number
  wiki_cache_seconds: number
  allow_unverified_sources: boolean
}
export type ReplyConfig = Partial<Record<PromptKey, string | null>> & {
  enabled: boolean
  proactive: boolean
  ai_profile_id: string
  planning_profile_id?: string | null
  review_profile_id?: string | null
  local_min_text_chars?: number
  local_require_question_signal?: boolean
  local_question_pattern?: string
  context_message_chars?: number
  context_quote_chars?: number
  exclude_same_second?: boolean
  retrieval?: RetrievalConfig
  decision_profile_ids?: string[]
  decision_skip_threshold?: number
  search_profile_id?: string | null
  search_mode: SearchMode
  context_messages: number
  context_minutes: number
  processing_timeout_seconds?: number
  live_expiry_seconds?: number
  cooldown_seconds: number
  confidence_threshold: number
  max_reply_chars: number
}
export type ReplyRule = {
  account: string
  room: string
  name: string
  revision: number
  config: ReplyConfig
}
export type Case = {
  message_id: number
  category: string
  label: string
  text: string
  timestamp: number
}
export type BatchSummary = {
  id: string
  room: string
  name: string
  status: string
  total: number
  done: number
  replies: number
  created_at: number
}
export type RunSummary = {
  id: string
  message_id: number
  trigger_mode: 'original' | 'ask'
  search_mode: SearchMode
  status: string
  input: string
  answer: string | null
  reason: string | null
  detail: string | null
  should_reply: number | null
  elapsed_ms: number | null
  confidence: number | null
  context_hash: string
}
export type Batch = BatchSummary & { runs: RunSummary[] }
export type ContextMessage = {
  message_id: number
  time: string
  speaker: string
  category: string
  text: string
  quote: { kind: string; text: string } | null
}
export type RunDetail = {
  id: string
  context_hash: string
  config: ReplyConfig
  model_revision: number
  profile_revisions?: Record<string, number | null>
  delivery_status: string
  context: {
    group: string
    at: string
    previous: ContextMessage[]
    current: ContextMessage
  }
  result: {
    should_reply: boolean
    answer: string
    reason: string
    detail: string
    confidence: number
    decision?: {
      profile_id?: string
      model?: string
      choice?: string
      probabilities?: Record<string, number>
      usage?: Record<string, number>
      attempts: {
        profile_id: string
        status: string
        detail?: string
        elapsed_ms: number
      }[]
    } | null
    plan: { question: string; reason: string; queries: string[] } | null
    evidence: {
      id: string
      title: string
      url: string
      source_kind: string
      published_at: string | null
      text: string
    }[]
    searches: { provider: string; query: string; results: number }[]
    retrieval_errors: string[]
    review: { reason: string } | null
  } | null
}
export type ReplyStatus = {
  prompts: Record<PromptKey, string>
  account: string
  profile: { display_name: string }
  rules: ReplyRule[]
  batches: BatchSummary[]
  recent: {
    id: string
    room: string
    message_id: number
    status: string
    created_at: number
    input: string
    answer: string | null
    reason: string | null
    delivery_status: string
  }[]
  running: boolean
  error: string | null
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${base}/group-replies${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const data = await response.json().catch(() => null)
  if (!response.ok || !data)
    throw new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : `问答请求失败（${response.status}）`
    )
  return data
}
export const replyApi = {
  status: () => request<ReplyStatus>(''),
  groups: (account: string) =>
    request<{ items: { id: string; name: string }[] }>(
      `/groups?${new URLSearchParams({ account })}`
    ),
  save: (rule: Omit<ReplyRule, 'name'>) => request<ReplyRule>('/rules', rule),
  cases: (account: string, room: string) =>
    request<{ items: Case[] }>(
      `/cases?${new URLSearchParams({ account, room })}`
    ),
  replay: (payload: {
    account: string
    room: string
    message_ids: number[]
    search_modes: SearchMode[]
    trigger_modes: ('original' | 'ask')[]
    request_id: string
  }) => request<Batch>('/replays', payload),
  batch: (id: string) => request<Batch>(`/replays/${id}`),
  cancel: (id: string) => request<Batch>(`/replays/${id}/cancel`, {}),
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
}
