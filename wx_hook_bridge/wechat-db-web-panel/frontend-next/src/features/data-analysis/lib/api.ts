export const API_BASE = (import.meta.env.VITE_API_BASE ?? '/api').replace(
  /\/$/,
  ''
)

export type DataQuality = {
  status?: string
  integrity?: string
  wal_mode?: string
  snapshot_time?: string
  [key: string]: unknown
}

export type Profile = {
  username: string
  display_name?: string
  nickname?: string
  remark?: string
  alias?: string
  avatar_url?: string
}

export type ProfileResponse = {
  profile: Profile
  stats?: Record<string, number | string | null>
  data_quality?: DataQuality
  snapshot_time?: string
  metric_basis?: string
}

export type TrendPoint = {
  date: string
  count: number
  sent?: number
  received?: number
  episodes?: number
}
export type Contact = {
  username: string
  display_name?: string
  avatar_url?: string
  is_group?: boolean
  message_count?: number
  active_days?: number
  first_interaction?: string
  last_active?: string
  sent_count?: number
  received_count?: number
  bidirectional_ratio?: number
  response_median_seconds?: number
  relationship_strength?: number
  channels?: string[]
  summary?: string
}

export type OverviewResponse = {
  profile?: Profile
  kpis?: Record<string, number | string | null>
  trend?: TrendPoint[]
  top_contacts?: Contact[]
  message_types?: Array<{ type: string; label?: string; count: number }>
  recent_messages?: Array<Record<string, unknown>>
  heatmap?: Array<{ date: string; count: number }>
  snapshot_time?: string
  metric_basis?: string
}

export type ContactsResponse = {
  items: Contact[]
  total: number
  snapshot_time?: string
  metric_basis?: string
}

export type GraphResponse = {
  nodes: Array<{
    id: string
    name: string
    value: number
    category?: string | number
    avatar_url?: string
  }>
  links: Array<{ source: string; target: string; value: number }>
}

export type MediaItem = {
  id: string | number
  kind: string
  message_key?: string
  file_name?: string
  file_size?: number
  modify_time?: string
  create_time?: string
  available?: boolean
  resource_available?: boolean
  browser_supported?: boolean
  url?: string
  thumbnail_url?: string
  title?: string
  source?: string
  snippet?: string
  confidence?: number
  text?: string
}

export type ChatSessionItem = {
  username: string
  display_name: string
  avatar_url?: string
  is_group: boolean
  message_count: number
  active_days: number
  last_active?: string
  summary?: string
  latest_category?: string
  latest_is_self?: boolean
  alias?: string
  remark?: string
  relationship_strength?: number
}

export type ChatListResponse = {
  items: ChatSessionItem[]
  total: number
  counts: { all: number; groups: number; contacts: number }
  scope: 'all' | 'groups' | 'contacts'
  query?: string
  snapshot_time?: string
  metric_basis?: string
}

export type MediaResponse = {
  counts?: Record<string, number>
  items?: MediaItem[]
  keywords?: Array<{ word: string; count: number }>
  topics?: Array<{ name: string; value: number }>
  files?: MediaItem[]
  links?: MediaItem[]
  ocr?: MediaItem[]
  snapshot_time?: string
  metric_basis?: string
}

export type SnsMedia = {
  id?: string | number
  type?: string | number
  sub_type?: string | number
  url?: string
  thumb_url?: string
  preview_url?: string
  media_url?: string
  download_url?: string
  poster_url?: string
  token?: string
  media_token?: string
  mime_type?: string
  file_name?: string
  md5?: string
  width?: number
  height?: number
  size?: number
  duration?: number
  local_path?: string
  available?: boolean
  resource_available?: boolean
  browser_supported?: boolean
  remote_only?: boolean
  status?: string
}

export type SnsComment = {
  id?: string
  comment_id?: string
  comment_64id?: string
  ref_comment_id?: string
  ref_comment_64id?: string
  username?: string
  nickname?: string
  content?: string
  create_time?: string | number
  deleted?: number
}

export type SnsLike = {
  id?: string
  username?: string
  nickname?: string
  create_time?: string | number
}

export type SnsLocation = {
  name?: string
  address?: string
  latitude?: string | number
  longitude?: string | number
}

export type SnsPost = {
  sns_id: string
  tid?: string
  username?: string
  nickname?: string
  avatar_url?: string
  create_time?: string
  create_timestamp?: number
  content?: string
  type?: string | number
  private?: boolean
  visibility?: string
  is_owner?: boolean
  location?: SnsLocation | null
  media?: SnsMedia[]
  comments?: SnsComment[]
  likes?: SnsLike[]
  media_count?: number
  comment_count?: number
  like_count?: number
}

export type SnsStats = {
  posts?: number
  users?: number
  comments?: number
  likes?: number
  media?: number
  remote_media?: number
  local_media?: number
  local_media_bytes?: number
  raw_interactions?: number
  interactions?: number
  source_rows?: number
  first_create_time?: number
  last_create_time?: number
  snapshot_time?: string
}

export type SnsSyncStatus = {
  enabled?: boolean
  configured?: boolean
  source_available?: boolean
  changed?: boolean
  refresh_count?: number
  generation?: number
  source_size?: number
  source_mtime_ns?: number | null
  source_wal_size?: number
  source_wal_mtime_ns?: number | null
  snapshot_size?: number
  snapshot_mtime_ns?: number | null
  snapshot_time?: string
  last_attempt?: string | null
  last_success?: string | null
  last_error?: string | null
}

export type SnsResponse = {
  items: SnsPost[]
  total: number
  limit?: number
  offset?: number
  query?: string
  snapshot_time?: string
  stats?: SnsStats
  sync?: SnsSyncStatus
  metric_basis?: string
}

export type SnsDetailResponse = SnsPost & {
  raw_xml?: string
  raw_tid?: string
  raw_interaction_count?: number
  interactions?: Array<Record<string, unknown>>
}

export type SnsBackup = {
  backup_id: string
  created_at?: string
  path?: string
  download_url: string
  bytes?: number
  sha256?: string
  manifest?: Record<string, unknown>
  counts?: SnsStats
}

export type SnsBackupRequest = {
  backup_id?: string
  include_local_media?: boolean
  include_runtime_artifacts?: boolean
}

export type SnsBackupsResponse = {
  items: SnsBackup[]
  total: number
}

export type ContributionDay = {
  date: string
  count: number
  sent?: number
  received?: number
  quoted?: number
  quality?: number
}

export type ContributionsResponse = {
  year: number
  available_years?: number[]
  kpis?: Record<string, number>
  heatmap?: ContributionDay[]
  selected_day?: Record<string, unknown>
  snapshot_time?: string
  metric_basis?: string
}

export type ConversationEpisode = {
  id: string | number
  date: string
  start_time?: string
  end_time?: string
  session_username?: string
  session_name?: string
  avatar_url?: string
  message_count?: number
  turn_count?: number
  summary?: string
  categories?: string[]
  media?: MediaItem[]
}

export type ConversationsResponse = {
  items: ConversationEpisode[]
  total: number
  hourly?: Array<{ hour: number; count: number }>
  trend?: TrendPoint[]
  top_quoted?: Array<Record<string, unknown>>
  kpis?: Record<string, number | string | null>
  snapshot_time?: string
  metric_basis?: string
}

export type ConversationMessage = {
  id: string
  session_username?: string
  session_name?: string
  sender_username?: string
  sender_name?: string
  avatar_url?: string
  create_time?: string
  timestamp?: number
  summary?: string
  content?: string
  category?: string
  decoded?: boolean
  decode_error?: string
  local_type?: number
  base_type?: number
  app_subtype?: number
  title?: string
  description?: string
  url?: string
  file_name?: string
  file_size?: number
  is_self?: boolean
  quote_count?: number
  reference?: {
    server_id?: string | number
    from_username?: string
    chat_username?: string
    display_name?: string
    content?: string
  } | null
  resources?: MediaItem[]
}

export type ConversationDetailResponse = {
  episode: Record<string, unknown>
  messages: ConversationMessage[]
  resources?: MediaItem[]
  snapshot_time?: string
}

export type ChatSessionDetailResponse = {
  session: ChatSessionItem & {
    alias?: string
    remark?: string
    nickname?: string
    remote_avatar_available?: boolean
    first_interaction?: string
    sent_count?: number
    received_count?: number
    bidirectional_ratio?: number
    tags?: string[]
  }
  messages: ConversationMessage[]
  files: ConversationMessage[]
  year: number
  available_years: number[]
  heatmap: Array<{
    date: string
    count: number
    sent?: number
    received?: number
  }>
  stats: {
    messages: number
    quality: number
    quoted: number
    active_days: number
  }
  selected_day: {
    date?: string
    messages: number
    sent: number
    received: number
    quality: number
    quoted: number
    speakers: number
    first_time?: string
    last_time?: string
  }
  categories: Array<{ category: string; count: number }>
  snapshot_time?: string
  metric_basis?: string
}

export type MessageDetailResponse = ConversationMessage & {
  referenced_message?: ConversationMessage | null
}

export type SearchResponse = {
  query: string
  items: Array<Record<string, unknown>>
  fts_used?: boolean
  errors?: string[]
}

export type RealtimeStatusResponse = {
  enabled?: boolean
  watcher_running?: boolean
  connected_clients?: number
  last_event_id?: string | number
  last_event_type?: string
  snapshot_time?: string
  source_read_only?: boolean
  [key: string]: unknown
}

export type IntentionNotificationGroup = {
  room_id: string
  name: string
}

export type IntentionNotificationConfig = {
  enabled: boolean
  consumer_id: string
  token_configured: boolean
  startup_policy: 'new_only' | 'include_pending'
  activation_cutoff?: string | null
  target_groups: IntentionNotificationGroup[]
}

export type IntentionNotificationRuntime = {
  state: string
  transport?: string | null
  running: boolean
  last_error?: string | null
  last_connected_at?: string | null
  last_event_at?: string | null
  last_schedule_error?: string | null
  rest_base_url: string
  wss_url: string
}

export type IntentionNotificationEvent = {
  event_id: string
  occurred_at: string
  group_name: string
  agent_name: string
  agent_today: number
  group_today: number
  overall_today: number
  announcement_text: string
  status: string
  last_error?: string | null
  remote_ack_status?: string | null
  remote_ack_error?: string | null
  sent_at_ms?: number | null
}

export type IntentionNotificationStatus = {
  config: IntentionNotificationConfig
  runtime: IntentionNotificationRuntime
  today: {
    date: string
    total: number
    sent: number
    skipped: number
    pending: number
    retrying: number
    team_snapshot_ready?: boolean
    team_snapshot_state?: 'ready' | 'missing' | 'no_events'
    on_duty_dialed_number_threshold?: number
  }
  recent: IntentionNotificationEvent[]
  schedule?: {
    timezone: string
    slots: Array<{
      key: string
      label: string
      kind: 'stage' | 'closing'
    }>
    reports: Array<{
      report_id: string
      slot: string
      kind: 'stage' | 'closing'
      outbound_group_key: string
      outbound_group_name: string
      status: string
      attempts: number
      last_error?: string | null
      sent_at_ms?: number | null
    }>
    runs?: Array<{
      report_date: string
      slot: string
      status: 'complete' | 'partial'
      unavailable_groups: string[]
      updated_at_ms: number
    }>
  }
}

export type IntentionNotificationConfigInput = {
  enabled: boolean
  consumer_id: string
  target_groups: IntentionNotificationGroup[]
  api_token?: string | null
  startup_policy?: 'new_only' | 'include_pending' | null
}

export type MPTokenFieldName = 'key' | 'uin' | 'pass_ticket'
export type MPAuxiliaryTokenFieldName = 'appmsg_token'

export type MPTokenFieldStatus = {
  present: boolean
  value?: string | null
}

export type MPTokenStatus = {
  available: boolean
  complete: boolean
  expired: boolean
  fields: Record<MPTokenFieldName, MPTokenFieldStatus>
  auxiliary?: Partial<Record<MPAuxiliaryTokenFieldName, MPTokenFieldStatus>>
  captured_at?: string | null
  expires_at?: string | null
  source?: string
  request_host?: string
  generation?: number
}

export type MPTokenStatusResponse = {
  token: MPTokenStatus
  sources?: Record<string, string | boolean | number | null>
}

export type MPTokenMutationResponse = {
  captured: string[]
  changed: string[]
  source: string
  token: MPTokenStatus
}

export type MPTokenInput = {
  key?: string
  uin?: string
  pass_ticket?: string
  appmsg_token?: string
  cookie?: string
  user_agent?: string
}

export type MPArticleComment = {
  content_id?: string | null
  nickname?: string | null
  content: string
  create_time?: number | null
  like_count?: number | null
  is_top?: boolean
  reply_count?: number
}

export type MPArticleMetricsRequest = {
  url?: string
  html?: string
  appmsgext?: unknown
  comments?: unknown
  tokens?: MPTokenInput
  fetch_missing?: boolean
  include_comments?: boolean
  comment_limit?: number
  max_comment_pages?: number
}

export type MPArticleMetricsResponse = {
  url?: string | null
  biz?: string | null
  mid?: string | null
  idx?: string | null
  sn?: string | null
  comment_id?: string | null
  title?: string | null
  author?: string | null
  publish_time?: number | null
  read_count?: number | null
  display_read_count?: number | null
  wow_count?: number | null
  like_count?: number | null
  comment_count?: number | null
  comments?: MPArticleComment[]
  metric_basis?: Record<string, string>
  sources?: string[]
  warnings?: string[]
  collected_at?: string
}

export type MPArticleMetricsBatchRequest = {
  urls: string[]
  tokens?: MPTokenInput
  fetch_missing?: boolean
  include_comments?: boolean
  concurrency?: number
  comment_limit?: number
  max_comment_pages?: number
}

export type MPArticleMetricsBatchItem = {
  index: number
  url: string
  ok: boolean
  data?: MPArticleMetricsResponse | null
  error?: { code?: string; status?: number; detail?: string } | null
}

export type MPArticleMetricsBatchResponse = {
  total: number
  succeeded?: number
  failed?: number
  concurrency?: number
  items: MPArticleMetricsBatchItem[]
}

export type MPArticleHistoryResponse = {
  items: MPArticleMetricsResponse[]
  total?: number
  count?: number
  limit?: number
}

export type MPExportFormat = 'markdown' | 'html' | 'pdf'

export type MPExportRequest = {
  url: string
  formats: MPExportFormat[]
  localize_resources?: boolean
}

export type MPExportResponse = {
  export_id: string
  output_dir?: string
  title?: string
  source_url?: string
  files: Record<string, string>
  archive: string
  download_url?: string
  resources?: Array<Record<string, unknown>>
  warnings?: string[]
  pdf_backend?: string
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message)
  }
}

async function parseApiResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // Preserve the HTTP status when a proxy returns a non-JSON error body.
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function fetchApi<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>,
  signal?: AbortSignal
): Promise<T> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '')
      url.searchParams.set(key, String(value))
  })
  const response = await fetch(url, {
    signal,
    headers: { Accept: 'application/json' },
  })
  return parseApiResponse<T>(response)
}

export async function postApi<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal
): Promise<T> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  const response = await fetch(url, {
    method: 'POST',
    signal,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return parseApiResponse<T>(response)
}

export async function deleteApi<T>(
  path: string,
  signal?: AbortSignal
): Promise<T> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  const response = await fetch(url, {
    method: 'DELETE',
    signal,
    headers: { Accept: 'application/json' },
  })
  return parseApiResponse<T>(response)
}

export const api = {
  profile: (signal?: AbortSignal) =>
    fetchApi<ProfileResponse>('/profile', undefined, signal),
  overview: (days = 30, signal?: AbortSignal) =>
    fetchApi<OverviewResponse>('/overview', { days }, signal),
  contacts: (
    params: Record<string, string | number | undefined> = {},
    signal?: AbortSignal
  ) => fetchApi<ContactsResponse>('/contacts', params, signal),
  graph: (limit = 30, signal?: AbortSignal) =>
    fetchApi<GraphResponse>('/contacts/graph', { limit }, signal),
  chatList: (
    params: Record<string, string | number | undefined> = {},
    signal?: AbortSignal
  ) => fetchApi<ChatListResponse>('/chat-list', params, signal),
  chatSession: (
    username: string,
    params: Record<string, string | number | undefined> = {},
    signal?: AbortSignal
  ) =>
    fetchApi<ChatSessionDetailResponse>(
      `/chat-list/${encodeURIComponent(username)}`,
      params,
      signal
    ),
  media: (
    params: Record<string, string | number | undefined> = {},
    signal?: AbortSignal
  ) => fetchApi<MediaResponse>('/media', params, signal),
  sns: (
    params: Record<string, string | number | undefined> = {},
    signal?: AbortSignal
  ) => fetchApi<SnsResponse>('/sns', params, signal),
  snsStats: (signal?: AbortSignal) =>
    fetchApi<SnsStats>('/sns/stats', undefined, signal),
  snsSyncStatus: (signal?: AbortSignal) =>
    fetchApi<SnsSyncStatus>('/sns/sync/status', undefined, signal),
  snsSync: (signal?: AbortSignal) =>
    postApi<SnsSyncStatus>('/sns/sync', {}, signal),
  snsDetail: (id: string, signal?: AbortSignal) =>
    fetchApi<SnsDetailResponse>(
      `/sns/${encodeURIComponent(id)}`,
      undefined,
      signal
    ),
  snsBackups: (signal?: AbortSignal) =>
    fetchApi<SnsBackupsResponse>('/sns/backups', undefined, signal),
  snsBackup: (payload: SnsBackupRequest = {}, signal?: AbortSignal) =>
    postApi<SnsBackup>('/sns/backups', payload, signal),
  snsDeleteBackup: (id: string, signal?: AbortSignal) =>
    deleteApi<{ deleted: boolean }>(
      `/sns/backups/${encodeURIComponent(id)}`,
      signal
    ),
  contributions: (year: number, date?: string, signal?: AbortSignal) =>
    fetchApi<ContributionsResponse>('/contributions', { year, date }, signal),
  conversations: (
    params: Record<string, string | number | undefined> = {},
    signal?: AbortSignal
  ) => fetchApi<ConversationsResponse>('/conversations', params, signal),
  conversation: (id: string | number, signal?: AbortSignal) =>
    fetchApi<ConversationDetailResponse>(
      `/conversations/${encodeURIComponent(String(id))}`,
      undefined,
      signal
    ),
  message: (id: string, signal?: AbortSignal) =>
    fetchApi<MessageDetailResponse>(
      `/messages/${encodeURIComponent(id)}`,
      undefined,
      signal
    ),
  search: (q: string, scope = 'all', signal?: AbortSignal) =>
    fetchApi<SearchResponse>('/search', { q, scope, limit: 50 }, signal),
  realtimeStatus: (signal?: AbortSignal) =>
    fetchApi<RealtimeStatusResponse>('/realtime/status', undefined, signal),
  intentionNotifications: (signal?: AbortSignal) =>
    fetchApi<IntentionNotificationStatus>(
      '/intention-notifications',
      undefined,
      signal
    ),
  intentionNotificationGroups: (signal?: AbortSignal) =>
    fetchApi<{ items: IntentionNotificationGroup[]; total: number }>(
      '/intention-notifications/groups',
      undefined,
      signal
    ),
  configureIntentionNotifications: (
    payload: IntentionNotificationConfigInput,
    signal?: AbortSignal
  ) =>
    postApi<IntentionNotificationStatus>(
      '/intention-notifications/config',
      payload,
      signal
    ),
  mpTokenStatus: (signal?: AbortSignal) =>
    fetchApi<MPTokenStatusResponse>('/mp/tokens', undefined, signal),
  mpTokens: (signal?: AbortSignal) =>
    fetchApi<MPTokenStatusResponse>('/mp/tokens', undefined, signal),
  mpTokenIngest: (
    values: MPTokenInput,
    source = 'manual-import',
    signal?: AbortSignal
  ) =>
    postApi<MPTokenMutationResponse>(
      `/mp/tokens/ingest?source=${encodeURIComponent(source)}`,
      values,
      signal
    ),
  mpTokenClear: (signal?: AbortSignal) =>
    deleteApi<{ cleared: boolean; token: MPTokenStatus }>('/mp/tokens', signal),
  mpArticleMetrics: (payload: MPArticleMetricsRequest, signal?: AbortSignal) =>
    postApi<MPArticleMetricsResponse>('/mp/articles/metrics', payload, signal),
  mpMetrics: (payload: MPArticleMetricsRequest, signal?: AbortSignal) =>
    postApi<MPArticleMetricsResponse>('/mp/articles/metrics', payload, signal),
  mpArticleMetricsBatch: (
    payload: MPArticleMetricsBatchRequest,
    signal?: AbortSignal
  ) =>
    postApi<MPArticleMetricsBatchResponse>(
      '/mp/articles/metrics/batch',
      payload,
      signal
    ),
  mpMetricsBatch: (
    payload: MPArticleMetricsBatchRequest,
    signal?: AbortSignal
  ) =>
    postApi<MPArticleMetricsBatchResponse>(
      '/mp/articles/metrics/batch',
      payload,
      signal
    ),
  mpArticleHistory: (
    params: { limit?: number; url?: string } = {},
    signal?: AbortSignal
  ) =>
    fetchApi<MPArticleHistoryResponse>('/mp/articles/history', params, signal),
  mpHistory: (
    params: { limit?: number; url?: string } = {},
    signal?: AbortSignal
  ) =>
    fetchApi<MPArticleHistoryResponse>('/mp/articles/history', params, signal),
  mpExport: (payload: MPExportRequest, signal?: AbortSignal) =>
    postApi<MPExportResponse>('/mp/exports', payload, signal),
}

export function conversationExportUrl(
  format: 'csv' | 'json' | 'html',
  params: Record<string, string | undefined>
): string {
  const url = new URL(
    `${API_BASE}/conversations/export`,
    window.location.origin
  )
  url.searchParams.set('format', format)
  Object.entries(params).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value)
  })
  return `${url.pathname}${url.search}`
}

export function assetUrl(value?: string): string | undefined {
  if (!value) return undefined
  if (/^(https?:|data:|blob:)/.test(value)) return value
  if (value.startsWith('/')) return value
  return `${API_BASE}/${value.replace(/^\/+/, '')}`
}

export function externalUrl(value?: string): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
      ? url.href
      : undefined
  } catch {
    return undefined
  }
}
