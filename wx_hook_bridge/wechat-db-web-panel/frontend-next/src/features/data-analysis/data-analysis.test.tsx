import type { PropsWithChildren } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { DataAnalysis } from './index'

const state = vi.hoisted(() => ({
  navigate: vi.fn(),
  section: 'search',
  search: '?q=hello',
  ready: false,
  account: 'account-a',
  snapshot: 'snapshot-a',
  failed: false,
  requests: [] as string[],
}))
vi.mock('@tanstack/react-router', () => ({
  useLocation: () => ({
    pathname: `/analysis/${state.section}`,
    searchStr: state.search,
  }),
  useNavigate: () => state.navigate,
  Link: ({ children }: PropsWithChildren) => (
    <a href='/analysis/overview'>{children}</a>
  ),
}))
vi.mock('@/components/layout/header', () => ({
  Header: ({ children }: PropsWithChildren) => <header>{children}</header>,
}))
vi.mock('@/components/search', () => ({ Search: () => null }))
vi.mock('@/components/theme-switch', () => ({ ThemeSwitch: () => null }))
vi.mock('@/components/config-drawer', () => ({ ConfigDrawer: () => null }))

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return {
    client,
    element: (
      <QueryClientProvider client={client}>
        <DataAnalysis section={state.section} />
      </QueryClientProvider>
    ),
  }
}

beforeEach(() => {
  state.navigate.mockReset()
  state.section = 'search'
  state.search = '?q=hello'
  state.ready = false
  state.failed = false
  state.account = 'account-a'
  state.snapshot = 'snapshot-a'
  state.requests = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin)
      state.requests.push(url.pathname + url.search)
      if (state.failed) throw new Error('local API disconnected')
      let body: unknown
      if (url.pathname.endsWith('/health'))
        body = {
          status: state.ready ? 'ok' : 'setup_required',
          configured: state.ready,
          account: state.account,
          snapshot_time: state.snapshot,
        }
      else if (url.pathname.endsWith('/analysis-snapshots'))
        body = {
          capability: {
            available: false,
            account: '',
            reason: 'Fixture setup',
          },
          automation: {
            enabled: false,
            account: '',
            interval_minutes: 360,
            revision: 0,
          },
          job: null,
        }
      else if (url.pathname.endsWith('/profile'))
        body = { profile: { username: state.account } }
      else if (url.pathname.endsWith('/search'))
        body = {
          fts_used: true,
          items: [
            {
              id: state.snapshot,
              title: state.snapshot,
              snippet: 'source-backed result',
              kind: 'message',
              session_username: 'peer',
            },
          ],
        }
      else throw new Error(`Unexpected request: ${url.pathname}`)
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
  )
})

describe('integrated snapshot analysis', () => {
  it('shows setup instructions without querying unavailable statistics', async () => {
    const { element } = setup()
    const screen = await render(element)
    await expect
      .element(screen.getByText('尚未配置可用的解密快照'))
      .toBeInTheDocument()
    const setupEndpoints = ['/api/health', '/api/analysis-snapshots']
    expect(state.requests).toEqual(expect.arrayContaining(setupEndpoints))
    expect(state.requests.every((path) => setupEndpoints.includes(path))).toBe(
      true
    )
  })

  it('recovers from a disconnected API after an explicit retry', async () => {
    state.failed = true
    const { element } = setup()
    const screen = await render(element)
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('local API disconnected')
    state.failed = false
    await screen.getByRole('button', { name: '重新连接' }).click()
    await expect
      .element(screen.getByText('尚未配置可用的解密快照'))
      .toBeInTheDocument()
  })

  it('renders real search results and retains filters when navigating to conversation analysis', async () => {
    state.ready = true
    const { element } = setup()
    const screen = await render(element)
    await expect.element(screen.getByText('snapshot-a')).toBeInTheDocument()
    expect(
      state.requests.some((path) => path.includes('/search?q=hello&scope=all'))
    ).toBe(true)
    await screen.getByText('snapshot-a').click()
    expect(state.navigate).toHaveBeenCalledWith({
      to: '/analysis/$section',
      params: { section: 'conversations' },
      search: { session: 'peer', q: 'hello' },
    })
  })

  it('fetches new results after the published account or snapshot changes', async () => {
    state.ready = true
    const { client, element } = setup()
    const screen = await render(element)
    await expect.element(screen.getByText('snapshot-a')).toBeInTheDocument()
    state.account = 'account-b'
    state.snapshot = 'snapshot-b'
    await client.invalidateQueries({ queryKey: ['data-analysis', 'health'] })
    await expect.element(screen.getByText('snapshot-b')).toBeInTheDocument()
    await expect.element(screen.getByText('snapshot-a')).not.toBeInTheDocument()
  })

  it('keeps search updates inside the integrated route', async () => {
    state.ready = true
    const { element } = setup()
    const screen = await render(element)
    await expect.element(screen.getByText('snapshot-a')).toBeInTheDocument()
    await screen.getByPlaceholder('输入中文、联系人或空格拼音').fill('new term')
    await screen.getByRole('button', { name: '搜索', exact: true }).click()
    expect(state.navigate).toHaveBeenCalledWith({
      to: '/analysis/$section',
      params: { section: 'search' },
      search: { q: 'new term' },
    })
  })
})
