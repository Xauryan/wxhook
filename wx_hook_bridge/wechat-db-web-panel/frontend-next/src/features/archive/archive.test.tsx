import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import type { Catalog, NotificationConfig, Report } from './api'
import { Exports, NotificationForm, Reports } from './index'
import { reportHtml } from './report-export'

const catalog: Catalog = {
  account: 'self',
  scope: 'scope1',
  snapshot_time: '2026-10-15',
  years: [2026, 2025],
  total_sessions: 2,
  sessions: [
    {
      username: 'friend',
      display_name: '测试好友',
      is_group: 0,
      message_count: 10,
    },
    {
      username: 'group@chatroom',
      display_name: '测试群',
      is_group: 1,
      message_count: 20,
    },
  ],
}
const writes: Array<Record<string, unknown>> = []
let fail = false
let running = false
let reportKind = ''
const report: Report = {
  account: 'self',
  scope: 'scope1',
  snapshot_time: '2026-10-15',
  kind: 'annual',
  year: 2026,
  session: null,
  totals: {
    message_count: 30,
    sent_count: 10,
    received_count: 20,
    active_days: 2,
    session_count: 2,
    observed_speakers: 3,
    text_characters: 100,
    first_date: '2026-01-01',
    last_date: '2026-01-02',
  },
  monthly: [{ label: '2026-01', count: 30 }],
  daily: [],
  hourly: [],
  categories: [],
  ranking: [],
  speakers: [],
  peak_day: null,
  longest_streak: { days: 2, start: null, end: null },
  phrases: [],
  phrase_sample_count: 0,
  first_messages: [],
  notes: ['仅统计本地快照'],
} as Report

beforeEach(() => {
  writes.length = 0
  fail = false
  running = false
  reportKind = ''
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), location.origin)
      if (init?.method === 'POST') writes.push(JSON.parse(String(init.body)))
      if (fail)
        return Response.json(
          { detail: '账号或快照已变化，请重新读取。' },
          { status: 409 }
        )
      if (url.pathname.endsWith('/report')) {
        reportKind = url.searchParams.get('kind') || ''
        return Response.json(report)
      }
      if (url.pathname.endsWith('/automation'))
        return Response.json({
          revision: 2,
          enabled: false,
          interval_minutes: 1440,
          template: null,
        })
      if (url.pathname.endsWith('/preview'))
        return Response.json({ messages: [] })
      if (url.pathname.endsWith('/exports'))
        return Response.json(
          init?.method === 'POST'
            ? { id: 'job' }
            : {
                jobs: running
                  ? [
                      {
                        id: 'job',
                        account: 'self',
                        scope: 'scope1',
                        status: 'running',
                        format: 'json',
                        source: 'messages',
                        rows: 100,
                        message: '正在导出',
                      },
                    ]
                  : [],
              }
        )
      return Response.json({})
    })
  )
})

it('exports a readable self-contained report with escaped source data', () => {
  const output = reportHtml({ ...report, notes: ['<script>alert(1)</script>'] })
  expect(output).toContain('年度与长期报告')
  expect(output).toContain('月度趋势')
  expect(output).not.toContain('<script>')
  expect(output).toContain('&lt;script&gt;')
})

async function setup(children: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

it('requires a selection and posts snapshot-bound batch filters', async () => {
  const screen = await setup(<Exports catalog={catalog} />)
  await expect
    .element(screen.getByRole('button', { name: '创建后台导出' }))
    .toBeDisabled()
  await screen.getByRole('checkbox', { name: '选择会话：测试好友' }).click()
  await screen
    .getByRole('combobox', { name: '文件格式' })
    .selectOptions('chatlab-jsonl')
  await screen.getByRole('button', { name: '创建后台导出' }).click()
  await vi.waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]).toMatchObject({
    account: 'self',
    scope: 'scope1',
    sessions: ['friend'],
    all_sessions: false,
    format: 'chatlab-jsonl',
    use_local_edits: false,
  })
  expect(writes[0].request_id).toMatch(/^[a-f0-9]{32}$/)
})

it('offers cancellation for running jobs and blocks duplicate creation', async () => {
  running = true
  const screen = await setup(<Exports catalog={catalog} />)
  await screen.getByRole('checkbox', { name: '选择会话：测试好友' }).click()
  await expect
    .element(screen.getByRole('button', { name: '创建后台导出' }))
    .toBeDisabled()
  await screen.getByRole('button', { name: '取消任务' }).click()
  await vi.waitFor(() => expect(writes).toHaveLength(1))
})

it('shows server scope errors instead of claiming export success', async () => {
  const screen = await setup(<Exports catalog={catalog} />)
  await screen.getByRole('checkbox', { name: '选择会话：测试好友' }).click()
  fail = true
  await screen.getByRole('button', { name: '创建后台导出' }).click()
  await expect
    .element(screen.getByText('账号或快照已变化，请重新读取。').first())
    .toBeInTheDocument()
  await expect
    .element(screen.getByRole('link', { name: '下载 ZIP' }))
    .not.toBeInTheDocument()
})

it('does not request a dual report until a private session is selected', async () => {
  const screen = await setup(<Reports catalog={catalog} />)
  await expect.element(screen.getByText('消息总数')).toBeInTheDocument()
  await screen.getByRole('combobox', { name: '报告类型' }).selectOptions('dual')
  await expect
    .element(screen.getByText('选择一个会话后生成报告。'))
    .toBeInTheDocument()
  expect(reportKind).toBe('annual')
  await screen
    .getByRole('combobox', { name: '选择联系人' })
    .selectOptions('friend')
  await vi.waitFor(() => expect(reportKind).toBe('dual'))
})

it('keeps notifications disabled and preview hidden until explicitly saved', async () => {
  const initial: NotificationConfig = {
    account: 'self',
    revision: 0,
    enabled: false,
    mode: 'all',
    sessions: [],
    preview: false,
    quiet_enabled: false,
    quiet_start: 23,
    quiet_end: 8,
  }
  const screen = await setup(<NotificationForm initial={initial} />)
  await expect
    .element(screen.getByRole('checkbox', { name: '启用本机提醒' }))
    .not.toBeChecked()
  await expect
    .element(
      screen.getByRole('checkbox', { name: '在提醒中显示消息摘要（默认隐藏）' })
    )
    .not.toBeChecked()
  await screen.getByRole('checkbox', { name: '启用本机提醒' }).click()
  expect(writes).toHaveLength(0)
  await screen.getByRole('button', { name: '保存提醒设置' }).click()
  await vi.waitFor(() =>
    expect(writes[0]).toMatchObject({
      account: 'self',
      enabled: true,
      preview: false,
      revision: 0,
    })
  )
})
