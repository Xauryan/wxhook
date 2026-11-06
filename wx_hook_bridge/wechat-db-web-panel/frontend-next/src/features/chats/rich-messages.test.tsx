import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@/styles/index.css'
import '@/styles/workspace.css'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page } from 'vitest/browser'
import { chatApi, localAsset, type MediaItem, type Message } from './api'
import { MessageContent } from './message-content'
import './message-media.css'
import { MessageRow } from './message-row'

const message = (
  category: string,
  changes: Partial<Message> = {}
): Message => ({
  id: 'fixture',
  order_time: 1,
  revision: 1,
  category,
  ...changes,
})
const asset = (
  index: number,
  variant: string,
  changes: Partial<MediaItem> = {}
): MediaItem => ({
  index,
  variant,
  kind: 'image',
  state: 'available',
  detail: '可用',
  url: `/api/rich-fixture/${variant}`,
  ...changes,
})

async function show(value: Message) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MessageContent
        account='fixture-account'
        message={value}
        locate={() => {}}
      />
    </QueryClientProvider>
  )
}
function media(items: MediaItem[], preferred = 0) {
  vi.spyOn(chatApi, 'media').mockResolvedValue({
    items,
    preferred,
    elapsed_ms: 0,
  })
}
afterEach(() => vi.restoreAllMocks())

describe('rich chat messages', () => {
  it('uses the original on first click and closes with Escape', async () => {
    media([asset(0, 'thumbnail'), asset(1, 'original', { state: 'local' })])
    const screen = await show(message('image'))
    const fixtureResponse = await fetch(
      localAsset('/api/rich-fixture/thumbnail')!
    )
    expect(fixtureResponse.headers.get('content-type')).toContain('image')
    await screen.getByRole('button', { name: '预览图片' }).click()
    await expect
      .element(page.getByRole('img', { name: '图片原图', exact: true }))
      .toBeVisible()
    await vi.waitFor(() =>
      expect(
        document.querySelector<HTMLImageElement>('.image-preview-stage img')
          ?.src
      ).toContain('/api/rich-fixture/original')
    )
    await expect
      .element(page.getByText('当前显示原图', { exact: true }))
      .toBeVisible()
    await page.getByRole('button', { name: '放大图片' }).click()
    await expect
      .element(page.getByRole('button', { name: '适应窗口' }))
      .toBeVisible()
    await page
      .getByRole('dialog')
      .element()
      .dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })
      )
    await expect.element(page.getByRole('dialog')).not.toBeInTheDocument()
  })

  it('falls back from a broken original without calling it an original', async () => {
    media([
      asset(0, 'thumbnail'),
      asset(1, 'original', {
        url: '/api/rich-fixture/original-broken',
        state: 'local',
      }),
    ])
    const screen = await show(message('image'))
    await screen.getByRole('button', { name: '预览图片' }).click()
    await expect
      .element(page.getByRole('img', { name: '图片缩略图' }))
      .toBeVisible()
    await expect
      .element(page.getByText(/较清晰版本加载失败，已降级/))
      .toBeVisible()
    await expect.element(page.getByText(/当前显示缩略图/)).toBeVisible()
  })

  it('renders classic and modern emoticons while preserving unknown text and quotes', async () => {
    const screen = await show(
      message('quote', {
        content: 'literal <b> [憨笑]\n[捂脸] [unknown]',
        reference: { content: 'quote [微笑]', display_name: 'peer' },
      })
    )
    await expect
      .element(screen.getByRole('img', { name: '[憨笑]', exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('img', { name: '[捂脸]', exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('img', { name: '[微笑]', exact: true }))
      .toBeVisible()
    expect(
      screen.container.querySelector('.message-text')?.textContent
    ).toContain('literal <b>')
    expect(
      screen.container.querySelector('.message-text')?.textContent
    ).toContain('[unknown]')
    expect(screen.container.querySelector('b')).toBeNull()
    const emoji = screen.container.querySelector<HTMLImageElement>('img')!
    await vi.waitFor(() => expect(emoji.naturalWidth).toBeGreaterThan(0))
  })

  it('renders first-cover and following articles without unsafe links or HTML execution', async () => {
    const screen = await show(
      message('public_articles', {
        source_name: 'Fixture Publisher',
        articles: [
          { title: 'First article', url: 'https://example.com/first' },
          {
            title: 'Second <script> title',
            url: 'https://example.com/second',
            description: 'Digest',
          },
          {
            title: 'Unsafe',
            url: 'javascript:alert(1)',
            cover_url: 'file:///secret',
          },
        ],
      })
    )
    await expect
      .element(screen.getByRole('link', { name: '打开文章：First article' }))
      .toBeVisible()
    const first = screen.container.querySelector('.article-lead')!
    expect(first.getAttribute('href')).toBe('https://example.com/first')
    expect(first.getAttribute('rel')).toContain('noopener')
    expect(
      screen.container.querySelectorAll('.article-following')
    ).toHaveLength(2)
    expect(screen.container.querySelectorAll('a')).toHaveLength(2)
    expect(screen.container.querySelector('script')).toBeNull()
    await expect.element(screen.getByText('Fixture Publisher')).toBeVisible()
  })

  it('plays and pauses a real silent voice fixture from its bubble', async () => {
    media([asset(0, 'voice', { kind: 'voice', mime: 'audio/wav' })])
    const screen = await show(message('voice', { duration: 3 }))
    await expect
      .element(screen.getByRole('button', { name: '播放语音' }))
      .toBeVisible()
    screen.container.querySelector('audio')!.loop = true
    await screen.getByRole('button', { name: '播放语音' }).click()
    await expect
      .element(screen.getByRole('button', { name: '暂停语音' }))
      .toBeVisible()
    const audio = screen.container.querySelector('audio')!
    expect(audio.paused).toBe(false)
    await screen.getByRole('button', { name: '暂停语音' }).click()
    expect(audio.paused).toBe(true)
  })

  it('opens a video player on click and removes it on close', async () => {
    media([asset(0, 'video', { kind: 'video', mime: 'video/mp4' })])
    const screen = await show(message('video', { duration: 3 }))
    await screen.getByRole('button', { name: '播放视频' }).click()
    await expect.element(page.getByRole('dialog')).toBeVisible()
    const video = document.querySelector<HTMLVideoElement>('.video-preview')!
    expect(video.controls).toBe(true)
    await vi.waitFor(() => expect(video.readyState).toBeGreaterThanOrEqual(2))
    await page.getByRole('button', { name: '关闭', exact: true }).click()
    expect(document.querySelector('.video-preview')).toBeNull()
  })

  it('opens a file from its entire card and keeps a separate download action', async () => {
    media([
      asset(0, 'file', {
        kind: 'file',
        mime: 'application/octet-stream',
        name: 'fixture.apk',
        size: 100,
      }),
    ])
    const screen = await show(message('file', { file_name: 'fixture.apk' }))
    await screen.getByRole('button', { name: '打开文件：fixture.apk' }).click()
    await expect
      .element(
        page.getByText('此格式不支持浏览器内预览，请下载后使用本机应用打开。')
      )
      .toBeVisible()
    expect(
      page
        .getByRole('dialog')
        .element()
        .querySelector('a[download]')
        ?.getAttribute('href')
    ).toContain('download=true')
  })

  it('reads office content only after opening the file and escapes cell text', async () => {
    media([
      asset(0, 'file', {
        kind: 'file',
        mime: 'application/octet-stream',
        name: 'fixture.xlsx',
      }),
    ])
    const fetcher = vi.spyOn(window, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          kind: 'xlsx',
          detail: 'Fixture content preview',
          truncated: false,
          sections: [
            { title: 'Sheet 1', rows: [['<script>alert(1)</script>', '42']] },
          ],
        }),
        { headers: { 'Content-Type': 'application/json' } }
      )
    )
    const screen = await show(message('file', { file_name: 'fixture.xlsx' }))
    expect(fetcher).not.toHaveBeenCalled()
    await screen.getByRole('button', { name: '打开文件：fixture.xlsx' }).click()
    await expect
      .element(page.getByText('<script>alert(1)</script>', { exact: true }))
      .toBeVisible()
    expect(document.querySelector('.office-preview script')).toBeNull()
    expect(fetcher).toHaveBeenCalledOnce()
  })

  it('does not render external or non-api media as local attachments', () => {
    expect(localAsset('https://example.com/private.png')).toBeUndefined()
    expect(localAsset('javascript:alert(1)')).toBeUndefined()
    expect(localAsset('/images/private.png')).toBeUndefined()
  })

  it('keeps a full article card inside a mobile message row', async () => {
    await page.viewport(390, 844)
    const screen = await render(
      <div style={{ width: '100%', padding: 12 }}>
        <MessageRow
          account='fixture'
          message={message('public_articles', {
            articles: [
              {
                title: 'A long article title for a narrow screen',
                url: 'https://example.com',
              },
              { title: 'Second article' },
            ],
          })}
          separator={false}
          group={false}
          selfName='Self'
          highlighted={false}
          locate={() => {}}
        />
      </div>
    )
    const rect = screen.container
      .querySelector('.public-articles')!
      .getBoundingClientRect()
    expect(rect.right).toBeLessThanOrEqual(390)
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390)
    await page.viewport(1280, 800)
  })
})

describe('message type coverage', () => {
  it('renders media plus its reference and preserves jump interaction', async () => {
    media([asset(0, 'original')])
    const locate = vi.fn()
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const screen = await render(
      <QueryClientProvider client={client}>
        <MessageContent
          account='fixture-account'
          message={message('image', {
            reference: {
              content: 'Original card title',
              server_id: '123',
              display_name: 'Author',
            },
          })}
          locate={locate}
        />
      </QueryClientProvider>
    )
    await expect
      .element(screen.getByRole('button', { name: '预览图片' }))
      .toBeVisible()
    await expect.element(screen.getByText('Original card title')).toBeVisible()
    await screen.getByRole('button', { name: '定位原消息' }).click()
    expect(locate).toHaveBeenCalledWith('123')
  })
  it.each([
    ['mini_program', '小程序'],
    ['game', '小游戏'],
    ['finder_video', '视频号'],
    ['finder_live', '视频号直播'],
    ['shared_video', '视频分享'],
    ['music', '音乐分享'],
  ])('renders %s with a real external link', async (category, label) => {
    const screen = await show(
      message(category, {
        title: 'Shared title',
        url: 'https://example.com/share',
        type_label: label,
        source_name: 'Publisher',
      })
    )
    const link = screen.getByRole('link', { name: '打开分享：Shared title' })
    await expect.element(link).toBeVisible()
    expect(link.element().getAttribute('href')).toBe(
      'https://example.com/share'
    )
    expect(screen.container.textContent).toContain(label)
  })
  it('renders announcement body without executing markup', async () => {
    const screen = await show(
      message('announcement', { content: 'Important <script> announcement' })
    )
    await expect
      .element(screen.getByText('Important <script> announcement'))
      .toBeVisible()
    expect(screen.container.querySelector('script')).toBeNull()
  })
  it('renders call result, payment amount and source status', async () => {
    const call = await show(
      message('call', { call: { text: '对方已取消', duration: 8 } })
    )
    await expect.element(call.getByText('对方已取消')).toBeVisible()
    await expect.element(call.getByText('时长 8 秒')).toBeVisible()
    const payment = await show(
      message('transfer', {
        payment: {
          kind: 'transfer',
          amount: '￥12.34',
          remark: 'Memo',
          status: 'Source status',
        },
      })
    )
    await expect.element(payment.getByText('￥12.34')).toBeVisible()
    await expect.element(payment.getByText('Source status')).toBeVisible()
    expect(payment.container.querySelector('button')).toBeNull()
  })
  it('requests nested attachments with distinct record positions only when expanded', async () => {
    const spy = vi
      .spyOn(chatApi, 'media')
      .mockResolvedValue({
        items: [asset(0, 'original')],
        preferred: 0,
        elapsed_ms: 0,
      })
    const screen = await show(
      message('chat_history', {
        title: 'Merged record',
        records: [
          { sender: 'A', content: '[图片]', type: '2', kind: 'image' },
          {
            sender: 'B',
            content: 'nested',
            type: '14',
            kind: 'chat_history',
            children: [
              { sender: 'C', content: '[图片]', type: '2', kind: 'image' },
            ],
          },
        ],
      })
    )
    await screen.getByText('Merged record', { exact: true }).click()
    await expect
      .element(screen.getByRole('button', { name: '预览图片' }))
      .toBeVisible()
    await vi.waitFor(() =>
      expect(spy.mock.calls.some((c) => c[4] === '0')).toBe(true)
    )
    expect(spy.mock.calls.some((c) => c[4] === '1.0')).toBe(false)
    await screen.getByText('嵌套聊天记录', { exact: true }).click()
    await vi.waitFor(() =>
      expect(spy.mock.calls.some((c) => c[4] === '1.0')).toBe(true)
    )
  })
  it('rejects executable shared-card and reference links', async () => {
    const screen = await show(
      message('mini_program', {
        url: 'javascript:alert(1)',
        title: 'Unsafe',
        reference: { url: 'file:///secret', content: 'quoted' },
      })
    )
    expect(screen.container.querySelector('a')).toBeNull()
    await expect.element(screen.getByText('请在微信中打开')).toBeVisible()
  })
})
