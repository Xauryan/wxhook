export type Session = {
  username: string
  display_name: string
  avatar_url?: string
  is_group: boolean
  message_count: number
  summary?: string
  last_active?: string
  remark?: string
  can_send?: boolean
}
export type Message = {
  id: string
  server_id?: string
  order_time: number
  revision: number
  source?: string
  decode_error?: string | null
  parse_error?: boolean
  sender_name?: string
  avatar_url?: string
  create_time?: string
  summary?: string
  content?: string
  category?: string
  is_self?: boolean
  title?: string
  description?: string
  url?: string
  cover_url?: string
  source_name?: string
  articles?: Article[]
  file_name?: string
  file_size?: number
  duration?: number
  card?: { name: string; username: string; description: string }
  location?: { label: string; poiname: string; x: string; y: string }
  records?: MessageRecord[]
  records_truncated?: boolean
  type_label?: string
  mini_program?: { name?: string; page?: string; app_id?: string }
  payment?: { kind: string; amount?: string; remark?: string; status?: string }
  call?: { text?: string; duration?: number }
  reference?: {
    display_name?: string
    content?: string
    server_id?: string
    kind?: string
    url?: string
  } | null
}
export type MessageRecord = {
  sender: string
  content: string
  type: string
  kind?: string
  title?: string
  url?: string
  file_name?: string
  file_size?: number
  duration?: number
  resource_note?: string
  children?: MessageRecord[]
  truncated?: boolean
}
export type Article = {
  title: string
  url?: string
  cover_url?: string
  description?: string
  source?: string
}
export type MediaItem = {
  index: number
  kind: string
  variant: string
  name?: string
  size?: number
  state: string
  detail: string
  url?: string
  mime?: string
}
export type MediaResult = {
  items: MediaItem[]
  preferred: number
  elapsed_ms: number
}
export type FilePreviewResult = {
  kind: string
  detail: string
  truncated: boolean
  sections: { title: string; rows?: string[][]; paragraphs?: string[] }[]
}
export type SessionList = {
  items: Session[]
  total: number
  snapshot_time?: string
}
export type Recall = {
  id: string
  server_id: string
  recalled_at: number | null
  observed_at: number
  notice: string
  sender_name: string
  session: Session
  original: Partial<Message> | null
  jump_server_id: string | null
  jump_message_id: string | null
}
export type RecallList = {
  account: string
  items: Recall[]
  total: number
  next_cursor: string | null
  revision: number
}
export type ChatTarget = { server?: string; messageId?: string }
export type SessionDetail = {
  session: Session
  messages: Message[]
  snapshot_time?: string
  next_cursor: string | null
  boundary_cursor: string | null
  detached?: boolean
  removed_ids?: string[]
  has_more: boolean
  revision: number
  elapsed_ms: number
  account: string
  history_revision: string
}
export type Changes = {
  messages: Message[]
  deleted: string[]
  revision: number
  has_more: boolean
  account: string
  session?: Session
  boundary_cursor?: string | null
  elapsed_ms?: number
}
export type Profile = {
  profile: {
    username: string
    display_name?: string
    nickname?: string
    avatar_url?: string
  }
}
export type Scope = 'all' | 'groups' | 'contacts'
export type LiveStatus = {
  send_enabled?: boolean
  contacts_verified?: boolean
  contacts_syncing?: boolean
  contacts_error?: string | null
  contacts_revision?: number
  history_syncing?: boolean
  history_error?: string | null
  history_updated_at?: number
  history_count?: number
  history_revision?: string
  account: string
  profile: Profile['profile']
  bridge_connected: boolean
  receive_connected: boolean
  newest_sequence: number
  relay_pending: number
  relay_dropped: number
}
export type SendReceipt = {
  request_id: string
  status: 'dispatching' | 'accepted' | 'failed' | 'unknown'
  detail: string
}

export const base = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')

async function responseData<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => null)
  if (!response.ok)
    throw new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : `本地接口暂不可用（HTTP ${response.status}）`
    )
  if (
    !response.headers.get('content-type')?.includes('application/json') ||
    !data ||
    typeof data !== 'object' ||
    Array.isArray(data)
  )
    throw new Error('本地接口返回了无法识别的结果，请检查 API 服务。')
  return data as T
}

async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    signal,
    headers: { Accept: 'application/json' },
  })
  return responseData<T>(response)
}

// Only local API assets are rendered. Never load remote contact avatars.
export function localAsset(value?: string): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value, window.location.origin)
    const api = new URL(base, window.location.origin)
    if (
      url.origin !== api.origin ||
      !url.pathname.startsWith(`${api.pathname}/`)
    )
      return undefined
    return url.href
  } catch {
    return undefined
  }
}

export function cleanText(value?: string): string {
  if (!value) return ''
  return value
    .replace(/<[^>]*>/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .trim()
}

export const chatApi = {
  list: (
    account: string,
    q: string,
    scope: Scope,
    offset: number,
    signal?: AbortSignal
  ) =>
    read<SessionList>(
      `/chat/sessions?${new URLSearchParams({ account, q, scope, offset: String(offset), limit: '80' })}`,
      signal
    ),
  detail: (
    account: string,
    username: string,
    before?: string | null,
    signal?: AbortSignal,
    around?: string,
    floor?: string,
    aroundId?: string
  ) =>
    read<SessionDetail>(
      `/chat/sessions/${encodeURIComponent(username)}?${new URLSearchParams({ account, limit: '60', ...(before ? { before } : {}), ...(around ? { around } : {}), ...(floor ? { floor } : {}), ...(aroundId ? { around_id: aroundId } : {}) })}`,
      signal
    ),
  recalls: (
    account: string,
    q: string,
    before: string | null,
    signal?: AbortSignal
  ) =>
    read<RecallList>(
      `/chat/recalls?${new URLSearchParams({ account, q, limit: '50', ...(before ? { before } : {}) })}`,
      signal
    ),
  changes: (
    account: string,
    username: string,
    after: number,
    signal?: AbortSignal,
    floor?: string
  ) =>
    read<Changes>(
      `/chat/sessions/${encodeURIComponent(username)}/changes?${new URLSearchParams({ account, after: String(after), ...(floor ? { floor } : {}) })}`,
      signal
    ),
  media: (
    account: string,
    id: string,
    signal?: AbortSignal,
    refresh = false,
    record?: string
  ) =>
    read<MediaResult>(
      `/chat/messages/${id}/media?${new URLSearchParams({ account, ...(refresh ? { refresh: 'true' } : {}), ...(record ? { record } : {}) })}`,
      signal
    ),
  status: (signal?: AbortSignal) => read<LiveStatus>('/chat/status', signal),
  receipt: (account: string, request: string) =>
    read<SendReceipt>(
      `/chat/outbox/${request}?${new URLSearchParams({ account })}`
    ),
  send: async (
    account: string,
    username: string,
    text: string,
    request_id: string
  ): Promise<SendReceipt> => {
    const response = await fetch(`${base}/chat/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account, username, text, request_id }),
    })
    const result = await responseData<SendReceipt>(response)
    if (
      result.request_id !== request_id ||
      !['dispatching', 'accepted', 'failed', 'unknown'].includes(
        result.status
      ) ||
      typeof result.detail !== 'string'
    )
      throw new Error('发送回执格式异常，请先在微信核对结果。')
    return result
  },
}
