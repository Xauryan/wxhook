import { base } from '@/features/chats/api'

export type FriendConfig = {
  enabled: boolean
  reply_enabled: boolean
  reply_text: string
}
export type GroupConfig = { enabled: boolean; keyword: string; room: string }
export type Saved<T> = { config: T; revision: number; updated_at: number }
export type HongbaoStatus = {
  available: boolean
  enabled: boolean | null
  receive_only: boolean
  delay_ms: string | null
  scope: 'native_process'
  detail: string
}
export type IntegrationStatus = {
  account: string
  profile: { username: string; display_name: string }
  friends: Saved<FriendConfig>
  groups: Saved<GroupConfig>
  hongbao: HongbaoStatus
  native_friends: { available: boolean; enabled: boolean | null }
  runtime: { running: boolean; available: boolean; error: string | null }
  actions: {
    action_id: string
    feature: string
    peer: string
    room: string
    state: string
    detail: string
    created_at: number
  }[]
}
export type Group = {
  username: string
  name: string
  role: 'owner' | 'unverified'
  can_manage: boolean
  reason: string
}

async function request<T>(path: string, signal?: AbortSignal, body?: unknown) {
  const response = await fetch(`${base}/integrations${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    signal,
    headers: {
      Accept: 'application/json',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const result = await response.json().catch(() => null)
  if (!response.ok || !result) {
    throw new Error(
      typeof result?.detail === 'string'
        ? result.detail
        : `应用接口不可用（HTTP ${response.status}）`
    )
  }
  return result as T
}

export const integrationsApi = {
  status: (signal?: AbortSignal) => request<IntegrationStatus>('', signal),
  groups: (account: string, signal?: AbortSignal) =>
    request<{ account: string; checked_at: number; items: Group[] }>(
      `/groups?${new URLSearchParams({ account, refresh: 'true' })}`,
      signal
    ),
  save: (
    feature: 'friends' | 'groups',
    account: string,
    revision: number,
    config: FriendConfig | GroupConfig
  ) =>
    request<Saved<FriendConfig | GroupConfig>>(`/${feature}`, undefined, {
      account,
      revision,
      config,
    }),
  hongbao: (account: string, enabled: boolean, expected_enabled: boolean) =>
    request<HongbaoStatus>('/hongbao', undefined, {
      account,
      enabled,
      expected_enabled,
    }),
  hongbaoDelay: (
    account: string,
    delay_ms: string,
    expected_delay_ms: string
  ) =>
    request<HongbaoStatus>('/hongbao/delay', undefined, {
      account,
      delay_ms,
      expected_delay_ms,
    }),
}
