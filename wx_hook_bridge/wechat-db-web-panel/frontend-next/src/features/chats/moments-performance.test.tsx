import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@/styles/index.css'
import '@/styles/workspace.css'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page } from 'vitest/browser'
import { type LiveStatus } from './api'
import { momentsApi, type Moment, type MomentList } from './moments-api'
import { MomentsWorkspace } from './moments-workspace'
import { useAccountBoundary } from './use-account-boundary'

vi.mock('./use-live-events', () => ({ useLiveEvents: () => true }))

const accountA = 'moments-performance-a'
const accountB = 'moments-performance-b'
const clients: QueryClient[] = []

function post(id: string, content: string): Moment {
  return {
    id,
    username: `fixture-${id}`,
    display_name: `Author ${id}`,
    content,
    create_time: 1790000000,
    is_owner: false,
    location: '',
    content_type: 1,
    media: [],
    likes: [],
    comments: [],
    like_count: 0,
    comment_count: 0,
    interactions_limited: false,
  }
}

const first = post('head-v1', 'Cached first page')
const older = post('older-v1', 'Cached older page')
const updated = { ...first, content: 'Updated first page' }
const nextFirst = post('head-v2', 'New revision first page')
const nextOlder = post('older-v2', 'New revision older page')
const otherAccount = post('account-b', 'Active second account post')

function listing(
  items: Moment[],
  overrides: Partial<MomentList> = {}
): MomentList {
  return {
    account: accountA,
    ready: true,
    syncing: false,
    revision: 'revision-1',
    total: 2,
    items,
    next_cursor: null,
    has_more: false,
    source: 'isolated_fixture',
    write_supported: false,
    write_detail: 'Isolated read-only performance fixture',
    ...overrides,
  }
}

function head(items = [first], overrides: Partial<MomentList> = {}) {
  return listing(items, {
    next_cursor: 'cursor-v1',
    has_more: true,
    ...overrides,
  })
}

function live(account: string): LiveStatus {
  return {
    account,
    profile: { username: account, display_name: 'Fixture profile' },
    bridge_connected: true,
    receive_connected: true,
    contacts_verified: true,
    send_enabled: false,
    newest_sequence: 0,
    relay_pending: 0,
    relay_dropped: 0,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function Harness() {
  const [account, setAccount] = useState(accountA)
  const [visible, setVisible] = useState(true)
  useAccountBoundary(account, true)
  return (
    <>
      <button onClick={() => setVisible((value) => !value)}>
        Toggle fixture feed
      </button>
      <button onClick={() => setAccount(accountB)}>
        Switch fixture account
      </button>
      {visible && (
        <div
          className='chat-workspace'
          style={{
            height: 740,
            containerType: 'inline-size',
            containerName: 'content',
          }}
        >
          <div className='chat-page' style={{ height: '100%' }}>
            <MomentsWorkspace key={account} live={live(account)} />
          </div>
        </div>
      )}
    </>
  )
}

async function workspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  const screen = await render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>
  )
  return { screen, client }
}

beforeEach(async () => {
  await page.viewport(1280, 800)
  vi.stubGlobal(
    'EventSource',
    class extends EventTarget {
      static CLOSED = 2
      static OPEN = 1
      static CONNECTING = 0
      readyState = 1
      close() {}
    }
  )
  vi.spyOn(momentsApi, 'list').mockImplementation(
    async (_account, _q, _scope, cursor) => (cursor ? listing([older]) : head())
  )
  vi.spyOn(momentsApi, 'refresh').mockResolvedValue(
    listing([], { syncing: true })
  )
})

afterEach(() => {
  clients.splice(0).forEach((client) => client.clear())
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('moments cache-first pagination performance, isolated fixtures only', () => {
  it('attaches local thumbnail sources only after their viewport intersection', async () => {
    const observers: ControlledObserver[] = []
    class ControlledObserver implements IntersectionObserver {
      readonly root = null
      readonly rootMargin: string
      readonly scrollMargin = '0px'
      readonly thresholds = [0]
      target: Element | undefined
      disconnect = vi.fn()
      unobserve = vi.fn()
      takeRecords = () => []

      constructor(
        private callback: IntersectionObserverCallback,
        options?: IntersectionObserverInit
      ) {
        this.rootMargin = options?.rootMargin ?? '0px'
        observers.push(this)
      }

      observe(target: Element) {
        this.target = target
      }

      intersect(isIntersecting: boolean) {
        const target = this.target!
        const bounds = target.getBoundingClientRect()
        this.callback(
          [
            {
              target,
              isIntersecting,
              intersectionRatio: isIntersecting ? 1 : 0,
              boundingClientRect: bounds,
              intersectionRect: isIntersecting ? bounds : new DOMRect(),
              rootBounds: null,
              time: performance.now(),
            },
          ],
          this
        )
      }
    }
    vi.stubGlobal('IntersectionObserver', ControlledObserver)
    const thumbnails = ['visible', 'distant'].map((id) => ({
      id,
      kind: 'image',
      width: 200,
      height: 200,
      state: 'local',
      detail: 'Isolated local thumbnail',
      thumbnail_url: `/api/rich-fixture/thumbnail?slot=${id}`,
      url: '/api/rich-fixture/original',
    }))
    vi.mocked(momentsApi.list).mockResolvedValue(
      listing([{ ...first, media: thumbnails }], { total: 1 })
    )
    const { screen } = await workspace()
    await vi.waitFor(() => expect(observers).toHaveLength(2))
    const [visible, distant] =
      screen.container.querySelectorAll<HTMLImageElement>(
        '.moment-media-grid img'
      )
    expect(observers.map((observer) => observer.target)).toEqual([
      visible,
      distant,
    ])
    expect(observers.map((observer) => observer.rootMargin)).toEqual([
      '100px',
      '100px',
    ])
    expect(visible.hasAttribute('src')).toBe(false)
    expect(distant.hasAttribute('src')).toBe(false)

    observers[1].intersect(false)
    observers[0].intersect(true)
    await vi.waitFor(() =>
      expect(visible.src).toBe(
        new URL(thumbnails[0].thumbnail_url, window.location.origin).href
      )
    )
    expect(observers[0].disconnect).toHaveBeenCalled()
    expect(distant.hasAttribute('src')).toBe(false)
    expect(distant.style.visibility).toBe('hidden')
    expect(observers[1].disconnect).not.toHaveBeenCalled()

    observers[1].intersect(true)
    await vi.waitFor(() =>
      expect(distant.src).toBe(
        new URL(thumbnails[1].thumbnail_url, window.location.origin).href
      )
    )
    expect(distant.style.visibility).toBe('')
    expect(observers[1].disconnect).toHaveBeenCalled()
    expect(momentsApi.list).toHaveBeenCalledTimes(1)
  })

  it('remounts both fresh cached pages without waiting for another request', async () => {
    const { screen } = await workspace()
    await expect.element(screen.getByText(first.content)).toBeVisible()
    await screen
      .getByRole('button', { name: '加载更多动态', exact: true })
      .click()
    await expect.element(screen.getByText(older.content)).toBeVisible()
    expect(momentsApi.list).toHaveBeenCalledTimes(2)

    await screen.getByRole('button', { name: 'Toggle fixture feed' }).click()
    await expect
      .element(screen.getByText(first.content))
      .not.toBeInTheDocument()
    // A remount must not depend on this deliberately unresolved request.
    vi.mocked(momentsApi.list).mockImplementation(
      () => deferred<MomentList>().promise
    )
    await screen.getByRole('button', { name: 'Toggle fixture feed' }).click()

    expect(screen.container.textContent).toContain(first.content)
    expect(screen.container.textContent).toContain(older.content)
    expect(screen.container.textContent).not.toContain('正在读取朋友圈…')
    expect(momentsApi.list).toHaveBeenCalledTimes(2)
  })

  it('refreshes and polls only the head while preserving same-revision older pages', async () => {
    let refreshing = false
    let syncReads = 0
    vi.mocked(momentsApi.list).mockImplementation(
      async (_account, _q, _scope, cursor) => {
        if (cursor) return listing([older])
        if (!refreshing) return head()
        syncReads += 1
        return head([updated], { syncing: syncReads < 3 })
      }
    )
    vi.mocked(momentsApi.refresh).mockImplementation(async () => {
      refreshing = true
      return listing([], { syncing: true })
    })
    const { screen } = await workspace()
    await screen
      .getByRole('button', { name: '加载更多动态', exact: true })
      .click()
    await expect.element(screen.getByText(older.content)).toBeVisible()
    const callsBeforeRefresh = vi.mocked(momentsApi.list).mock.calls.length

    await screen
      .getByRole('button', { name: '刷新朋友圈', exact: true })
      .click()
    await expect.element(screen.getByText(updated.content)).toBeVisible()
    await expect.element(screen.getByText(older.content)).toBeVisible()
    await expect
      .element(screen.getByText('后台更新中，不影响浏览缓存'))
      .toBeVisible()
    await vi.waitFor(() => expect(syncReads).toBe(3), { timeout: 3000 })
    await expect.element(screen.getByText('本地已缓存 2 条动态')).toBeVisible()
    await expect.element(screen.getByText(older.content)).toBeVisible()

    const refreshCalls = vi
      .mocked(momentsApi.list)
      .mock.calls.slice(callsBeforeRefresh)
    expect(refreshCalls).toHaveLength(3)
    expect(refreshCalls.every(([, , , cursor]) => cursor === null)).toBe(true)
    expect(
      vi
        .mocked(momentsApi.list)
        .mock.calls.filter(([, , , cursor]) => cursor === 'cursor-v1')
    ).toHaveLength(1)
    expect(screen.container.querySelectorAll('article')).toHaveLength(2)
  })

  it('drops stale older pages on revision changes and uses only the new cursor', async () => {
    let revision = 'revision-1'
    vi.mocked(momentsApi.list).mockImplementation(
      async (_account, _q, _scope, cursor) => {
        if (cursor === 'cursor-v1') return listing([older])
        if (cursor === 'cursor-v2') return listing([nextOlder], { revision })
        return revision === 'revision-1'
          ? head()
          : head([nextFirst], { revision, next_cursor: 'cursor-v2' })
      }
    )
    vi.mocked(momentsApi.refresh).mockImplementation(async () => {
      revision = 'revision-2'
      return listing([], { syncing: true, revision })
    })
    const { screen } = await workspace()
    await screen
      .getByRole('button', { name: '加载更多动态', exact: true })
      .click()
    await expect.element(screen.getByText(older.content)).toBeVisible()

    await screen
      .getByRole('button', { name: '刷新朋友圈', exact: true })
      .click()
    await expect.element(screen.getByText(nextFirst.content)).toBeVisible()
    await expect
      .element(screen.getByText(first.content))
      .not.toBeInTheDocument()
    await expect
      .element(screen.getByText(older.content))
      .not.toBeInTheDocument()
    expect(screen.container.querySelectorAll('article')).toHaveLength(1)
    expect(momentsApi.list).toHaveBeenCalledTimes(3)

    await screen
      .getByRole('button', { name: '加载更多动态', exact: true })
      .click()
    await expect.element(screen.getByText(nextOlder.content)).toBeVisible()
    expect(momentsApi.list).toHaveBeenLastCalledWith(
      accountA,
      '',
      'all',
      'cursor-v2',
      expect.any(AbortSignal)
    )
    expect(
      vi
        .mocked(momentsApi.list)
        .mock.calls.filter(([, , , cursor]) => cursor === 'cursor-v1')
    ).toHaveLength(1)
    expect(screen.container.querySelectorAll('article')).toHaveLength(2)
  })

  it('cancels a pending old-account page without leaking its late response', async () => {
    const pending = deferred<MomentList>()
    let pendingSignal: AbortSignal | undefined
    vi.mocked(momentsApi.list).mockImplementation(
      async (account, _q, _scope, cursor, signal) => {
        if (account === accountB)
          return listing([otherAccount], {
            account,
            revision: 'account-b',
            total: 1,
          })
        if (cursor) {
          pendingSignal = signal
          return pending.promise
        }
        return head()
      }
    )
    const { screen, client } = await workspace()
    await screen
      .getByRole('button', { name: '加载更多动态', exact: true })
      .click()
    await vi.waitFor(() => expect(pendingSignal).toBeDefined())
    await screen.getByRole('button', { name: 'Switch fixture account' }).click()
    await expect.element(screen.getByText(otherAccount.content)).toBeVisible()
    expect(pendingSignal?.aborted).toBe(true)

    pending.resolve(listing([older]))
    await vi.waitFor(() =>
      expect(
        client
          .getQueryCache()
          .findAll({ queryKey: ['wechat-moments', accountA] })
      ).toHaveLength(0)
    )
    await expect
      .element(screen.getByText(first.content))
      .not.toBeInTheDocument()
    await expect
      .element(screen.getByText(older.content))
      .not.toBeInTheDocument()
    expect(screen.container.querySelectorAll('article')).toHaveLength(1)
    expect(momentsApi.list).toHaveBeenCalledTimes(3)
  })

  it('ignores a pending previous-account refresh acknowledgement after switching', async () => {
    const pending = deferred<MomentList>()
    vi.mocked(momentsApi.list).mockImplementation(async (account) =>
      account === accountB
        ? listing([otherAccount], { account, revision: 'account-b', total: 1 })
        : listing([first], { total: 1 })
    )
    vi.mocked(momentsApi.refresh).mockReturnValue(pending.promise)
    const { screen, client } = await workspace()
    await expect.element(screen.getByText(first.content)).toBeVisible()
    await screen
      .getByRole('button', { name: '刷新朋友圈', exact: true })
      .click()
    expect(momentsApi.refresh).toHaveBeenCalledWith(accountA)
    await screen.getByRole('button', { name: 'Switch fixture account' }).click()
    await expect.element(screen.getByText(otherAccount.content)).toBeVisible()

    pending.resolve(listing([updated], { syncing: true }))
    await vi.waitFor(() => expect(client.isMutating()).toBe(0))
    expect(
      client.getQueryCache().findAll({ queryKey: ['wechat-moments', accountA] })
    ).toHaveLength(0)
    expect(momentsApi.list).toHaveBeenCalledTimes(2)
    await expect
      .element(screen.getByText(updated.content))
      .not.toBeInTheDocument()
    await expect
      .element(screen.getByText(first.content))
      .not.toBeInTheDocument()
    await expect.element(screen.getByText(otherAccount.content)).toBeVisible()
    expect(screen.container.querySelectorAll('article')).toHaveLength(1)
  })
})
