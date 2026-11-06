import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { chatApi, type MediaResult } from './api'
import { MediaContent } from './message-media'

const unavailable: MediaResult = {
  preferred: 0,
  elapsed_ms: 1,
  items: [
    {
      index: 0,
      kind: 'file',
      variant: 'original',
      state: 'not_downloaded',
      detail: '微信尚未下载此资源；本页不会自动联网补拉',
    },
  ],
}
const ready: MediaResult = {
  preferred: 0,
  elapsed_ms: 1,
  items: [
    {
      index: 0,
      kind: 'file',
      variant: 'original',
      state: 'available',
      detail: '可用',
      url: '/api/chat/media/1/0?account=media-fixture',
      mime: 'text/plain',
    },
  ],
}
async function workspace() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MediaContent
        account='media-fixture'
        message={{
          id: '1',
          order_time: 1,
          revision: 1,
          category: 'file',
          file_name: 'fixture.txt',
        }}
      />
    </QueryClientProvider>
  )
}
afterEach(() => {
  vi.restoreAllMocks()
  focusManager.setFocused(undefined)
})

describe('local attachment recovery', () => {
  it('explains unavailable resources and rescans on click with visible progress', async () => {
    let finish!: (value: MediaResult) => void
    const api = vi
      .spyOn(chatApi, 'media')
      .mockResolvedValueOnce(unavailable)
      .mockImplementationOnce(
        async () =>
          new Promise<MediaResult>((resolve) => {
            finish = resolve
          })
      )
    const screen = await workspace()
    await expect
      .element(screen.getByText('暂未找到本地文件', { exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByText('请先在电脑微信打开此文件，再点击重新检查。'))
      .toBeVisible()
    expect(screen.container.textContent).not.toContain('微信尚未下载')
    await userEvent.click(
      screen.getByRole('button', { name: '重新检查本地资源', exact: true })
    )
    await expect
      .element(screen.getByRole('button', { name: '正在重新检查…' }))
      .toBeDisabled()
    expect(api.mock.calls[1][3]).toBe(true)
    finish(ready)
    await expect
      .element(screen.getByRole('button', { name: '打开文件：fixture.txt' }))
      .toBeEnabled()
    await expect
      .element(screen.getByRole('link', { name: '下载原文件' }))
      .toBeVisible()
    expect(screen.container.querySelector('.media-state')).toBeNull()
  })
  it('rechecks unavailable resources when returning from the WeChat client', async () => {
    const api = vi
      .spyOn(chatApi, 'media')
      .mockResolvedValueOnce(unavailable)
      .mockResolvedValue(ready)
    const screen = await workspace()
    await expect
      .element(screen.getByText('暂未找到本地文件', { exact: true }))
      .toBeVisible()
    focusManager.setFocused(false)
    focusManager.setFocused(true)
    await expect
      .element(screen.getByRole('button', { name: '打开文件：fixture.txt' }))
      .toBeEnabled()
    expect(api).toHaveBeenCalledTimes(2)
    expect(api.mock.calls[1][3]).toBe(true)
  })
})
