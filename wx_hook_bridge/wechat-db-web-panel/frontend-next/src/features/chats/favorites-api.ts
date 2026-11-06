import { base, type MediaResult } from './api'

export type FavoriteAttachment = {
  group: number
  kind: string
  name: string
  size?: number | null
  duration?: number | null
}
export type Favorite = {
  id: string
  revision: string
  category: string
  type_label: string
  title: string
  content: string
  description: string
  summary: string
  url: string
  source_name: string
  updated_at: number
  attachments: FavoriteAttachment[]
  tags: { id: string; name: string }[]
  thumbnail_url?: string | null
  thumbnail_fallback_url?: string | null
  parse_error: boolean
  content_limited?: boolean
}
export type FavoriteList = {
  account: string
  ready: boolean
  syncing: boolean
  error?: string | null
  updated_at?: number
  revision?: string
  total: number
  counts: { all: number; media: number }
  items: Favorite[]
  tags: { id: string; name: string; count: number }[]
  tags_ready: boolean
  limited_count: number
  binding_limited: number
  recorded_attachment_bytes?: number | null
  cloud_remaining_bytes: number | null
  write_supported: boolean
  write_detail: string
}

async function request<T>(
  path: string,
  signal?: AbortSignal,
  refresh = false
): Promise<T> {
  const response = await fetch(`${base}/chat/favorites${path}`, {
    signal,
    method: refresh ? 'POST' : 'GET',
    headers: { Accept: 'application/json' },
  })
  const data = await response.json().catch(() => null)
  if (!response.ok || !data)
    throw new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : '收藏暂不可用，请稍后重试'
    )
  return data as T
}
export const favoritesApi = {
  list: (
    account: string,
    q: string,
    category: 'all' | 'media',
    tag: string,
    offset: number,
    signal?: AbortSignal
  ) =>
    request<FavoriteList>(
      `?${new URLSearchParams({ account, q, category, tag, offset: String(offset), limit: '60' })}`,
      signal
    ),
  detail: (account: string, id: string, signal?: AbortSignal) =>
    request<Favorite>(
      `/${encodeURIComponent(id)}?${new URLSearchParams({ account })}`,
      signal
    ),
  media: (account: string, id: string, group: number, signal?: AbortSignal) =>
    request<MediaResult>(
      `/${encodeURIComponent(id)}/media?${new URLSearchParams({ account, group: String(group) })}`,
      signal
    ),
  refresh: (account: string) =>
    request<FavoriteList>(
      `/refresh?${new URLSearchParams({ account })}`,
      undefined,
      true
    ),
}
