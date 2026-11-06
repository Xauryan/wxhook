import { base } from '@/features/chats/api'

export async function archiveApi<T>(
  path: string,
  signal?: AbortSignal,
  body?: unknown
): Promise<T> {
  const response = await fetch(`${base}/archive${path}`, {
    signal,
    ...(body === undefined
      ? {}
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }),
  })
  const value = await response.json()
  if (!response.ok)
    throw new Error(
      typeof value.detail === 'string'
        ? value.detail
        : `请求失败（${response.status}）`
    )
  return value as T
}

export type Session = {
  username: string
  display_name: string
  is_group: number
  message_count: number
}
export type Catalog = {
  account: string
  scope: string
  snapshot_time: string
  years: number[]
  sessions: Session[]
  total_sessions: number
}
export type Scope = Pick<Catalog, 'account' | 'scope'>
export const params = (value: Record<string, string | number>) =>
  new URLSearchParams(
    Object.entries(value).map(([key, v]) => [key, String(v)])
  ).toString()
export type Count = { label: string | number; count: number; sent?: number }
export const categoryName = (value: string) =>
  (
    ({
      text: '文本',
      image: '图片',
      voice: '语音',
      video: '视频',
      sticker: '表情',
      file: '文件',
      link: '链接',
      quote: '引用回复',
      system: '系统消息',
      app: '应用消息',
      location: '位置',
      call: '通话',
      transfer: '转账',
      contact_card: '名片',
      chat_history: '合并记录',
      friend_request: '好友申请',
      status: '状态',
      other: '其他',
    }) as Record<string, string>
  )[value] || value
export type Report = Scope & {
  snapshot_time: string
  kind: string
  year: number
  session: Session | null
  totals: {
    message_count: number
    sent_count: number
    received_count: number
    active_days: number
    session_count: number
    observed_speakers: number
    text_characters: number
    first_date: string | null
    last_date: string | null
  }
  monthly: Count[]
  daily: Count[]
  hourly: Count[]
  categories: Count[]
  ranking: { username: string; name: string; count: number; sent: number }[]
  speakers: { username: string; name: string; count: number }[]
  peak_day: Count | null
  longest_streak: { days: number; start: string | null; end: string | null }
  phrases: { text: string; count: number }[]
  phrase_sample_count: number
  first_messages: {
    message_key: string
    local_date: string
    sender_name: string
    summary: string
  }[]
  notes: string[]
}
export type ExportTemplate = Scope & {
  request_id: string
  source: string
  format: string
  sessions: string[]
  all_sessions: boolean
  start_date: string
  end_date: string
  direction: string
  contact_kind: string
  use_local_edits: boolean
}
export type ExportJob = Scope & {
  id: string
  status: string
  rows: number
  format: string
  source: string
  message: string
  started_at: number
  completed_files?: number
  total_files?: number
}
export type Automation = {
  revision: number
  enabled: boolean
  interval_minutes: number
  template: ExportTemplate | null
}
export type PreviewMessage = {
  message_key: string
  content: string
  summary: string
  sender_name: string
  local_date: string
  original_hash: string
  edit: { text: string | null; note: string; revision: number }
}
export type NotificationConfig = {
  account: string
  revision: number
  enabled: boolean
  mode: 'all' | 'allowlist' | 'blocklist'
  sessions: string[]
  preview: boolean
  quiet_enabled: boolean
  quiet_start: number
  quiet_end: number
}
export type NotificationPoll = {
  config: NotificationConfig
  cursor: number
  events: { sequence: number; session: string; title: string; body: string }[]
}

export function saveFile(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
