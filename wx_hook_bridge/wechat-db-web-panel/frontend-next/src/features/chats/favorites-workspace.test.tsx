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
import { chatApi, type LiveStatus, type MediaResult } from './api'
import { favoritesApi, type Favorite, type FavoriteList } from './favorites-api'
import { FavoritesWorkspace } from './favorites-workspace'
import { Chats } from './index'
import { useAccountBoundary } from './use-account-boundary'

vi.mock('./use-live-events', () => ({ useLiveEvents: () => true }))
vi.mock('@/features/archive/notification-bridge', () => ({
  NotificationBridge: () => null,
}))

const photo: Favorite = {
  id: '1',
  revision: 'r1',
  category: 'image',
  type_label: '图片',
  title: '',
  content: '',
  description: '',
  summary: '[图片]',
  url: '',
  source_name: 'Fixture source',
  updated_at: 1790000000,
  parse_error: false,
  attachments: [{ group: 0, kind: 'image', name: 'fixture.png', size: 100 }],
  tags: [{ id: 'work', name: 'Work' }],
  thumbnail_url: '/api/rich-fixture/thumbnail',
}
const note: Favorite = {
  ...photo,
  id: '2',
  category: 'text',
  type_label: '文字',
  title: 'Existing saved text',
  content: 'Fixture words [憨笑]',
  attachments: [],
  thumbnail_url: undefined,
  tags: [],
}
const video: Favorite = {
  ...photo,
  id: '3',
  category: 'video',
  type_label: '视频',
  attachments: [{ group: 0, kind: 'video', name: 'fixture.mp4', duration: 3 }],
  tags: [],
}
const other: Favorite = {
  ...note,
  id: '4',
  title: 'Other account favorite',
  content: 'Other account text',
}
const live = (account = 'favorite-a'): LiveStatus => ({
  account,
  profile: { username: account },
  bridge_connected: true,
  receive_connected: true,
  contacts_verified: true,
  send_enabled: true,
  newest_sequence: 0,
  relay_pending: 0,
  relay_dropped: 0,
})
const listing = (
  items = [video, note, photo],
  account = 'favorite-a'
): FavoriteList => ({
  account,
  ready: true,
  syncing: false,
  total: items.length,
  items,
  counts: { all: 3, media: 2 },
  tags: [{ id: 'work', name: 'Work', count: 1 }],
  tags_ready: true,
  limited_count: 0,
  binding_limited: 0,
  recorded_attachment_bytes: 100,
  cloud_remaining_bytes: null,
  write_supported: false,
  write_detail: 'Writing not supported',
})
const imageMedia: MediaResult = {
  preferred: 1,
  elapsed_ms: 0,
  items: [
    {
      index: 0,
      kind: 'image',
      variant: 'original',
      state: 'local',
      detail: '本地资源',
      url: '/api/rich-fixture/original',
    },
    {
      index: 1,
      kind: 'image',
      variant: 'thumbnail',
      state: 'available',
      detail: '可用',
      url: '/api/rich-fixture/thumbnail',
    },
  ],
}

beforeEach(async () => {
  await page.viewport(1280, 800)
  vi.spyOn(favoritesApi, 'list').mockImplementation(
    async (account, q, category, tag) => {
      const all = account === 'favorite-b' ? [other] : [video, note, photo]
      return listing(
        all.filter(
          (item) =>
            (!q ||
              `${item.title} ${item.content} ${item.source_name}`
                .toLowerCase()
                .includes(q.toLowerCase())) &&
            (category !== 'media' ||
              ['image', 'video'].includes(item.category)) &&
            (!tag || item.tags.some((value) => value.id === tag))
        ),
        account
      )
    }
  )
  vi.spyOn(favoritesApi, 'detail').mockImplementation(
    async (_account, id) =>
      [photo, note, video, other].find((item) => item.id === id)!
  )
  vi.spyOn(favoritesApi, 'media').mockImplementation(async (_account, id) =>
    id === '3'
      ? {
          preferred: 0,
          elapsed_ms: 0,
          items: [
            {
              index: 0,
              kind: 'video',
              variant: 'original',
              state: 'available',
              detail: '可用',
              mime: 'video/mp4',
              url: '/api/rich-fixture/video',
            },
          ],
        }
      : imageMedia
  )
  vi.spyOn(favoritesApi, 'refresh').mockResolvedValue(listing())
})
afterEach(() => vi.restoreAllMocks())

function Harness() {
  const [account, setAccount] = useState('favorite-a')
  useAccountBoundary(account, true)
  return (
    <>
      <button onClick={() => setAccount('favorite-b')}>
        Switch favorite fixture account
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
          <FavoritesWorkspace key={account} live={live(account)} />
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

describe('verified favorites workspace', () => {
  it('shows real supplied cards, counts and honest write/capacity boundaries', async () => {
    const screen = await workspace()
    await expect
      .element(screen.getByRole('button', { name: '查看收藏 1：图片' }))
      .toBeVisible()
    expect(screen.container.querySelectorAll('.favorite-card')).toHaveLength(3)
    await expect
      .element(screen.getByRole('button', { name: '新建笔记', exact: true }))
      .toBeDisabled()
    await expect.element(screen.getByText('云端剩余容量未获取')).toBeVisible()
    expect(screen.container.textContent).not.toContain('2.0G')
    expect(favoritesApi.refresh).not.toHaveBeenCalled()
  })

  it('filters all/image-video, text search, and actual tags', async () => {
    const screen = await workspace()
    await screen.getByRole('button', { name: '图片与视频 2' }).click()
    await vi.waitFor(() =>
      expect(screen.container.querySelectorAll('.favorite-card')).toHaveLength(
        2
      )
    )
    await screen.getByRole('textbox', { name: '搜索收藏' }).fill('not-found')
    await expect
      .element(screen.getByRole('heading', { name: '没有匹配的收藏' }))
      .toBeVisible()
    await screen.getByRole('button', { name: '清空收藏搜索' }).click()
    await screen.getByRole('button', { name: '筛选标签：Work' }).click()
    await vi.waitFor(() =>
      expect(screen.container.querySelectorAll('.favorite-card')).toHaveLength(
        1
      )
    )
    expect(favoritesApi.list).toHaveBeenLastCalledWith(
      'favorite-a',
      '',
      'all',
      'work',
      0,
      expect.any(AbortSignal)
    )
    await screen.getByRole('button', { name: '标签', exact: true }).click()
    await expect
      .element(screen.getByRole('button', { name: '筛选标签：Work' }))
      .not.toBeInTheDocument()
  })

  it('opens the original immediately from the card and closes the viewer', async () => {
    const screen = await workspace()
    await screen.getByRole('button', { name: '查看收藏 1：图片' }).click()
    await expect
      .element(page.getByRole('img', { name: '收藏图片原图', exact: true }))
      .toBeVisible()
    await expect
      .element(page.getByText('当前显示原图', { exact: true }))
      .toBeVisible()
    expect(
      document.querySelector<HTMLImageElement>('.image-preview-stage img')!.src
    ).toContain('/api/rich-fixture/original')
    await page.getByRole('button', { name: '放大图片' }).click()
    await expect
      .element(page.getByRole('button', { name: '适应窗口' }))
      .toBeVisible()
    await page.getByRole('button', { name: '关闭', exact: true }).click()
    await expect.element(page.getByRole('dialog')).not.toBeInTheDocument()
  })

  it('falls back to a thumbnail without claiming original quality', async () => {
    vi.mocked(favoritesApi.media).mockResolvedValue({
      ...imageMedia,
      items: [
        { ...imageMedia.items[0], url: '/api/rich-fixture/original-broken' },
        imageMedia.items[1],
      ],
    })
    const screen = await workspace()
    await screen.getByRole('button', { name: '查看收藏 1：图片' }).click()
    await expect
      .element(page.getByRole('img', { name: '收藏图片缩略图' }))
      .toBeVisible()
    await expect
      .element(page.getByText(/较清晰版本加载失败，已降级/))
      .toBeVisible()
  })

  it('opens a playable video fixture and removes its player when closing', async () => {
    const screen = await workspace()
    await screen.getByRole('button', { name: '查看收藏 3：视频' }).click()
    await vi.waitFor(() =>
      expect(
        document.querySelector<HTMLVideoElement>(
          '.favorite-preview-dialog video'
        )?.readyState
      ).toBeGreaterThanOrEqual(2)
    )
    expect(
      document.querySelector<HTMLVideoElement>('.favorite-preview-dialog video')
        ?.controls
    ).toBe(true)
    await page.getByRole('button', { name: '关闭', exact: true }).click()
    expect(document.querySelector('.favorite-preview-dialog video')).toBeNull()
  })

  it('renders saved text and emoticons without executing its markup or unsafe URL', async () => {
    vi.mocked(favoritesApi.detail).mockResolvedValue({
      ...note,
      content: '<script>fixture</script> [憨笑]',
      url: 'javascript:alert(1)',
    })
    const screen = await workspace()
    await screen
      .getByRole('button', { name: '查看收藏 2：Existing saved text' })
      .click()
    await expect
      .element(
        page
          .getByRole('dialog')
          .getByRole('img', { name: '[憨笑]', exact: true })
      )
      .toBeVisible()
    expect(document.querySelector('.favorite-preview-dialog script')).toBeNull()
    expect(
      document.querySelector('.favorite-text-content')?.textContent
    ).toContain('<script>fixture</script>')
    await expect
      .element(page.getByRole('link', { name: '打开收藏链接' }))
      .not.toBeInTheDocument()
  })

  it('reports unavailable resources without pretending the image loaded', async () => {
    vi.mocked(favoritesApi.media).mockResolvedValue({
      preferred: 0,
      elapsed_ms: 0,
      items: [
        {
          index: 0,
          kind: 'image',
          variant: 'original',
          state: 'not_downloaded',
          detail: '微信尚未下载此资源',
        },
      ],
    })
    const screen = await workspace()
    await screen.getByRole('button', { name: '查看收藏 1：图片' }).click()
    await expect
      .element(page.getByText('微信尚未下载此资源', { exact: true }))
      .toBeVisible()
    expect(document.querySelector('.image-preview-stage img')).toBeNull()
  })

  it('distinguishes loading from a truly empty account collection', async () => {
    vi.mocked(favoritesApi.list).mockResolvedValue({
      ...listing([]),
      ready: false,
      tags_ready: false,
    })
    const screen = await workspace()
    await expect
      .element(screen.getByText('正在核验当前账号的收藏…'))
      .toBeVisible()
    await expect
      .element(screen.getByRole('heading', { name: '暂无收藏' }))
      .not.toBeInTheDocument()
  })

  it('has a truthful empty-tags and empty-results state', async () => {
    vi.mocked(favoritesApi.list).mockResolvedValue({
      ...listing([]),
      counts: { all: 0, media: 0 },
      tags: [],
    })
    const screen = await workspace()
    await expect
      .element(screen.getByText('暂无标签', { exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('heading', { name: '暂无收藏' }))
      .toBeVisible()
  })

  it('preserves prior cards with a refresh error and offers explicit retry', async () => {
    vi.mocked(favoritesApi.list).mockResolvedValue({
      ...listing(),
      error: '收藏快照校验失败',
    })
    const screen = await workspace()
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('收藏快照校验失败')
    expect(screen.container.querySelectorAll('.favorite-card')).toHaveLength(3)
    await screen.getByRole('button', { name: '刷新收藏', exact: true }).click()
    await vi.waitFor(() =>
      expect(favoritesApi.refresh).toHaveBeenCalledWith('favorite-a')
    )
  })

  it('loads another page without replacing the existing cards', async () => {
    vi.mocked(favoritesApi.list).mockImplementation(
      async (_account, _q, _category, _tag, offset) => ({
        ...listing(offset ? [note] : [photo]),
        total: 2,
      })
    )
    const screen = await workspace()
    await screen
      .getByRole('button', { name: '加载更多收藏', exact: true })
      .click()
    await vi.waitFor(() =>
      expect(screen.container.querySelectorAll('.favorite-card')).toHaveLength(
        2
      )
    )
    expect(favoritesApi.list).toHaveBeenLastCalledWith(
      'favorite-a',
      '',
      'all',
      '',
      1,
      expect.any(AbortSignal)
    )
  })

  it('does not show a delayed previous-account favorite after switching accounts', async () => {
    let resolve!: (value: Favorite) => void
    vi.mocked(favoritesApi.detail).mockImplementation(async (_account, id) =>
      id === '1'
        ? new Promise<Favorite>((done) => {
            resolve = done
          })
        : other
    )
    const screen = await workspace()
    await screen.getByRole('button', { name: '查看收藏 1：图片' }).click()
    await vi.waitFor(() => expect(resolve).toBeTypeOf('function'))
    await page.getByRole('button', { name: '关闭', exact: true }).click()
    await screen
      .getByRole('button', { name: 'Switch favorite fixture account' })
      .click()
    resolve(photo)
    await expect
      .element(
        screen.getByRole('button', {
          name: '查看收藏 4：Other account favorite',
        })
      )
      .toBeVisible()
    await expect
      .element(screen.getByRole('button', { name: '查看收藏 1：图片' }))
      .not.toBeInTheDocument()
    await expect.element(page.getByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps mobile category navigation and previews within the viewport', async () => {
    await page.viewport(375, 812)
    const screen = await workspace()
    await screen.getByRole('button', { name: '打开收藏分类' }).click()
    await expect
      .element(screen.getByRole('textbox', { name: '搜索收藏' }))
      .toBeVisible()
    await screen
      .getByRole('textbox', { name: '搜索收藏' })
      .fill('Fixture source')
    await screen.getByRole('button', { name: '查看收藏结果' }).click()
    await expect
      .element(screen.getByRole('button', { name: '查看收藏 1：图片' }))
      .toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(375)
    await screen.getByRole('button', { name: '查看收藏 1：图片' }).click()
    await expect
      .element(page.getByRole('img', { name: '收藏图片原图', exact: true }))
      .toBeVisible()
    expect(
      page.getByRole('dialog').element().getBoundingClientRect().right
    ).toBeLessThanOrEqual(375)
  })
})

it('opens the favorites menu inside the chat workspace rather than navigating to the dashboard', async () => {
  vi.spyOn(chatApi, 'status').mockResolvedValue(live())
  vi.spyOn(chatApi, 'list').mockResolvedValue({ items: [], total: 0 })
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
      <ThemeProvider defaultTheme='light' storageKey='favorite-test-theme'>
        <FontProvider>
          <DirectionProvider>
            <RouterProvider router={router} />
          </DirectionProvider>
        </FontProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
  await screen.getByRole('button', { name: '收藏', exact: true }).click()
  await expect
    .element(screen.getByRole('heading', { name: '全部收藏', exact: true }))
    .toBeVisible()
  expect(router.state.location.pathname).toBe('/chats')
  expect(screen.container.querySelector('.favorites-window')).not.toBeNull()
})
