import { base } from '@/features/chats/api'

export type ReportConfig = {
  enabled: boolean
  ai_profile_id: string
  schedule_time: string
  window_hours: number
  output_format: 'image' | 'text' | 'pdf'
  skin: 'md3' | 'anime' | 'newspaper' | 'art' | 'scrapbook'
  theme: 'light' | 'dark' | 'auto'
  manual_enabled: boolean
  admin_ids: string[]
  max_messages: number
  min_messages: number
  max_topics: number
  max_quotes: number
  max_titles: number
  prompt_topics: string
  prompt_titles: string
  prompt_quotes: string
}
export type ReportRule = {
  account: string
  room: string
  name: string
  revision: number
  config: ReportConfig
  next_run: number
}
export type ReportJob = {
  id: string
  room: string
  name: string
  start: number
  end: number
  trigger: string
  status: string
  detail: string
  created_at: number
}
export type ReportStatus = {
  account: string
  profile: { display_name: string }
  rules: ReportRule[]
  jobs: ReportJob[]
  prompts: { topics: string; titles: string; quotes: string }
  running: boolean
  error: string | null
}
export type GroupItem = { id: string; name: string }

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${base}/group-reports${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const data = await response.json().catch(() => null)
  if (!response.ok || !data)
    throw new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : `群日报请求失败（${response.status}）`
    )
  return data
}
export const reportApi = {
  status: () => request<ReportStatus>(''),
  groups: (account: string) =>
    request<{ items: GroupItem[] }>(
      `/groups?${new URLSearchParams({ account })}`
    ),
  members: (account: string, room: string) =>
    request<{ items: GroupItem[] }>(
      `/members?${new URLSearchParams({ account, room })}`
    ),
  save: (rule: {
    account: string
    room: string
    revision: number
    config: ReportConfig
  }) => request<ReportRule>('/rules', rule),
  run: (
    account: string,
    room: string,
    start: string,
    end: string,
    send: boolean,
    requestId: string
  ) =>
    request<ReportJob>('/jobs', {
      account,
      room,
      start,
      end,
      send,
      request_id: requestId,
    }),
  send: (id: string) => request<ReportJob>(`/jobs/${id}/send`, {}),
  file: (id: string, type: 'png' | 'pdf' | 'txt', download = false) =>
    `${base}/group-reports/jobs/${id}/files/report.${type}${download ? '?download=true' : ''}`,
}
