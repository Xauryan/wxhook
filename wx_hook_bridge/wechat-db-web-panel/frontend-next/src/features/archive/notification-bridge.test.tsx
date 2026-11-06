import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { chatApi, type LiveStatus } from '@/features/chats/api'
import { NotificationBridge } from './notification-bridge'

let enabled = false
const shown: string[] = []
const status: LiveStatus = {
  account: 'notification-fixture',
  profile: { username: 'notification-fixture', display_name: '测试账号' },
  bridge_connected: true,
  receive_connected: true,
  newest_sequence: 0,
  relay_pending: 0,
  relay_dropped: 0,
}

beforeEach(() => {
  shown.length = 0
  enabled = false
  localStorage.removeItem('wechat-notification:notification-fixture')
  vi.spyOn(chatApi, 'status').mockResolvedValue(status)
  vi.stubGlobal(
    'Notification',
    class {
      static permission = 'granted'
      constructor(title: string) {
        shown.push(title)
      }
    }
  )
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), location.origin)
      if (url.pathname.endsWith('/archive/notifications/enabled'))
        return Response.json({ enabled })
      if (!url.pathname.endsWith('/archive/notifications'))
        throw new Error('Unexpected fixture network request')
      return Response.json({
        config: { account: status.account, enabled, revision: 1 },
        cursor: url.searchParams.has('after') ? 7 : 0,
        events:
          url.searchParams.get('after') === '0'
            ? [
                { sequence: 7, title: '测试新消息', body: '收到一条新消息' },
                { sequence: 7, title: '重复事件', body: '重复事件' },
              ]
            : [],
      })
    })
  )
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  await render(
    <QueryClientProvider client={client}>
      <NotificationBridge />
    </QueryClientProvider>
  )
  return client
}

it('does not display or replay notifications when disabled', async () => {
  await setup()
  await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
  expect(chatApi.status).not.toHaveBeenCalled()
  expect(shown).toEqual([])
})

it('starts from the latest cursor and deduplicates fresh events', async () => {
  enabled = true
  await setup()
  await vi.waitFor(() => expect(shown).toEqual(['测试新消息']), {
    timeout: 6000,
  })
  expect(localStorage.getItem('wechat-notification:notification-fixture')).toBe(
    '7'
  )
})
