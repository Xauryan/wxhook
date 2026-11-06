import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import '@/styles/index.css'
import '@/styles/workspace.css'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page, userEvent } from 'vitest/browser'
import { DirectionProvider } from '@/context/direction-provider'
import { FontProvider } from '@/context/font-provider'
import { ThemeProvider } from '@/context/theme-provider'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'
import { chatApi, type Recall, type Session, type SessionDetail } from './api'
import { Composer } from './composer'
import { Chats } from './index'

vi.mock('./use-live-events', () => ({ useLiveEvents: () => true }))
vi.mock('@/features/archive/notification-bridge', () => ({
  NotificationBridge: () => null,
}))

const peer: Session = {
  username: 'layout-fixture-peer',
  display_name: 'AAA',
  is_group: false,
  message_count: 2,
  can_send: true,
  summary: '测试撤回',
}
const group: Session = {
  ...peer,
  username: 'layout-fixture-group',
  display_name: '布局测试群',
  is_group: true,
}
const detail: SessionDetail = {
  account: 'layout-fixture',
  session: peer,
  messages: [
    {
      id: '1',
      order_time: 1000,
      revision: 1,
      category: 'text',
      content: '测试',
      is_self: true,
    },
    {
      id: '2',
      order_time: 1001,
      revision: 2,
      category: 'text',
      content: '测试撤回',
      sender_name: 'AAA',
    },
  ],
  next_cursor: null,
  boundary_cursor: null,
  has_more: false,
  revision: 2,
  elapsed_ms: 1,
  history_revision: 'fixture',
}

beforeEach(async () => {
  await page.viewport(1280, 800)
  sessionStorage.removeItem(
    'live-chat-draft:layout-fixture:layout-fixture-peer'
  )
  vi.spyOn(chatApi, 'status').mockResolvedValue({
    account: 'layout-fixture',
    profile: { username: 'layout-fixture', display_name: '本人' },
    bridge_connected: true,
    receive_connected: true,
    send_enabled: true,
    contacts_verified: true,
    newest_sequence: 0,
    relay_pending: 0,
    relay_dropped: 0,
  })
  vi.spyOn(chatApi, 'list').mockImplementation(
    async (_account, search, scope) => {
      const items = [peer, group].filter(
        (item) =>
          item.display_name.includes(search) &&
          (scope === 'all' || item.is_group === (scope === 'groups'))
      )
      return { items, total: items.length }
    }
  )
  vi.spyOn(chatApi, 'detail').mockResolvedValue(detail)
  vi.spyOn(chatApi, 'changes').mockResolvedValue({
    account: 'layout-fixture',
    messages: [],
    deleted: [],
    revision: 2,
    has_more: false,
  })
  vi.spyOn(chatApi, 'send').mockResolvedValue({
    request_id: 'fixture-receipt',
    status: 'accepted',
    detail: '模拟受理',
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function workspace() {
  const root = createRootRoute({ component: AuthenticatedLayout })
  const route = createRoute({
    getParentRoute: () => root,
    path: '/chats',
    component: Chats,
  })
  const router = createRouter({
    routeTree: root.addChildren([route]),
    history: createMemoryHistory({ initialEntries: ['/chats'] }),
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const screen = await render(
    <QueryClientProvider client={client}>
      <ThemeProvider defaultTheme='light' storageKey='layout-fixture-theme'>
        <FontProvider>
          <DirectionProvider>
            <RouterProvider router={router} />
          </DirectionProvider>
        </FontProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
  await expect
    .element(screen.getByRole('button', { name: /AAA.*测试撤回/ }))
    .toBeVisible()
  return screen
}

const recall: Recall = {
  id: '1',
  server_id: '9007199254740993123',
  recalled_at: 1010,
  observed_at: 1010,
  notice: '对方撤回了一条消息',
  sender_name: 'AAA',
  session: peer,
  original: {
    ...detail.messages[1],
    content: '完整原文\n第二行 <script>literal</script>🙂',
  },
  jump_server_id: '9007199254740993123',
  jump_message_id: null,
}

describe('recall records with isolated API fixtures', () => {
  it('opens the saved original and highlights its exact chat position', async () => {
    const calls = vi.spyOn(chatApi, 'recalls').mockResolvedValue({
      account: 'layout-fixture',
      items: [recall],
      total: 1,
      next_cursor: null,
      revision: 1,
    })
    const detailCalls = vi
      .spyOn(chatApi, 'detail')
      .mockImplementation(async () => ({
        ...detail,
        messages: [
          {
            ...detail.messages[1],
            server_id: recall.server_id,
            category: 'system',
            content: recall.notice,
          },
        ],
      }))
    const screen = await workspace()
    await userEvent.click(
      screen.getByRole('button', { name: '撤回记录', exact: true })
    )
    await expect.element(screen.getByText(/完整原文/)).toBeVisible()
    expect(document.querySelector('.recall-text')?.textContent).toBe(
      recall.original!.content
    )
    expect(document.querySelector('.recall-text script')).toBeNull()
    expect(calls).toHaveBeenCalledWith(
      'layout-fixture',
      '',
      null,
      expect.any(AbortSignal)
    )
    await userEvent.click(screen.getByRole('button', { name: '查看聊天位置' }))
    await expect
      .element(screen.getByRole('region', { name: 'AAA的聊天记录' }))
      .toBeVisible()
    await expect
      .element(page.getByText(recall.notice, { exact: true }))
      .toBeVisible()
    await expect
      .poll(() =>
        document
          .querySelector('.message-highlight')
          ?.getAttribute('data-message-id')
      )
      .toBe('2')
    expect(
      detailCalls.mock.calls.some((call) => call[4] === recall.server_id)
    ).toBe(true)
  })

  it('shows an uncaptured body honestly and locates the notice by row identity', async () => {
    await page.viewport(375, 812)
    vi.spyOn(chatApi, 'recalls').mockResolvedValue({
      account: 'layout-fixture',
      items: [
        {
          ...recall,
          original: null,
          recalled_at: null,
          jump_server_id: null,
          jump_message_id: '2',
        },
      ],
      total: 1,
      next_cursor: null,
      revision: 1,
    })
    const detailCalls = vi.spyOn(chatApi, 'detail').mockResolvedValue({
      ...detail,
      messages: [
        { ...detail.messages[1], category: 'system', content: recall.notice },
      ],
    })
    const screen = await workspace()
    await userEvent.click(
      screen.getByRole('button', { name: '撤回记录', exact: true })
    )
    await expect
      .element(screen.getByText('原文未捕获', { exact: true }))
      .toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(375)
    await expect.element(screen.getByText(/发现于/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: '查看聊天位置' }))
    await expect
      .poll(() =>
        document
          .querySelector('.message-highlight')
          ?.getAttribute('data-message-id')
      )
      .toBe('2')
    expect(detailCalls.mock.calls.some((call) => call[6] === '2')).toBe(true)
  })
})

describe('reference desktop chat layout, isolated fixtures only', () => {
  it('keeps the admin sidebar and header around the reference chat layout', async () => {
    const screen = await workspace()
    await userEvent.click(screen.getByRole('button', { name: /AAA.*测试撤回/ }))
    await expect
      .element(screen.getByRole('textbox', { name: '聊天消息' }))
      .toBeVisible()
    const box = (selector: string) =>
      screen.container.querySelector(selector)!.getBoundingClientRect()
    expect(box('.chat-navigation').width).toBe(60)
    expect(box('.session-panel').width).toBe(212)
    expect(box('.conversation').left - box('.chat-navigation').left).toBe(272)
    expect(box('.conversation-heading').height).toBe(68)
    expect(box('.chat-composer').height).toBe(168)
    expect(box('.composer-send').width).toBe(120)
    expect(box('.session-item').height).toBe(68)
    await expect
      .element(screen.getByRole('link', { name: '微信聊天', exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('button', { name: '切换主题' }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('button', { name: '打开外观设置' }))
      .toBeVisible()
    expect(
      screen.container.querySelector('[data-sidebar="sidebar"]')
    ).not.toBeNull()
    expect(box('.chat-page').left).toBeGreaterThan(200)
    expect(box('.chat-page').top).toBeGreaterThanOrEqual(64)
    expect(box('.chat-page').bottom).toBeLessThanOrEqual(window.innerHeight)
    expect(
      screen.container.querySelector('.session-item.selected')
    ).not.toBeNull()
    expect(
      getComputedStyle(
        screen.container.querySelector('.is-self .message-bubble')!
      ).backgroundColor
    ).toBe('rgb(149, 237, 104)')
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      window.innerWidth
    )
    expect(chatApi.send).not.toHaveBeenCalled()
  })

  it('retains contact search, conversation filtering and message search', async () => {
    const screen = await workspace()
    await userEvent.fill(
      screen.getByRole('textbox', { name: '搜索微信会话' }),
      'AAA'
    )
    await vi.waitFor(() =>
      expect(screen.container.querySelectorAll('.session-item')).toHaveLength(1)
    )
    await userEvent.click(screen.getByRole('button', { name: '清空会话搜索' }))
    await userEvent.click(
      screen.getByRole('button', { name: '会话操作与筛选' })
    )
    await userEvent.click(screen.getByRole('menuitemradio', { name: '私聊' }))
    await vi.waitFor(() =>
      expect(chatApi.list).toHaveBeenLastCalledWith(
        'layout-fixture',
        '',
        'contacts',
        0,
        expect.any(AbortSignal)
      )
    )
    await userEvent.click(screen.getByRole('button', { name: /AAA.*测试撤回/ }))
    await userEvent.click(
      screen.getByRole('button', { name: '搜索当前已加载消息' })
    )
    await userEvent.fill(
      screen.getByRole('textbox', { name: '搜索已加载消息' }),
      '撤回'
    )
    await vi.waitFor(() =>
      expect(
        screen.container.querySelectorAll('[data-message-id]')
      ).toHaveLength(1)
    )
    expect(chatApi.send).not.toHaveBeenCalled()
  })

  it('has no horizontal overflow on mobile and returns to the session list', async () => {
    await page.viewport(375, 812)
    const screen = await workspace()
    await userEvent.click(screen.getByRole('button', { name: /AAA.*测试撤回/ }))
    await expect
      .element(screen.getByRole('button', { name: '返回会话列表' }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('button', { name: '发送', exact: true }))
      .toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(375)
    const conversation = screen.container
      .querySelector('.conversation')!
      .getBoundingClientRect()
    expect(conversation.width).toBeGreaterThan(300)
    expect(conversation.right).toBeLessThanOrEqual(375)
    expect(
      screen.container.querySelector('.composer-send')!.getBoundingClientRect()
        .bottom
    ).toBeLessThanOrEqual(812)
    await expect
      .element(screen.getByRole('button', { name: '打开外观设置' }))
      .toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: '返回会话列表' }))
    await expect
      .element(screen.getByRole('textbox', { name: '搜索微信会话' }))
      .toBeVisible()
  })

  it('keeps the back button when the admin sidebar narrows a desktop viewport', async () => {
    await page.viewport(850, 710)
    const screen = await workspace()
    await userEvent.click(screen.getByRole('button', { name: /AAA.*测试撤回/ }))
    await expect
      .element(screen.getByRole('button', { name: '返回会话列表' }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('link', { name: '微信聊天', exact: true }))
      .toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(850)
  })

  it('reports a local service failure without inventing a WeChat login or queue state', async () => {
    vi.mocked(chatApi.status).mockRejectedValue(
      new Error('本地接口暂不可用（HTTP 502）')
    )
    const root = createRootRoute({ component: AuthenticatedLayout })
    const route = createRoute({
      getParentRoute: () => root,
      path: '/chats',
      component: Chats,
    })
    const router = createRouter({
      routeTree: root.addChildren([route]),
      history: createMemoryHistory({ initialEntries: ['/chats'] }),
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, retryDelay: 0 } },
    })
    const screen = await render(
      <QueryClientProvider client={client}>
        <ThemeProvider defaultTheme='light' storageKey='layout-fixture-theme'>
          <FontProvider>
            <DirectionProvider>
              <RouterProvider router={router} />
            </DirectionProvider>
          </FontProvider>
        </ThemeProvider>
      </QueryClientProvider>
    )
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('本地服务无法连接')
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('HTTP 502')
    expect(screen.container.textContent).not.toContain('请登录微信并启动桥接')
    expect(screen.container.textContent).not.toContain('微信未连接')
    expect(screen.container.textContent).not.toContain('积压 0 条')
    expect(screen.container.querySelector('.chat-state .spin')).toBeNull()
    expect(chatApi.list).not.toHaveBeenCalled()
    let finishStatus!: (
      value: Awaited<ReturnType<typeof chatApi.status>>
    ) => void
    vi.mocked(chatApi.status).mockImplementation(
      () =>
        new Promise((resolve) => {
          finishStatus = resolve
        })
    )
    await userEvent.click(
      screen.getByRole('button', { name: '重试', exact: true })
    )
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('本地服务无法连接')
    expect(screen.container.textContent).not.toContain(
      '正在连接本地服务并读取微信状态'
    )
    expect(screen.container.querySelector('.chat-state .spin')).toBeNull()
    finishStatus({
      account: 'layout-fixture',
      profile: { username: 'layout-fixture' },
      bridge_connected: true,
      receive_connected: true,
      send_enabled: true,
      contacts_verified: true,
      newest_sequence: 0,
      relay_pending: 0,
      relay_dropped: 0,
    })
    await expect
      .element(screen.getByRole('button', { name: /AAA.*测试撤回/ }))
      .toBeVisible()
    expect(screen.container.textContent).not.toContain('本地服务无法连接')
    expect(chatApi.send).not.toHaveBeenCalled()
  })

  it('keeps a plain-text server failure uncertain, retains the draft and never resends', async () => {
    vi.mocked(chatApi.send).mockRestore()
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('Internal Server Error', { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)
    const screen = await render(
      <Composer account='layout-fixture' username={peer.username} enabled />
    )
    await userEvent.fill(
      screen.getByRole('textbox', { name: '聊天消息' }),
      'failure fixture only'
    )
    await userEvent.click(
      screen.getByRole('button', { name: '发送', exact: true })
    )
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('本地接口暂不可用（HTTP 500）')
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('不会自动重发')
    await expect
      .element(screen.getByRole('textbox', { name: '聊天消息' }))
      .toHaveValue('failure fixture only')
    await expect
      .element(screen.getByRole('textbox', { name: '聊天消息' }))
      .toBeDisabled()
    await expect
      .element(screen.getByRole('button', { name: '查询状态' }))
      .toBeVisible()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const saved = JSON.parse(
      sessionStorage.getItem(
        'live-chat-draft:layout-fixture:layout-fixture-peer'
      )!
    )
    expect(saved.uncertain).toBe(true)
    expect(saved.request).toBe(
      JSON.parse(fetchMock.mock.calls[0][1].body).request_id
    )
    fetchMock.mockResolvedValue(
      Response.json({ detail: '未找到发送请求' }, { status: 404 })
    )
    await userEvent.click(screen.getByRole('button', { name: '查询状态' }))
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('未找到发送请求')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1][1].method).toBeUndefined()
    await expect
      .element(screen.getByRole('textbox', { name: '聊天消息' }))
      .toBeDisabled()
  })

  it('inserts a text emoticon, preserves draft state and only submits through the mocked API', async () => {
    const screen = await render(
      <Composer account='layout-fixture' username={peer.username} enabled />
    )
    await expect
      .element(screen.getByRole('button', { name: '发送', exact: true }))
      .toBeDisabled()
    await userEvent.fill(
      screen.getByRole('textbox', { name: '聊天消息' }),
      '你好'
    )
    await userEvent.click(screen.getByRole('button', { name: '插入表情' }))
    await userEvent.click(
      page.getByRole('button', { name: '[微笑]', exact: true })
    )
    await expect
      .element(screen.getByRole('textbox', { name: '聊天消息' }))
      .toHaveValue('你好[微笑]')
    expect(
      JSON.parse(
        sessionStorage.getItem(
          'live-chat-draft:layout-fixture:layout-fixture-peer'
        )!
      ).text
    ).toBe('你好[微笑]')
    await userEvent.click(
      screen.getByRole('button', { name: '发送', exact: true })
    )
    expect(chatApi.send).toHaveBeenCalledWith(
      'layout-fixture',
      peer.username,
      '你好[微笑]',
      expect.any(String)
    )
    await expect
      .element(screen.getByRole('textbox', { name: '聊天消息' }))
      .toHaveValue('')
    await expect
      .element(screen.getByRole('button', { name: '发送文件（暂未接入）' }))
      .toBeDisabled()
    await expect
      .element(screen.getByRole('button', { name: '语音通话（暂未接入）' }))
      .toBeDisabled()
  })
})
