import { base } from './api'

export type Contact = {
  username: string
  display_name: string
  nickname: string
  remark: string
  alias: string
  description?: string
  avatar_url?: string
  initial: string
  is_self: boolean
  can_send: boolean
  can_edit_remark: boolean
  gender?: 'male' | 'female' | null
  signature?: string
  region?: { country: string; province: string; city: string }
  source_scene?: number | null
  source_label?: string | null
  added_date?: string | null
  common_groups_known: boolean
  common_groups: { username: string; display_name: string }[]
}
export type ContactList = {
  account: string
  ready: boolean
  updated_at?: number
  directory_count: number | null
  total: number
  items: Contact[]
  contacts_error?: string | null
  contacts_syncing?: boolean
}
export type NewFriends = {
  account: string
  ready: boolean
  detail: string
  items: {
    id: string
    username: string
    display_name: string
    content: string
    timestamp: number
    in_contacts: boolean
    avatar_url?: string
  }[]
}
export type RemarkRequest = {
  account: string
  remark: string
  expected_remark: string
  request_id: string
}
export type RemarkReceipt = {
  request_id: string
  status: 'dispatching' | 'confirmed' | 'pending' | 'unknown' | 'failed'
  detail: string
}

async function request<T>(
  path: string,
  signal?: AbortSignal,
  body?: unknown
): Promise<T> {
  const response = await fetch(`${base}/chat/contacts${path}`, {
    signal,
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      Accept: 'application/json',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json().catch(() => null)
  if (!response.ok)
    throw new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : '通讯录暂不可用，请稍后重试'
    )
  if (!data) throw new Error('通讯录接口返回了无效内容')
  return data as T
}
export const contactsApi = {
  list: (account: string, q: string, offset: number, signal?: AbortSignal) =>
    request<ContactList>(
      `?${new URLSearchParams({ account, q, offset: String(offset), limit: '100' })}`,
      signal
    ),
  detail: (account: string, username: string, signal?: AbortSignal) =>
    request<Contact>(
      `/${encodeURIComponent(username)}?${new URLSearchParams({ account })}`,
      signal
    ),
  refresh: (account: string) =>
    request<ContactList>(
      `/refresh?${new URLSearchParams({ account })}`,
      undefined,
      {}
    ),
  newFriends: (account: string, signal?: AbortSignal) =>
    request<NewFriends>(
      `/new-friends?${new URLSearchParams({ account })}`,
      signal
    ),
  remark: (username: string, body: RemarkRequest) =>
    request<RemarkReceipt>(
      `/${encodeURIComponent(username)}/remark`,
      undefined,
      body
    ),
}
