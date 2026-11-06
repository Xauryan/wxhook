import { useState } from 'react'
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
import { page } from 'vitest/browser'
import { DirectionProvider } from '@/context/direction-provider'
import { FontProvider } from '@/context/font-provider'
import { ThemeProvider } from '@/context/theme-provider'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'
import { chatApi, type LiveStatus } from './api'
import { Chats } from './index'
import { momentsApi, MomentsApiError } from './moments-api'
import { MomentsWorkspace } from './moments-workspace'
import { useAccountBoundary } from './use-account-boundary'

vi.mock('./use-live-events', () => ({ useLiveEvents: () => true }))
vi.mock('@/features/archive/notification-bridge', () => ({
  NotificationBridge: () => null,
}))

type MomentList = Awaited<ReturnType<typeof momentsApi.list>>
type Moment = MomentList['items'][number]

const photo: Moment = {
  id: '9007199254740993',
  username: 'moments-fixture-peer',
  display_name: 'Fixture author',
  avatar_url: '/api/rich-fixture/thumbnail',
  content: 'Fixture autumn gathering [憨笑]',
  create_time: 1790000000,
  is_owner: false,
  location: 'Fixture location',
  content_type: 1,
  media: [
    {
      id: 'fixture-image',
      kind: 'image',
      width: 640,
      height: 960,
      state: 'local',
      detail: '本地资源',
      thumbnail_url: '/api/rich-fixture/thumbnail',
      url: '/api/rich-fixture/original',
    },
  ],
  likes: [{ username: 'fixture-liker', display_name: 'Fixture liker' }],
  comments: [
    {
      id: 'fixture-comment',
      username: 'fixture-commenter',
      display_name: 'Fixture commenter',
      content: 'Fixture comment words',
      create_time: 1790000001,
    },
  ],
  like_count: 3,
  comment_count: 2,
  interactions_limited: true,
}

const note: Moment = {
  ...photo,
  id: '9007199254740994',
  username: 'moments-a',
  display_name: 'Fixture self',
  content: 'Fixture personal note',
  is_owner: true,
  location: '',
  media: [],
  likes: [],
  comments: [],
  like_count: 0,
  comment_count: 0,
}

const other: Moment = {
  ...note,
  id: '9007199254740995',
  username: 'moments-b',
  display_name: 'Other account author',
  content: 'Other account moment',
}

const live = (account = 'moments-a'): LiveStatus => ({
  account,
  profile: {
    username: account,
    display_name: 'Fixture profile',
    avatar_url: '/api/rich-fixture/thumbnail',
  },
  bridge_connected: true,
  receive_connected: true,
  contacts_verified: true,
  send_enabled: true,
  newest_sequence: 0,
  relay_pending: 0,
  relay_dropped: 0,
})

const listing = (
  items: Moment[] = [photo, note],
  account = 'moments-a'
): MomentList => ({
  account,
  ready: true,
  syncing: false,
  total: items.length,
  items,
  next_cursor: null,
  has_more: false,
  source: 'active_sns_db',
  write_supported: false,
  write_detail: 'Publishing and interactions are not connected',
})

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
  vi.spyOn(momentsApi, 'list').mockImplementation(async (account, q, scope) => {
    const all = account === 'moments-b' ? [other] : [photo, note]
    return listing(
      all.filter(
        (item) =>
          (!q || `${item.content} ${item.display_name}`.includes(q)) &&
          (scope !== 'self' || item.is_owner)
      ),
      account
    )
  })
  vi.spyOn(momentsApi, 'refresh').mockResolvedValue(listing())
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function Harness() {
  const [account, setAccount] = useState('moments-a')
  useAccountBoundary(account, true)
  return (
    <>
      <button onClick={() => setAccount('moments-b')}>
        Switch moments fixture account
      </button>
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
    </>
  )
}

async function workspace() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Harness />
    </QueryClientProvider>
  )
}

describe('account-scoped moments workspace, isolated fixtures only', () => {
  it('shows supplied posts and interactions without inventing write capabilities', async () => {
    const screen = await workspace()
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .toBeVisible()
    expect(screen.container.querySelectorAll('article')).toHaveLength(2)
    await expect.element(screen.getByText('Fixture liker')).toBeVisible()
    await expect.element(screen.getByText('Fixture commenter')).toBeVisible()
    await expect
      .element(screen.getByText('Fixture comment words'))
      .toBeVisible()
    await expect.element(screen.getByText('Fixture location')).toBeVisible()
    await expect
      .element(screen.getByRole('img', { name: '[憨笑]', exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByText('本地记录：3 个赞 · 2 条评论，明细可能不完整'))
      .toBeVisible()
    await screen
      .getByRole('button', { name: '发表朋友圈', exact: true })
      .click()
    await expect
      .element(page.getByRole('dialog', { name: '发表朋友圈暂未接入' }))
      .toBeVisible()
    await expect
      .element(
        page.getByText(/当前仅支持浏览本地朋友圈，发布、点赞和评论尚未接入/)
      )
      .toBeVisible()
    await page.getByRole('button', { name: '知道了', exact: true }).click()
    expect(momentsApi.refresh).not.toHaveBeenCalled()
  })

  it('searches and filters the active account using supplied backend results', async () => {
    const screen = await workspace()
    await screen
      .getByRole('button', { name: '我的朋友圈', exact: true })
      .click()
    await vi.waitFor(() =>
      expect(screen.container.querySelectorAll('article')).toHaveLength(1)
    )
    await expect
      .element(screen.getByRole('article', { name: 'Fixture self的朋友圈' }))
      .toBeVisible()
    await screen.getByRole('button', { name: '全部动态', exact: true }).click()
    await screen.getByRole('textbox', { name: '搜索朋友圈' }).fill('gathering')
    await vi.waitFor(() =>
      expect(momentsApi.list).toHaveBeenLastCalledWith(
        'moments-a',
        'gathering',
        'all',
        null,
        expect.any(AbortSignal)
      )
    )
    await expect
      .element(screen.getByRole('article', { name: 'Fixture self的朋友圈' }))
      .not.toBeInTheDocument()
  })

  it('keeps likes and comments disabled and explains the notifications boundary', async () => {
    const screen = await workspace()
    await screen
      .getByRole('article', { name: 'Fixture author的朋友圈' })
      .getByRole('button', { name: '动态操作' })
      .click()
    await expect
      .element(page.getByRole('button', { name: '点赞（暂未接入）' }))
      .toBeDisabled()
    await expect
      .element(page.getByRole('button', { name: '评论（暂未接入）' }))
      .toBeDisabled()
    await screen.getByRole('button', { name: '朋友圈消息' }).click()
    await expect
      .element(page.getByRole('dialog', { name: '朋友圈消息暂未接入' }))
      .toBeVisible()
    await expect
      .element(page.getByText(/当前未接入朋友圈通知及已读状态/))
      .toBeVisible()
  })

  it('distinguishes an unready local cache from an empty verified feed', async () => {
    vi.mocked(momentsApi.list).mockResolvedValue({
      ...listing([]),
      ready: false,
    })
    const screen = await workspace()
    await expect
      .element(screen.getByText('正在准备当前账号的朋友圈缓存…'))
      .toBeVisible()
    await expect
      .element(screen.getByRole('heading', { name: '暂无本地朋友圈动态' }))
      .not.toBeInTheDocument()
  })

  it('shows a truthful empty state after the active account is verified', async () => {
    vi.mocked(momentsApi.list).mockResolvedValue(listing([]))
    const screen = await workspace()
    await expect
      .element(screen.getByRole('heading', { name: '暂无本地朋友圈动态' }))
      .toBeVisible()
    await expect.element(screen.getByText('本地已缓存 0 条动态')).toBeVisible()
    expect(screen.container.querySelectorAll('article')).toHaveLength(0)
  })

  it('renders an out-of-range timestamp as unknown without crashing the feed', async () => {
    vi.mocked(momentsApi.list).mockResolvedValue(
      listing([{ ...note, create_time: 1e20 }])
    )
    const screen = await workspace()
    await expect
      .element(screen.getByRole('article', { name: 'Fixture self的朋友圈' }))
      .toBeVisible()
    await expect
      .element(screen.getByText('时间未知', { exact: true }))
      .toBeVisible()
    expect(
      screen.container.querySelector('time')?.hasAttribute('datetime')
    ).toBe(false)
  })

  it('reports an initial request failure without claiming the feed is empty', async () => {
    vi.mocked(momentsApi.list).mockRejectedValue(
      new Error('Fixture feed unavailable')
    )
    const screen = await workspace()
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('Fixture feed unavailable')
    await expect
      .element(screen.getByRole('heading', { name: '暂无本地朋友圈动态' }))
      .not.toBeInTheDocument()
  })

  it('shows cached posts immediately while retrying a previous source failure', async () => {
    const updatedAt = 1790000000
    vi.mocked(momentsApi.list).mockResolvedValue({
      ...listing(),
      syncing: true,
      error: 'Fixture previous source failure',
      updated_at: updatedAt,
    })
    const screen = await workspace()
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .toBeVisible()
    await expect.element(screen.getByText('本地已缓存 2 条动态')).toBeVisible()
    await expect
      .element(screen.getByText('后台重试中，显示上次缓存'))
      .toBeVisible()
    expect(screen.container.querySelector('.chat-notice')).toBeNull()
    await expect.element(screen.getByRole('alert')).not.toBeInTheDocument()
    expect(
      screen.container
        .querySelector('.moments-source time')
        ?.getAttribute('datetime')
    ).toBe(new Date(updatedAt * 1000).toISOString())
    await screen.getByText('上次同步失败详情', { exact: true }).click()
    await expect
      .element(screen.getByText('Fixture previous source failure'))
      .toBeVisible()
    await expect
      .element(screen.getByRole('button', { name: '刷新朋友圈', exact: true }))
      .toBeDisabled()
  })

  it('keeps a cached source failure visible and immediately retriable', async () => {
    vi.mocked(momentsApi.list).mockResolvedValue({
      ...listing(),
      error: 'Fixture source database unavailable',
      updated_at: 1790000000,
    })
    const screen = await workspace()
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('后台同步失败，显示上次缓存')
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .toBeVisible()
    expect(screen.container.querySelector('.chat-notice')).toBeNull()
    await expect
      .element(screen.getByRole('button', { name: '重试同步', exact: true }))
      .toBeEnabled()
    await expect
      .element(screen.getByRole('button', { name: '刷新朋友圈', exact: true }))
      .toBeEnabled()
    await screen.getByText('上次同步失败详情', { exact: true }).click()
    await expect
      .element(screen.getByText('Fixture source database unavailable'))
      .toBeVisible()
  })

  it('reports a blocked initial source read without claiming usable cached data', async () => {
    vi.mocked(momentsApi.list).mockResolvedValue({
      ...listing([]),
      ready: false,
      error: 'Fixture initial source database unavailable',
    })
    const screen = await workspace()
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('Fixture initial source database unavailable')
    await expect
      .element(
        screen
          .getByRole('alert')
          .getByRole('button', { name: '重试', exact: true })
      )
      .toBeEnabled()
    await expect
      .element(screen.getByRole('heading', { name: '暂无本地朋友圈动态' }))
      .not.toBeInTheDocument()
    expect(screen.container.querySelectorAll('article')).toHaveLength(0)
    expect(screen.container.textContent).not.toContain('显示上次缓存')
    expect(screen.container.querySelector('.moments-source time')).toBeNull()
  })

  it('clears a prior sync failure only after the background retry succeeds', async () => {
    let current: MomentList = {
      ...listing(),
      error: 'Fixture recoverable source failure',
      updated_at: 1790000000,
    }
    vi.mocked(momentsApi.list).mockImplementation(async () => current)
    vi.mocked(momentsApi.refresh).mockImplementation(async () => {
      current = { ...current, syncing: true }
      return current
    })
    const screen = await workspace()
    await screen.getByRole('button', { name: '重试同步', exact: true }).click()
    await expect
      .element(screen.getByText('后台重试中，显示上次缓存'))
      .toBeVisible()
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .toBeVisible()
    const updatedAt = 1790000060
    current = { ...listing(), updated_at: updatedAt }
    await expect
      .element(screen.getByText('上次同步失败详情', { exact: true }))
      .not.toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: '刷新朋友圈', exact: true }))
      .toBeEnabled()
    expect(
      screen.container
        .querySelector('.moments-source time')
        ?.getAttribute('datetime')
    ).toBe(new Date(updatedAt * 1000).toISOString())
    expect(screen.container.textContent).not.toContain(
      'Fixture recoverable source failure'
    )
    expect(screen.container.querySelectorAll('article')).toHaveLength(2)
  })

  it('does not carry a previous account sync failure or timestamp into another account', async () => {
    vi.mocked(momentsApi.list).mockImplementation(async (account) =>
      account === 'moments-a'
        ? {
            ...listing(),
            error: 'Fixture account A source failure',
            updated_at: 1790000000,
          }
        : listing([other], account)
    )
    const screen = await workspace()
    await expect
      .element(screen.getByText('后台同步失败，显示上次缓存'))
      .toBeVisible()
    await screen
      .getByRole('button', { name: 'Switch moments fixture account' })
      .click()
    await expect
      .element(
        screen.getByRole('article', { name: 'Other account author的朋友圈' })
      )
      .toBeVisible()
    expect(screen.container.textContent).not.toContain(
      'Fixture account A source failure'
    )
    expect(screen.container.textContent).not.toContain('显示上次缓存')
    expect(screen.container.querySelector('.moments-source time')).toBeNull()
  })

  it('opens a locally supplied image and closes its viewer', async () => {
    const screen = await workspace()
    await screen.getByRole('button', { name: '查看朋友圈图片 1' }).click()
    await expect
      .element(page.getByRole('dialog', { name: '朋友圈图片', exact: true }))
      .toBeVisible()
    await vi.waitFor(() => {
      const image = document.querySelector<HTMLImageElement>(
        '[role="dialog"] img'
      )
      expect(image?.complete).toBe(true)
      expect(image?.naturalWidth).toBeGreaterThan(0)
    })
    await page.getByRole('button', { name: '关闭', exact: true }).click()
    await expect.element(page.getByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps missing media truthful instead of rendering a broken remote image', async () => {
    vi.mocked(momentsApi.list).mockResolvedValue(
      listing([
        {
          ...photo,
          media: [
            {
              ...photo.media[0],
              state: 'not_downloaded',
              detail: 'Fixture media not cached locally',
              url: undefined,
              thumbnail_url: undefined,
            },
          ],
        },
      ])
    )
    const screen = await workspace()
    await expect
      .element(screen.getByText('Fixture media not cached locally'))
      .toBeVisible()
    expect(screen.container.querySelector('.moment-media-grid img')).toBeNull()
  })

  it('reports a broken local image without claiming it loaded', async () => {
    vi.mocked(momentsApi.list).mockResolvedValue(
      listing([
        {
          ...photo,
          media: [
            { ...photo.media[0], url: '/api/rich-fixture/original-broken' },
          ],
        },
      ])
    )
    const screen = await workspace()
    await screen.getByRole('button', { name: '查看朋友圈图片 1' }).click()
    await expect
      .element(page.getByRole('dialog').getByText('图片读取失败'))
      .toBeVisible()
    expect(document.querySelector('.moment-image-preview img')).toBeNull()
  })

  it('uses a local video fixture with playback controls', async () => {
    vi.mocked(momentsApi.list).mockResolvedValue(
      listing([
        {
          ...note,
          media: [
            {
              ...photo.media[0],
              id: 'fixture-video',
              kind: 'video',
              url: '/api/rich-fixture/video',
            },
          ],
        },
      ])
    )
    const screen = await workspace()
    await expect.element(screen.getByLabelText('朋友圈视频')).toBeVisible()
    const video = screen.container.querySelector<HTMLVideoElement>('video')!
    expect(video.controls).toBe(true)
    expect(video.src).toContain('/api/rich-fixture/video')
    video.muted = true
    await video.play()
    await vi.waitFor(() => expect(video.readyState).toBeGreaterThanOrEqual(2))
    expect(video.paused).toBe(false)
    video.pause()
  })

  it('recovers failed image and video elements when refresh supplies new URLs', async () => {
    const broken: Moment = {
      ...note,
      media: [
        {
          ...photo.media[0],
          url: '/api/rich-fixture/original-broken',
          thumbnail_url: '/api/rich-fixture/original-broken',
        },
        {
          ...photo.media[0],
          id: 'fixture-video',
          kind: 'video',
          url: '/api/rich-fixture/original-broken',
        },
      ],
    }
    vi.mocked(momentsApi.list).mockResolvedValue(listing([broken]))
    const screen = await workspace()
    await expect
      .element(screen.getByText('图片读取失败', { exact: true }))
      .toBeVisible()
    screen.container.querySelector<HTMLVideoElement>('video')!.load()
    await expect
      .element(screen.getByText('本地视频无法播放', { exact: true }))
      .toBeVisible()
    const restored = {
      ...broken,
      media: [
        photo.media[0],
        { ...broken.media[1], url: '/api/rich-fixture/video' },
      ],
    }
    vi.mocked(momentsApi.refresh).mockResolvedValue(listing([restored]))
    vi.mocked(momentsApi.list).mockResolvedValue(listing([restored]))
    await screen
      .getByRole('button', { name: '刷新朋友圈', exact: true })
      .click()
    await vi.waitFor(() => {
      const image = screen.container.querySelector<HTMLImageElement>(
        '.moment-media-grid img'
      )
      expect(image?.complete).toBe(true)
      expect(image?.naturalWidth).toBeGreaterThan(0)
    })
    await expect.element(screen.getByLabelText('朋友圈视频')).toBeVisible()
    const video = screen.container.querySelector<HTMLVideoElement>('video')!
    video.muted = true
    await video.play()
    await vi.waitFor(() => expect(video.readyState).toBeGreaterThanOrEqual(2))
    expect(video.paused).toBe(false)
    video.pause()
    await expect
      .element(screen.getByText('图片读取失败', { exact: true }))
      .not.toBeInTheDocument()
    await expect
      .element(screen.getByText('本地视频无法播放', { exact: true }))
      .not.toBeInTheDocument()
  })

  it('renders post and comment markup as text rather than executable HTML', async () => {
    vi.mocked(momentsApi.list).mockResolvedValue(
      listing([
        {
          ...note,
          content: '<script>fixture-post</script> [憨笑]',
          comments: [
            {
              ...photo.comments[0],
              content: '<img src=x onerror=alert(1)>',
            },
          ],
          comment_count: 1,
        },
      ])
    )
    const screen = await workspace()
    const post = screen.getByRole('article', { name: 'Fixture self的朋友圈' })
    await expect
      .element(post)
      .toHaveTextContent('<script>fixture-post</script>')
    await expect.element(post).toHaveTextContent('<img src=x onerror=alert(1)>')
    expect(post.element().querySelector('script')).toBeNull()
    expect(post.element().querySelector('img[onerror]')).toBeNull()
  })

  it('retains displayed posts and reports a failed refresh', async () => {
    vi.mocked(momentsApi.refresh).mockRejectedValue(
      new Error('Fixture refresh unavailable')
    )
    const screen = await workspace()
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .toBeVisible()
    await screen
      .getByRole('button', { name: '刷新朋友圈', exact: true })
      .click()
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('Fixture refresh unavailable')
    expect(screen.container.querySelectorAll('article')).toHaveLength(2)
    expect(momentsApi.refresh).toHaveBeenCalledWith('moments-a')
  })

  it('refreshes explicitly and adopts new active-account data', async () => {
    const screen = await workspace()
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .toBeVisible()
    const updated = { ...note, content: 'Fixture refreshed post' }
    vi.mocked(momentsApi.refresh).mockResolvedValue(listing([updated]))
    vi.mocked(momentsApi.list).mockResolvedValue(listing([updated]))
    await screen
      .getByRole('button', { name: '刷新朋友圈', exact: true })
      .click()
    await expect
      .element(screen.getByText('Fixture refreshed post'))
      .toBeVisible()
    expect(momentsApi.refresh).toHaveBeenCalledWith('moments-a')
  })

  it('appends the cursor page without replacing existing posts', async () => {
    vi.mocked(momentsApi.list).mockImplementation(
      async (_account, _q, _scope, cursor) => ({
        ...listing(cursor ? [note] : [photo]),
        total: 2,
        next_cursor: cursor ? null : 'fixture-next',
        has_more: !cursor,
      })
    )
    const screen = await workspace()
    await screen
      .getByRole('button', { name: '加载更多动态', exact: true })
      .click()
    await vi.waitFor(() =>
      expect(screen.container.querySelectorAll('article')).toHaveLength(2)
    )
    expect(momentsApi.list).toHaveBeenLastCalledWith(
      'moments-a',
      '',
      'all',
      'fixture-next',
      expect.any(AbortSignal)
    )
  })

  it('preserves the first page when pagination fails and retries that cursor', async () => {
    let failNext = true
    vi.mocked(momentsApi.list).mockImplementation(
      async (_account, _q, _scope, cursor) => {
        if (cursor && failNext) throw new Error('Fixture next page unavailable')
        return {
          ...listing(cursor ? [note] : [photo]),
          total: 2,
          next_cursor: cursor ? null : 'fixture-next',
          has_more: !cursor,
        }
      }
    )
    const screen = await workspace()
    await screen
      .getByRole('button', { name: '加载更多动态', exact: true })
      .click()
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('Fixture next page unavailable')
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .toBeVisible()
    failNext = false
    await screen
      .getByRole('button', { name: '加载更多动态', exact: true })
      .click()
    await vi.waitFor(() =>
      expect(screen.container.querySelectorAll('article')).toHaveLength(2)
    )
  })

  it('restarts from the first page when a stale pagination cursor returns 409', async () => {
    vi.mocked(momentsApi.list).mockImplementation(
      async (_account, _q, _scope, cursor) => {
        if (cursor) throw new MomentsApiError('Fixture cursor expired', 409)
        return {
          ...listing([photo]),
          total: 2,
          next_cursor: 'fixture-expired',
          has_more: true,
        }
      }
    )
    const screen = await workspace()
    await screen
      .getByRole('button', { name: '加载更多动态', exact: true })
      .click()
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('Fixture cursor expired')
    const callsBeforeRetry = vi.mocked(momentsApi.list).mock.calls.length
    await screen
      .getByRole('alert')
      .getByRole('button', { name: '重试', exact: true })
      .click()
    await vi.waitFor(() =>
      expect(momentsApi.list).toHaveBeenCalledTimes(callsBeforeRetry + 1)
    )
    expect(momentsApi.list).toHaveBeenLastCalledWith(
      'moments-a',
      '',
      'all',
      null,
      expect.any(AbortSignal)
    )
    await expect.element(screen.getByRole('alert')).not.toBeInTheDocument()
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .toBeVisible()
  })

  it('does not show delayed previous-account posts after switching accounts', async () => {
    let resolve!: (value: MomentList) => void
    vi.mocked(momentsApi.list).mockImplementation(async (account) =>
      account === 'moments-a'
        ? new Promise<MomentList>((done) => {
            resolve = done
          })
        : listing([other], account)
    )
    const screen = await workspace()
    await vi.waitFor(() => expect(resolve).toBeTypeOf('function'))
    await screen
      .getByRole('button', { name: 'Switch moments fixture account' })
      .click()
    resolve(listing())
    await expect
      .element(
        screen.getByRole('article', { name: 'Other account author的朋友圈' })
      )
      .toBeVisible()
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .not.toBeInTheDocument()
    expect(screen.container.textContent).not.toContain(
      'Fixture autumn gathering'
    )
  })

  it('keeps long content and image previews inside a 375 pixel viewport', async () => {
    await page.viewport(375, 812)
    vi.mocked(momentsApi.list).mockResolvedValue(
      listing([{ ...photo, content: `Fixture long text ${'W'.repeat(300)}` }])
    )
    const screen = await workspace()
    await expect
      .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
      .toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(375)
    expect(
      screen
        .getByRole('article', { name: 'Fixture author的朋友圈' })
        .element()
        .getBoundingClientRect().right
    ).toBeLessThanOrEqual(375)
    await screen.getByRole('button', { name: '查看朋友圈图片 1' }).click()
    await expect.element(page.getByRole('dialog')).toBeVisible()
    expect(
      page.getByRole('dialog').element().getBoundingClientRect().right
    ).toBeLessThanOrEqual(375)
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(375)
  })
})

it('opens moments inside the chat workspace and preserves the existing admin shell', async () => {
  vi.spyOn(chatApi, 'status').mockResolvedValue(live())
  vi.spyOn(chatApi, 'list').mockResolvedValue({ items: [], total: 0 })
  vi.spyOn(chatApi, 'send').mockRejectedValue(
    new Error('Fixture must not send')
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
  const screen = await render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ThemeProvider defaultTheme='light' storageKey='moments-test-theme'>
        <FontProvider>
          <DirectionProvider>
            <RouterProvider router={router} />
          </DirectionProvider>
        </FontProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
  await screen.getByRole('button', { name: '朋友圈', exact: true }).click()
  await expect
    .element(screen.getByRole('heading', { name: '朋友圈', exact: true }))
    .toBeVisible()
  await expect
    .element(screen.getByRole('article', { name: 'Fixture author的朋友圈' }))
    .toBeVisible()
  await expect
    .element(screen.getByRole('link', { name: '微信聊天', exact: true }))
    .toBeVisible()
  await expect
    .element(screen.getByRole('button', { name: '切换主题' }))
    .toBeVisible()
  expect(
    screen.container.querySelector('[data-sidebar="sidebar"]')
  ).not.toBeNull()
  expect(router.state.location.pathname).toBe('/chats')
  expect(chatApi.send).not.toHaveBeenCalled()
})
