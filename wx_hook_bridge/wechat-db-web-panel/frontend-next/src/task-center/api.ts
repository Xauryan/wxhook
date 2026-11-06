export const prefix = '/api/task-center/v1'
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message)
  }
}
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(prefix + path, {
    credentials: 'same-origin',
    ...init,
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok)
    throw new ApiError(
      response.status,
      payload.message || '请求失败，请稍后重试。'
    )
  return payload as T
}
export type Me = {
  id: string
  name: string
  login: string
  owner: boolean
  csrf: string
  title: string
}
export type Member = {
  has_account: boolean
  id: string
  name: string
  login: string
  disabled: boolean
}
export type Delivery = {
  id: string
  state: 'active' | 'accepted' | 'rejected' | 'withdrawn'
  summary: string
  author: string
  pr_links: string[]
  at: number
  reviewed_by?: string
  reviewed_at?: number
  reason?: string
}
export type Task = {
  id: string
  number: string
  title: string
  month: string
  status: string
  version: number
  created_by: string | null
  lead_id: string | null
  lead_unavailable: boolean
  objective: string
  criteria: string
  priority: string
  deadline: string
  members: Record<string, { active: boolean }>
  work_items: { id: string; title: string; assignee: string; state: string }[]
  comments: { id: string; author: string; text: string; at: number }[]

  deliveries: Delivery[]
  transfer: { from: string; to: string } | null
}
export type Snapshot = {
  items: Task[]
  event_cursor: number
  next_cursor: number | null
}
export type Activity = {
  id: number
  actor: string
  action: string
  at: number
  detail: Record<string, unknown>
}

// Keep the original version and key after ambiguous failures; refetching must not duplicate a command.
export async function write<T>(
  me: Me,
  path: string,
  body: Record<string, unknown>,
  method = 'POST'
): Promise<T> {
  const semantic = { ...body }
  delete semantic.expected_version
  const slot =
    'tc-write:' +
    me.id +
    ':' +
    method +
    ':' +
    path +
    ':' +
    JSON.stringify(semantic)
  const pending: { key: string; body: Record<string, unknown> } = JSON.parse(
    sessionStorage.getItem(slot) || 'null'
  ) || { key: crypto.randomUUID(), body }
  sessionStorage.setItem(slot, JSON.stringify(pending))
  try {
    const value = await api<T>(path, {
      method,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': me.csrf,
        'Idempotency-Key': pending.key,
      },
      body: JSON.stringify(pending.body),
    })
    sessionStorage.removeItem(slot)
    return value
  } catch (error) {
    if (
      error instanceof ApiError &&
      error.status < 500 &&
      error.status !== 408 &&
      error.status !== 429
    )
      sessionStorage.removeItem(slot)
    throw error
  }
}
