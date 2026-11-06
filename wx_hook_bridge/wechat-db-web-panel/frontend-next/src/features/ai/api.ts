import { base } from '@/features/chats/api'

export type ModelConfig = {
  name: string
  base_url: string
  model: string
  api_format:
    | 'auto'
    | 'chat_completions'
    | 'responses'
    | 'anthropic'
    | 'openai_decisions'
    | 'typesafe_decisions'
    | 'typesafe_systemone'
  provider:
    | 'compatible'
    | 'openai'
    | 'deepseek'
    | 'anthropic'
    | 'openai_decisions'
    | 'typesafe_decisions'
    | 'typesafe_systemone'
  reasoning_effort?: 'none' | 'low' | 'medium' | 'high' | 'xhigh' | 'max' | null
  search_fetch: boolean
  enabled: boolean
  timeout_seconds: number
  max_output_tokens: number
  context_chars: number | null
  token_parameter: 'max_tokens' | 'max_completion_tokens'
  json_mode: boolean
  temperature: number | null
}
export type ProviderKind = 'compatible' | 'openai' | 'deepseek' | 'anthropic'
export type ProviderConfig = {
  name: string
  kind: ProviderKind
  base_url: string
  search_fetch: boolean
  enabled: boolean
}
export type Provider = {
  id: string
  revision: number
  config: ProviderConfig
  api_key_set: boolean
  updated_at: number
}
export type ModelSettings = Omit<
  ModelConfig,
  'name' | 'base_url' | 'provider' | 'search_fetch'
> & { label: string | null }
export type ModelProfile = {
  id: string
  revision: number
  provider_id: string
  model: ModelSettings
  config: ModelConfig
  api_key_set: boolean
  decision_only?: boolean
  updated_at: number
}
export type ModelCapabilities = {
  context_window_tokens: number
  max_output_tokens: number
  reasoning_efforts: NonNullable<ModelConfig['reasoning_effort']>[]
  default_reasoning_effort: string
  source: string
}
export type AIStatus = {
  providers: Provider[]
  catalog: Record<string, ModelCapabilities>
  profiles: ModelProfile[]
  recent_usage: {
    profile_id: string
    purpose: string
    model: string
    status: string
    usage: Record<string, number>
    elapsed_ms: number
    created_at: number
  }[]
}

async function request<T>(
  path: string,
  method = 'GET',
  body?: unknown
): Promise<T> {
  const response = await fetch(`${base}/ai${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const data = await response.json().catch(() => null)
  if (!response.ok || !data) {
    throw new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : `AI 接口请求失败（${response.status}）`
    )
  }
  return data
}

export const aiApi = {
  remove: (id: string, revision: number) =>
    request<{ ok: boolean }>(`/profiles/${id}?revision=${revision}`, 'DELETE'),
  testDecision: (id: string) =>
    request<{ ok: boolean; choice: string; elapsed_ms: number }>(
      `/profiles/${id}/test-decision`,
      'POST'
    ),
  status: () => request<AIStatus>('/profiles'),
  saveProvider: (
    id: string | undefined,
    revision: number,
    config: ProviderConfig,
    apiKey: string,
    clear: boolean
  ) =>
    request<Provider>(
      id ? `/providers/${id}` : '/providers',
      id ? 'PUT' : 'POST',
      { revision, config, api_key: apiKey || null, clear_api_key: clear }
    ),
  removeProvider: (id: string, revision: number) =>
    request<{ ok: boolean }>(`/providers/${id}?revision=${revision}`, 'DELETE'),
  saveModel: (
    id: string | undefined,
    revision: number,
    providerId: string,
    config: ModelSettings
  ) =>
    request<ModelProfile>(
      id ? `/models/${id}` : '/models',
      id ? 'PUT' : 'POST',
      { revision, provider_id: providerId, config }
    ),
  test: (id: string) =>
    request<{
      ok: boolean
      model: string
      elapsed_ms: number
      usage: Record<string, number>
    }>(`/profiles/${id}/test`, 'POST'),
  testSearchTools: (id: string) =>
    request<{ ok: boolean; search_results: number; fetch_chars: number }>(
      `/profiles/${id}/test-search`,
      'POST'
    ),
}

export const providerKinds: Record<
  ProviderKind,
  { label: string; hint: string; url: string }
> = {
  openai: {
    label: 'OpenAI',
    hint: '官方接口或 OpenAI 格式的中转站，默认走 Responses 接口',
    url: 'https://api.openai.com/v1',
  },
  compatible: {
    label: '其他兼容服务',
    hint: '自建或第三方服务，可在模型里指定协议',
    url: '',
  },
  deepseek: {
    label: 'DeepSeek',
    hint: 'DeepSeek 官方接口，可同时开启 Search / Fetch',
    url: 'https://api.deepseek.com',
  },
  anthropic: {
    label: 'Anthropic',
    hint: 'Anthropic Messages 接口',
    url: 'https://api.anthropic.com/v1',
  },
}
