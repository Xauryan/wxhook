import { base } from './api'

export type MomentMedia = {
  id: string
  kind: string
  width?: number
  height?: number
  state: string
  detail: string
  thumbnail_url?: string | null
  url?: string | null
}
export type Moment = {
  id: string
  username: string
  display_name: string
  avatar_url?: string
  content: string
  create_time: number
  is_owner: boolean
  location: string
  content_type: number | string
  media: MomentMedia[]
  likes: { username: string; display_name: string }[]
  comments: {
    id: string
    username: string
    display_name: string
    content: string
    create_time: number
  }[]
  like_count: number
  comment_count: number
  interactions_limited: boolean
}
export type MomentList = {
  account: string
  ready: boolean
  syncing: boolean
  error?: string | null
  updated_at?: number
  revision?: string
  total: number
  items: Moment[]
  next_cursor: string | null
  has_more: boolean
  source: string
  write_supported: boolean
  write_detail: string
  limited_count?: number
}

export class MomentsApiError extends Error {
  constructor(
    message: string,
    public status: number
  ) {
    super(message)
    this.name = 'MomentsApiError'
  }
}

async function request(
  path: string,
  account: string,
  signal?: AbortSignal,
  refresh = false
) {
  const response = await fetch(`${base}/chat/moments${path}`, {
    signal,
    method: refresh ? 'POST' : 'GET',
    headers: { Accept: 'application/json' },
  })
  const data = await response.json().catch(() => null)
  if (!response.ok || !data)
    throw new MomentsApiError(
      typeof data?.detail === 'string'
        ? data.detail
        : '朋友圈暂不可用，请稍后重试',
      response.status
    )
  if (data.account !== account)
    throw new Error('微信账号已变化，请重新读取朋友圈')
  if (refresh && data.error && !data.syncing) throw new Error(data.error)
  return data as MomentList
}

export const momentsApi = {
  list: (
    account: string,
    q: string,
    scope: 'all' | 'self',
    cursor: string | null,
    signal?: AbortSignal
  ) =>
    request(
      `?${new URLSearchParams({ account, q, scope, cursor: cursor || '', limit: '20' })}`,
      account,
      signal
    ).then((data) => {
      if (cursor && data.error) throw new Error(data.error)
      return data
    }),
  refresh: (account: string) =>
    request(
      `/refresh?${new URLSearchParams({ account })}`,
      account,
      undefined,
      true
    ),
}
