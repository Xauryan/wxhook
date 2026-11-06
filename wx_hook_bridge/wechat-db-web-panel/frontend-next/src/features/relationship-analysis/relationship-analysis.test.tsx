import type { PropsWithChildren } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { sidebarData } from '@/components/layout/data/sidebar-data'
import { RelationshipAnalysis } from './index'

vi.mock('@/components/layout/header', () => ({
  Header: ({ children }: PropsWithChildren) => <header>{children}</header>,
}))
vi.mock('@/components/search', () => ({ Search: () => null }))
vi.mock('@/components/theme-switch', () => ({ ThemeSwitch: () => null }))
vi.mock('@/components/config-drawer', () => ({ ConfigDrawer: () => null }))

const state = {
  account: 'account-a',
  connected: true,
  fail: false,
  malformed: false,
  exportSuccess: false,
}
const requests: string[] = []

beforeEach(() => {
  state.account = 'account-a'
  state.connected = true
  state.fail = false
  state.malformed = false
  state.exportSuccess = false
  requests.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin)
      requests.push(url.pathname + url.search)
      if (url.pathname.endsWith('/status'))
        return Response.json({
          account: state.account,
          profile: { username: state.account },
          bridge_connected: state.connected,
        })
      if (url.pathname.endsWith('/relationships') && state.exportSuccess)
        return Response.json({ meta: { account: state.account } })
      if (url.pathname.endsWith('/relationships'))
        return Response.json({ detail: '账号已切换' }, { status: 409 })
      if (state.fail)
        return Response.json({ detail: '当前通讯录核验失败' }, { status: 503 })
      return new Response(`<h1>${state.account}</h1>`, {
        headers: {
          'Content-Type': state.malformed ? 'application/json' : 'text/html',
        },
      })
    })
  )
})
afterEach(() => vi.unstubAllGlobals())

async function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const screen = await render(
    <QueryClientProvider client={client}>
      <RelationshipAnalysis />
    </QueryClientProvider>
  )
  return { client, screen }
}

it('adds the requested relationship analysis navigation entry', () => {
  expect(sidebarData.navGroups.flatMap((group) => group.items)).toContainEqual(
    expect.objectContaining({
      title: '关系分析',
      url: '/analysis/relationships',
    })
  )
})

it('loads a sandboxed current-account analysis and clears it after switching accounts', async () => {
  const { client } = await setup()
  await expect
    .poll(() => document.querySelector('iframe')?.getAttribute('srcdoc'))
    .toContain('account-a')
  expect(document.querySelector('iframe')?.getAttribute('sandbox')).toBe(
    'allow-scripts allow-downloads'
  )
  state.account = 'account-b'
  await client.invalidateQueries({ queryKey: ['wechat-live-status-v2'] })
  await expect
    .poll(() => document.querySelector('iframe')?.getAttribute('srcdoc'))
    .toContain('account-b')
  expect(
    document.querySelector('iframe')?.getAttribute('srcdoc')
  ).not.toContain('account-a')
  expect(requests.some((path) => path.includes('account=account-b'))).toBe(true)
})

it('removes the previous graph when the verified connection is lost', async () => {
  const { client, screen } = await setup()
  await expect.poll(() => document.querySelector('iframe')).not.toBeNull()
  state.connected = false
  await client.invalidateQueries({ queryKey: ['wechat-live-status-v2'] })
  await expect
    .element(screen.getByText('微信未连接，请登录微信后重新读取账号。'))
    .toBeInTheDocument()
  expect(document.querySelector('iframe')).toBeNull()
})

it('shows source verification failures without a graph or a successful export', async () => {
  state.fail = true
  const { screen } = await setup()
  await expect
    .element(screen.getByRole('alert'))
    .toHaveTextContent('当前通讯录核验失败')
  expect(document.querySelector('iframe')).toBeNull()
  await expect
    .element(screen.getByRole('button', { name: '导出群与成员' }))
    .toBeDisabled()
})

it('rejects an unexpected successful content type', async () => {
  state.malformed = true
  const { screen } = await setup()
  await expect.element(screen.getByRole('alert')).toHaveTextContent('无法识别')
  expect(document.querySelector('iframe')).toBeNull()
})

it('surfaces an account-switch rejection during export', async () => {
  const { screen } = await setup()
  await expect.poll(() => document.querySelector('iframe')).not.toBeNull()
  await screen.getByRole('button', { name: '导出群与成员' }).click()
  await expect
    .element(screen.getByRole('alert'))
    .toHaveTextContent('账号已切换')
  expect(
    requests.some((path) => path.includes('/relationships?account=account-a'))
  ).toBe(true)
})

it('downloads a successful export and releases the busy state', async () => {
  state.exportSuccess = true
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, 'click')
    .mockImplementation(() => {})
  try {
    const { screen } = await setup()
    await expect.poll(() => document.querySelector('iframe')).not.toBeNull()
    await screen.getByRole('button', { name: '导出群与成员' }).click()
    await expect.poll(() => click.mock.calls.length).toBe(1)
    await expect
      .element(screen.getByRole('button', { name: '导出群与成员' }))
      .toBeEnabled()
    expect((click.mock.instances[0] as HTMLAnchorElement).href).toContain(
      '/relationships/export?account=account-a'
    )
  } finally {
    click.mockRestore()
  }
})
