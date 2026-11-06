import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { chatApi, type LiveStatus, type SessionDetail } from './api'
import { useAccountBoundary } from './use-account-boundary'
import { useLiveEvents } from './use-live-events'

class FixtureEvents extends EventTarget {
  static CLOSED = 2
  readyState = 1
  static instances: FixtureEvents[] = []
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor(public url: string) {
    super()
    FixtureEvents.instances.push(this)
  }
  emit(type: string, data: object, revision = '') {
    this.dispatchEvent(
      new MessageEvent(type, {
        data: JSON.stringify(data),
        lastEventId: revision,
      })
    )
  }
}

const initial = (username: string, revision = 5): SessionDetail => ({
  account: 'fixture',
  session: {
    username,
    display_name: username,
    is_group: false,
    message_count: 0,
  },
  messages: [],
  revision,
  next_cursor: null,
  boundary_cursor: null,
  has_more: false,
  elapsed_ms: 1,
  history_revision: 'same-files',
})
const settle = () => new Promise((resolve) => setTimeout(resolve, 220))

async function setup() {
  FixtureEvents.instances = []
  vi.stubGlobal('EventSource', FixtureEvents)
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  cache.setQueryData<LiveStatus>(['wechat-live-status-v2'], {
    account: 'fixture',
    profile: { username: 'fixture' },
    bridge_connected: true,
    receive_connected: true,
    newest_sequence: 5,
    relay_pending: 0,
    relay_dropped: 0,
    contacts_revision: 1,
    history_revision: 'same-files',
  })
  const peer = vi.fn(async () => initial('peer', 10))
  const other = vi.fn(async () => initial('other', 10))
  const list = vi.fn(async () => ({ items: [], total: 0 }))
  const media = vi.fn(async () => ({ items: [], preferred: 0, elapsed_ms: 1 }))
  cache.setQueryData(['wechat-flow', 'fixture', 'peer'], initial('peer'))
  cache.setQueryData(['wechat-flow', 'fixture', 'other'], initial('other'))
  cache.setQueryData(['wechat-roster-v2', 'fixture', 'list', '', 'all'], {
    items: [],
    total: 0,
  })
  cache.setQueryData(['wechat-media', 'fixture', '1', 1], {
    items: [],
    preferred: 0,
    elapsed_ms: 1,
  })
  function Harness() {
    const connected = useLiveEvents('fixture')
    useQuery({ queryKey: ['wechat-flow', 'fixture', 'peer'], queryFn: peer })
    useQuery({ queryKey: ['wechat-flow', 'fixture', 'other'], queryFn: other })
    useQuery({
      queryKey: ['wechat-roster-v2', 'fixture', 'list', '', 'all'],
      queryFn: list,
    })
    useQuery({ queryKey: ['wechat-media', 'fixture', '1', 1], queryFn: media })
    return <output>{String(connected)}</output>
  }
  function AccountBoundary() {
    const status = useQuery({
      queryKey: ['wechat-live-status-v2'],
      queryFn: ({ signal }) => chatApi.status(signal),
    })
    useAccountBoundary(
      status.data?.account ?? '',
      status.data?.bridge_connected === true
    )
    return status.data?.account === 'fixture' ? (
      <Harness />
    ) : (
      <output>account changed</output>
    )
  }
  const screen = await render(
    <QueryClientProvider client={cache}>
      <AccountBoundary />
    </QueryClientProvider>
  )
  const events = FixtureEvents.instances[0]
  events.onopen?.()
  await settle()
  peer.mockClear()
  other.mockClear()
  list.mockClear()
  media.mockClear()
  return { cache, peer, other, list, media, events, screen }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('scoped event loading without real network or contacts', () => {
  it('refreshes recall records only for the matching account', async () => {
    const { cache, events } = await setup()
    const key = ['wechat-recalls', 'fixture', '']
    cache.setQueryData(key, { items: [], total: 0 })
    events.emit('chat.recalls', { account: 'other', revision: 1 })
    expect(cache.getQueryState(key)?.isInvalidated).toBe(false)
    events.emit('chat.recalls', { account: 'fixture', revision: 1 })
    await expect.poll(() => cache.getQueryState(key)?.isInvalidated).toBe(true)
  })
  it('reopens a terminal SSE connection without discarding cached messages', async () => {
    const { cache, events } = await setup()
    const before = cache.getQueryData(['wechat-flow', 'fixture', 'peer'])
    events.readyState = FixtureEvents.CLOSED
    events.onerror?.()
    await vi.waitFor(() => expect(FixtureEvents.instances).toHaveLength(2), {
      timeout: 3500,
    })
    expect(cache.getQueryData(['wechat-flow', 'fixture', 'peer'])).toBe(before)
  })
  it('keeps messages during a transient bridge verification failure', async () => {
    const { cache, events, peer } = await setup()
    const before = cache.getQueryData(['wechat-flow', 'fixture', 'peer'])
    vi.spyOn(chatApi, 'status').mockResolvedValue({
      ...cache.getQueryData<LiveStatus>(['wechat-live-status-v2'])!,
      bridge_connected: false,
    })
    events.emit('chat.account_changed', {})
    await settle()
    expect(cache.getQueryData(['wechat-flow', 'fixture', 'peer'])).toBe(before)
    expect(peer).not.toHaveBeenCalled()
    expect(events.close).not.toHaveBeenCalled()
  })

  it('clears the previous account only after a verified identity change', async () => {
    const { cache, events, screen } = await setup()
    const remove = vi.spyOn(cache, 'removeQueries')
    vi.spyOn(chatApi, 'status').mockResolvedValue({
      ...cache.getQueryData<LiveStatus>(['wechat-live-status-v2'])!,
      account: 'new-account',
      bridge_connected: true,
    })
    events.emit('chat.account_changed', {})
    await settle()
    expect(chatApi.status).toHaveBeenCalledTimes(1)
    expect(
      cache.getQueryData<LiveStatus>(['wechat-live-status-v2'])?.account
    ).toBe('new-account')
    await expect.element(screen.getByText('account changed')).toBeVisible()
    expect(remove).toHaveBeenCalled()
    expect(
      cache.getQueryData(['wechat-flow', 'fixture', 'peer'])
    ).toBeUndefined()
    expect(events.close).toHaveBeenCalled()
  })
  it('history progress does not refetch any message, session or media query', async () => {
    const { events, cache, peer, other, list, media } = await setup()
    for (const syncing of [true, false, true, false])
      events.emit('chat.history', {
        account: 'fixture',
        history_syncing: syncing,
        history_revision: 'same-files',
        history_count: 20,
      })
    await settle()
    expect(peer).not.toHaveBeenCalled()
    expect(other).not.toHaveBeenCalled()
    expect(list).not.toHaveBeenCalled()
    expect(media).not.toHaveBeenCalled()
    expect(
      cache.getQueryData<LiveStatus>(['wechat-live-status-v2'])?.history_syncing
    ).toBe(false)
  })

  it('coalesces a message burst and refreshes only the affected conversation', async () => {
    const { events, peer, other, list, media } = await setup()
    for (let i = 0; i < 12; i++)
      events.emit(
        'chat.updated',
        { account: 'fixture', sessions: ['peer'], cursor: 10 },
        '10'
      )
    await settle()
    expect(peer).toHaveBeenCalledTimes(1)
    expect(other).not.toHaveBeenCalled()
    expect(list).toHaveBeenCalledTimes(1)
    expect(media).not.toHaveBeenCalled()
  })

  it('does not lose an event that arrives during a pending refresh', async () => {
    const { events, peer, other } = await setup()
    let finish!: (page: SessionDetail) => void
    peer.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    peer.mockResolvedValue(initial('peer', 12))
    events.emit(
      'chat.updated',
      { account: 'fixture', sessions: ['peer'], cursor: 11 },
      '11'
    )
    await vi.waitFor(() => expect(peer).toHaveBeenCalledTimes(1))
    events.emit(
      'chat.updated',
      { account: 'fixture', sessions: ['peer'], cursor: 12 },
      '12'
    )
    finish(initial('peer', 11))
    await settle()
    expect(peer).toHaveBeenCalledTimes(2)
    expect(other).not.toHaveBeenCalled()
  })

  it('ignores events from another account', async () => {
    const { events, peer, list, cache } = await setup()
    events.emit(
      'chat.updated',
      { account: 'foreign', sessions: ['peer'], cursor: 30 },
      '30'
    )
    events.emit('chat.history', { account: 'foreign', history_count: 99999 })
    await settle()
    expect(peer).not.toHaveBeenCalled()
    expect(list).not.toHaveBeenCalled()
    expect(
      cache.getQueryData<LiveStatus>(['wechat-live-status-v2'])?.history_count
    ).not.toBe(99999)
  })
})
