import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SnapshotPanel, type SnapshotJob } from './snapshot-panel'

let job: SnapshotJob | null
let available = true
let failure = false
let enabled = false
const writes: Array<{ path: string; body: Record<string, unknown> }> = []

beforeEach(() => {
  job = null
  available = true
  failure = false
  enabled = false
  writes.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), location.origin).pathname
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body))
        writes.push({ path, body })
        if (failure)
          return new Response(JSON.stringify({ detail: '微信账号已变化' }), {
            status: 409,
          })
        if (path.endsWith('/automation')) enabled = Boolean(body.enabled)
        else if (!path.endsWith('/cancel'))
          job = {
            id: 'job1',
            account: 'wxid_test',
            status: 'running',
            stage: 'decrypting',
            message: '正在解密并校验数据库。',
            completed_files: 2,
            total_files: 38,
            file: 'message/message_0.db',
            file_bytes: 50,
            file_total_bytes: 100,
          }
      }
      return new Response(
        JSON.stringify({
          capability: {
            available,
            account: 'wxid_test',
            reason: '请登录本机微信',
          },
          automation: {
            enabled,
            account: enabled ? 'wxid_test' : '',
            interval_minutes: 360,
            revision: 3,
          },
          job,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    })
  )
})

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return {
    client,
    screen: render(
      <QueryClientProvider client={client}>
        <SnapshotPanel />
      </QueryClientProvider>
    ),
  }
}

it('starts one account-bound job, displays progress, and requests cancellation', async () => {
  const { screen: pending } = setup()
  const screen = await pending
  await expect
    .element(screen.getByRole('button', { name: '一键生成快照' }))
    .toBeEnabled()
  await screen.getByRole('button', { name: '一键生成快照' }).click()
  await expect
    .element(screen.getByText('数据库 2 / 38 · message/message_0.db'))
    .toBeInTheDocument()
  await expect
    .element(screen.getByRole('button', { name: '正在生成快照' }))
    .toBeDisabled()
  await screen.getByRole('button', { name: '取消任务' }).click()
  await expect
    .element(screen.getByRole('button', { name: '正在取消…' }))
    .toBeDisabled()
  expect(writes[0].body).toEqual({ account: 'wxid_test' })
  expect(writes[1].path).toMatch(/\/job1\/cancel$/)
})

it('keeps generation disabled while WeChat is disconnected', async () => {
  available = false
  const { screen: pending } = setup()
  const screen = await pending
  await expect.element(screen.getByText('请登录本机微信')).toBeInTheDocument()
  await expect
    .element(screen.getByRole('button', { name: '一键生成快照' }))
    .toBeDisabled()
})

it('persists automation with selected interval and revision', async () => {
  const { screen: pending } = setup()
  const screen = await pending
  await expect
    .element(screen.getByRole('checkbox', { name: '自动更新快照' }))
    .toBeEnabled()
  await screen
    .getByRole('combobox', { name: '快照自动更新间隔' })
    .selectOptions('60')
  await screen.getByRole('checkbox', { name: '自动更新快照' }).click()
  await expect
    .element(
      screen.getByText(
        '已绑定 wxid_test；应用后台运行且该账号在线时执行，切换账号后等待。'
      )
    )
    .toBeInTheDocument()
  expect(writes[0].body).toEqual({
    enabled: true,
    account: 'wxid_test',
    interval_minutes: 60,
    revision: 3,
  })
})

it('shows a rejected account switch and allows retry', async () => {
  failure = true
  const { screen: pending } = setup()
  const screen = await pending
  await expect
    .element(screen.getByRole('button', { name: '一键生成快照' }))
    .toBeEnabled()
  await screen.getByRole('button', { name: '一键生成快照' }).click()
  await expect
    .element(screen.getByRole('alert'))
    .toHaveTextContent('微信账号已变化')
  await expect
    .element(screen.getByRole('button', { name: '一键生成快照' }))
    .toBeEnabled()
})

it('invalidates snapshot analysis only after successful publication', async () => {
  job = {
    id: 'done1',
    account: 'wxid_test',
    status: 'succeeded',
    stage: 'complete',
    message: '快照已生成并通过统计校验。',
    completed_files: 38,
    total_files: 38,
    message_count: 12345,
  }
  const { client, screen: pending } = setup()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const screen = await pending
  await expect
    .element(screen.getByText('已解析 12,345 条消息'))
    .toBeInTheDocument()
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['data-analysis'] })
})
