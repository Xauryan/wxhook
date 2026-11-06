import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@/styles/workspace.css'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { chatApi, type Message, type SessionDetail } from './api'
import { Conversation } from './conversation'
import { MessageContent } from './message-content'
import { captureAnchor } from './message-flow'
import { useMessageFlow } from './use-message-flow'

const makeMessage = (id: number, content = `fixture ${id}`): Message => ({
  id: String(id),
  server_id: String(id),
  order_time: id + 1000,
  revision: id,
  content,
  category: 'text',
  sender_name: 'Fixture',
})
function page(
  account: string,
  messages: Message[],
  cursor: string | null = null
): SessionDetail {
  return {
    account,
    session: {
      username: 'peer',
      display_name: 'Fixture',
      is_group: false,
      message_count: messages.length,
      can_send: false,
    },
    messages,
    next_cursor: cursor,
    boundary_cursor: cursor,
    has_more: !!cursor,
    revision: messages.length,
    elapsed_ms: 1,
    history_revision: 'fixture',
  }
}
function Harness() {
  const [account, setAccount] = useState('a')
  const flow = useMessageFlow(account, 'peer')
  return (
    <div>
      <button onClick={() => setAccount('b')}>switch</button>
      <button onClick={() => void flow.loadOlder()}>older</button>
      <button onClick={() => void flow.refetch()}>refresh</button>
      <button onClick={() => void flow.locate('10')}>locate</button>
      <output data-testid='account'>{account}</output>
      <output data-testid='messages'>
        {flow.data?.messages.map((m) => `${m.id}:${m.content}`).join('|')}
      </output>
      <output data-testid='more'>{String(flow.data?.has_more)}</output>
      <output data-testid='flow-error'>{flow.error?.message}</output>
    </div>
  )
}
const client = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } })
afterEach(() => vi.restoreAllMocks())

describe('chat fixture runtime, no real contacts or network', () => {
  it('keeps the located context when an initial latest page finishes later', async () => {
    let initial!: (data: SessionDetail) => void
    let located!: (data: SessionDetail) => void
    let latest!: (data: SessionDetail) => void
    let initialCalls = 0
    vi.spyOn(chatApi, 'detail').mockImplementation(
      async (_account, _peer, _before, _signal, around) =>
        new Promise((resolve) => {
          if (around) located = resolve
          else if (++initialCalls === 1) initial = resolve
          else latest = resolve
        })
    )
    const cache = client()
    const screen = await render(
      <QueryClientProvider client={cache}>
        <Harness />
      </QueryClientProvider>
    )
    await vi.waitFor(() => expect(initial).toBeDefined())
    await userEvent.click(
      screen.getByRole('button', { name: 'locate', exact: true })
    )
    await vi.waitFor(() => expect(located).toBeDefined())
    // A live-event refresh can start a first-page query during the manual
    // around request, after the original first-page query was cancelled.
    void cache.invalidateQueries({ queryKey: ['wechat-flow', 'a', 'peer'] })
    await vi.waitFor(() => expect(latest).toBeDefined())
    located(
      page('a', [
        makeMessage(9),
        makeMessage(10, 'recall target'),
        makeMessage(11),
      ])
    )
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('recall target')
    latest(page('a', [makeMessage(90), makeMessage(91)]))
    await vi.waitFor(() =>
      expect(
        cache.getQueryState(['wechat-flow', 'a', 'peer'])?.fetchStatus
      ).toBe('idle')
    )
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('recall target')
    expect(
      cache.getQueryData<SessionDetail>(['wechat-flow', 'a', 'peer'])?.detached
    ).toBe(true)
    initial(page('a', [makeMessage(100)]))
  })

  it('aborts old history work when the account changes', async () => {
    let pending!: (data: SessionDetail) => void
    let pageSignal: AbortSignal | undefined
    vi.spyOn(chatApi, 'detail').mockImplementation(
      async (account, _peer, before, signal) => {
        if (before) {
          pageSignal = signal
          return new Promise((resolve) => {
            pending = resolve
          })
        }
        return account === 'a'
          ? page('a', [makeMessage(2)], 'older')
          : page('b', [makeMessage(9, 'new account')])
      }
    )
    const screen = await render(
      <QueryClientProvider client={client()}>
        <Harness />
      </QueryClientProvider>
    )
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('2:fixture 2')
    await userEvent.click(
      screen.getByRole('button', { name: 'older', exact: true })
    )
    await vi.waitFor(() => expect(pageSignal).toBeDefined())
    await userEvent.click(
      screen.getByRole('button', { name: 'switch', exact: true })
    )
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('new account')
    expect(pageSignal!.aborted).toBe(true)
    pending(page('a', [makeMessage(1, 'stale history')]))
    await expect
      .element(screen.getByTestId('messages'))
      .not.toHaveTextContent('stale history')
  })

  it('a pending older page cannot resurrect a deleted message', async () => {
    let pending!: (data: SessionDetail) => void
    vi.spyOn(chatApi, 'detail').mockImplementation(
      async (account, _peer, before) =>
        before
          ? new Promise((resolve) => {
              pending = resolve
            })
          : page(account, [makeMessage(3)], 'older')
    )
    vi.spyOn(chatApi, 'changes').mockResolvedValue({
      account: 'a',
      messages: [],
      deleted: ['2'],
      revision: 4,
      has_more: false,
    })
    const screen = await render(
      <QueryClientProvider client={client()}>
        <Harness />
      </QueryClientProvider>
    )
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('3:fixture 3')
    await userEvent.click(
      screen.getByRole('button', { name: 'older', exact: true })
    )
    await vi.waitFor(() => expect(pending).toBeDefined())
    await userEvent.click(
      screen.getByRole('button', { name: 'refresh', exact: true })
    )
    await vi.waitFor(() => expect(chatApi.changes).toHaveBeenCalledTimes(1))
    pending(page('a', [makeMessage(1), makeMessage(2)]))
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('1:fixture 1|3:fixture 3')
  })

  it('stops a stalled change cursor instead of entering an endless request loop', async () => {
    vi.spyOn(chatApi, 'detail').mockResolvedValue(page('a', [makeMessage(1)]))
    vi.spyOn(chatApi, 'changes').mockResolvedValue({
      account: 'a',
      messages: [],
      deleted: [],
      revision: 1,
      has_more: true,
    })
    const screen = await render(
      <QueryClientProvider client={client()}>
        <Harness />
      </QueryClientProvider>
    )
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('fixture 1')
    await userEvent.click(
      screen.getByRole('button', { name: 'refresh', exact: true })
    )
    await expect
      .element(screen.getByTestId('flow-error'))
      .toHaveTextContent('消息增量游标未推进')
    expect(chatApi.changes).toHaveBeenCalledTimes(1)
  })
  it('retains older pages after reconnect and keeps an exhausted cursor exhausted', async () => {
    vi.spyOn(chatApi, 'detail').mockImplementation(
      async (account, _peer, before, _signal, _around, floor) =>
        before
          ? page(account, [makeMessage(1), makeMessage(2)])
          : {
              ...page(account, [makeMessage(3), makeMessage(4)], 'older'),
              boundary_cursor: floor === '1' ? null : 'older',
            }
    )
    vi.spyOn(chatApi, 'changes').mockResolvedValue({
      account: 'a',
      messages: [],
      deleted: [],
      revision: 4,
      has_more: false,
    })
    const screen = await render(
      <QueryClientProvider client={client()}>
        <Harness />
      </QueryClientProvider>
    )
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('3:fixture 3|4:fixture 4')
    await userEvent.click(
      screen.getByRole('button', { name: 'older', exact: true })
    )
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('1:fixture 1|2:fixture 2|3:fixture 3|4:fixture 4')
    await userEvent.click(
      screen.getByRole('button', { name: 'refresh', exact: true })
    )
    await expect.element(screen.getByTestId('more')).toHaveTextContent('false')
    expect(chatApi.detail).toHaveBeenCalledTimes(2)
    expect(chatApi.changes).toHaveBeenLastCalledWith(
      'a',
      'peer',
      2,
      expect.any(AbortSignal),
      '1'
    )
  })

  it('an old account response cannot populate the newly selected account', async () => {
    let finish!: (data: SessionDetail) => void
    vi.spyOn(chatApi, 'detail').mockImplementation((account) =>
      account === 'a'
        ? new Promise((resolve) => {
            finish = resolve
          })
        : Promise.resolve(page('b', [makeMessage(9, 'account b only')]))
    )
    const screen = await render(
      <QueryClientProvider client={client()}>
        <Harness />
      </QueryClientProvider>
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'switch', exact: true })
    )
    await expect
      .element(screen.getByTestId('messages'))
      .toHaveTextContent('account b only')
    finish(page('a', [makeMessage(1, 'old account secret')]))
    await expect
      .element(screen.getByTestId('messages'))
      .not.toHaveTextContent('old account secret')
  })

  it('keeps full text and quoted text as escaped text, not HTML or a summary', async () => {
    const content = 'literal <b>not markup</b> & symbol\n' + 'x'.repeat(2000)
    const screen = await render(
      <MessageContent
        account='fixture'
        message={{
          ...makeMessage(1, content),
          category: 'quote',
          summary: 'SHORT',
          reference: {
            display_name: 'Author',
            content: 'original\n' + content,
          },
        }}
        locate={vi.fn()}
      />
    )
    expect(screen.container.querySelector('.message-text')?.textContent).toBe(
      content
    )
    expect(screen.container.querySelector('b')).toBeNull()
    expect(screen.container.querySelector('blockquote span')?.textContent).toBe(
      'original\n' + content
    )
  })

  it.each(['missing', 'not_downloaded', 'decrypt_failed', 'unsupported'])(
    'shows %s without a fake loaded image',
    async (state) => {
      vi.spyOn(chatApi, 'media').mockResolvedValue({
        preferred: 0,
        elapsed_ms: 1,
        items: [
          {
            index: 0,
            kind: 'image',
            variant: 'thumbnail',
            state,
            detail: `fixture status ${state}`,
          },
        ],
      })
      const screen = await render(
        <QueryClientProvider client={client()}>
          <MessageContent
            account='fixture'
            message={{ ...makeMessage(1), category: 'image' }}
            locate={vi.fn()}
          />
        </QueryClientProvider>
      )
      await expect
        .element(
          screen.getByText(
            state === 'not_downloaded'
              ? '暂未找到本地图片'
              : `fixture status ${state}`,
            { exact: true }
          )
        )
        .toBeVisible()
      expect(screen.container.querySelector('.message-image img')).toBeNull()
    }
  )

  it('does not jump to latest when a new fixture arrives while reading history', async () => {
    let rows = Array.from({ length: 40 }, (_, i) =>
      makeMessage(i + 1, 'fixture line\n'.repeat(6))
    )
    vi.spyOn(chatApi, 'detail').mockImplementation(async () =>
      page('fixture', rows)
    )
    vi.spyOn(chatApi, 'changes').mockImplementation(async () => ({
      account: 'fixture',
      messages: rows.slice(40),
      deleted: [],
      revision: 41,
      has_more: false,
    }))
    const cache = client()
    const screen = await render(
      <QueryClientProvider client={cache}>
        <div style={{ display: 'flex', height: 650, width: 800 }}>
          <Conversation
            session={page('fixture', rows).session}
            live={{
              account: 'fixture',
              profile: { username: 'fixture' },
              bridge_connected: false,
              receive_connected: false,
              newest_sequence: 0,
              relay_pending: 0,
              relay_dropped: 0,
            }}
            onBack={() => {}}
          />
        </div>
      </QueryClientProvider>
    )
    await vi.waitFor(() =>
      expect(
        screen.container.querySelectorAll('[data-message-id]')
      ).toHaveLength(40)
    )
    const scroll =
      screen.container.querySelector<HTMLDivElement>('.message-scroll')!
    scroll.scrollTop = 500
    scroll.dispatchEvent(new Event('scroll'))
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))
    )
    const before = captureAnchor(scroll)!
    rows = [...rows, makeMessage(41, 'new fixture')]
    await cache.invalidateQueries({
      queryKey: ['wechat-flow', 'fixture', 'peer'],
    })
    await vi.waitFor(() =>
      expect(
        screen.container.querySelectorAll('[data-message-id]')
      ).toHaveLength(41)
    )
    const after = captureAnchor(scroll)!
    expect(after.id).toBe(before.id)
    expect(Math.abs(after.offset - before.offset)).toBeLessThan(5)
    await expect
      .element(screen.getByRole('button', { name: /1 条新消息/ }))
      .toBeVisible()
  })

  it('prepends exactly one page and keeps the same visible message anchor', async () => {
    const current = Array.from({ length: 60 }, (_, i) =>
      makeMessage(i + 61, 'fixture line\n'.repeat(3))
    )
    const older = Array.from({ length: 60 }, (_, i) =>
      makeMessage(i + 1, 'older fixture\n'.repeat(3))
    )
    let before: ReturnType<typeof captureAnchor>
    let requests = 0
    vi.spyOn(chatApi, 'detail').mockImplementation(
      async (account, _peer, cursor) => {
        if (cursor) {
          requests++
          before = captureAnchor(element)
          return page(account, older)
        }
        return page(account, current, 'older')
      }
    )
    const screen = await render(
      <QueryClientProvider client={client()}>
        <div style={{ display: 'flex', height: 650, width: 800 }}>
          <Conversation
            session={page('fixture', current).session}
            live={{
              account: 'fixture',
              profile: { username: 'fixture' },
              bridge_connected: false,
              receive_connected: false,
              newest_sequence: 0,
              relay_pending: 0,
              relay_dropped: 0,
            }}
            onBack={() => {}}
          />
        </div>
      </QueryClientProvider>
    )
    await vi.waitFor(() =>
      expect(
        screen.container.querySelectorAll('[data-message-id]')
      ).toHaveLength(60)
    )
    const element =
      screen.container.querySelector<HTMLDivElement>('.message-scroll')!
    await userEvent.click(
      screen.getByRole('button', { name: '加载更早的消息', exact: true })
    )
    await vi.waitFor(() =>
      expect(
        screen.container.querySelectorAll('[data-message-id]')
      ).toHaveLength(120)
    )
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))
    )
    expect(requests).toBe(1)
    const retained = element.querySelector<HTMLElement>(
      `[data-message-id="${before!.id}"]`
    )!
    const offset =
      retained.getBoundingClientRect().top - element.getBoundingClientRect().top
    expect(Math.abs(offset - before!.offset)).toBeLessThan(5)
  })
})
