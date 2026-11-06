import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@/styles/workspace.css'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import {
  chatApi,
  type Message,
  type SendReceipt,
  type SessionDetail,
} from './api'
import { Conversation } from './conversation'
import { reconcilePendingSends, type PendingSend } from './pending-sends'

let account = 'send-preview-fixture'
const username = 'preview-peer'
const text = 'local fixture <b>literal</b>\n full text '
const now = () => Math.floor(Date.now() / 1000)
const message = (id: number, content = text): Message => ({
  id: String(id),
  server_id: String(900000 + id),
  content,
  category: 'text',
  is_self: true,
  order_time: now(),
  revision: 10 + id,
  source: 'history',
})
const pending = (request: string): PendingSend => ({
  request,
  text,
  createdAt: Date.now(),
  status: 'accepted',
  baselineRevision: 10,
  knownIds: [],
})
function detail(identity: string, rows: Message[]): SessionDetail {
  return {
    account: identity,
    session: {
      username,
      display_name: 'Local fixture',
      is_group: false,
      can_send: true,
      message_count: rows.length,
    },
    messages: rows,
    revision: Math.max(10, ...rows.map((row) => row.revision)),
    history_revision: 'fixture',
    elapsed_ms: 1,
    next_cursor: null,
    boundary_cursor: null,
    has_more: false,
  }
}
function ConversationFixture({ identity = account }: { identity?: string }) {
  return (
    <div style={{ display: 'flex', height: 650, width: 800 }}>
      <Conversation
        session={detail(identity, []).session}
        live={{
          account: identity,
          profile: { username: identity },
          bridge_connected: true,
          send_enabled: true,
          receive_connected: true,
          newest_sequence: 10,
          relay_pending: 0,
          relay_dropped: 0,
        }}
        onBack={() => {}}
      />
    </div>
  )
}
function fixture() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  let rows: Message[] = []
  let resolve!: (receipt: SendReceipt) => void
  let reject!: (error: Error) => void
  let request = ''
  vi.spyOn(chatApi, 'detail').mockImplementation(async (identity) =>
    detail(identity, rows)
  )
  vi.spyOn(chatApi, 'changes').mockImplementation(async (identity) => ({
    account: identity,
    messages: rows,
    deleted: [],
    revision: Math.max(10, ...rows.map((row) => row.revision)),
    has_more: false,
  }))
  const send = vi
    .spyOn(chatApi, 'send')
    .mockImplementation(async (_account, _username, _text, id) => {
      request = id
      return await new Promise<SendReceipt>((ok, no) => {
        resolve = ok
        reject = no
      })
    })
  return {
    cache,
    send,
    request: () => request,
    accept: () =>
      resolve({
        request_id: request,
        status: 'accepted',
        detail: 'fixture accepted only',
      }),
    fail: () =>
      resolve({
        request_id: request,
        status: 'failed',
        detail: 'fixture rejected',
      }),
    unknown: () => reject(new Error('fixture network timeout')),
    async echo(...messages: Message[]) {
      rows = messages
      await cache.invalidateQueries({
        queryKey: ['wechat-flow', account, username],
      })
    },
  }
}

beforeEach(() => {
  sessionStorage.clear()
  account = 'send-preview-fixture-' + crypto.randomUUID()
})
afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('pending preview reconciliation', () => {
  it('matches repeated text one-to-one across partial readbacks without changing real messages', () => {
    const rows = [message(1), message(2)]
    const original = rows.slice()
    let records = [pending('one'), pending('two'), pending('three')]
    records = reconcilePendingSends(records, rows.slice(0, 1))
    expect(records.map((row) => row.messageId)).toEqual([
      '1',
      undefined,
      undefined,
    ])
    expect(reconcilePendingSends(records, rows.slice(0, 1))).toBe(records)
    records = reconcilePendingSends(records, rows)
    expect(records.map((row) => row.messageId)).toEqual(['1', '2', undefined])
    expect(rows).toEqual(original)
  })
  it('does not confirm from an old message, incoming text, a pending native ID, or a failed request', () => {
    const row = { ...pending('one'), knownIds: ['1'] }
    const rows = [
      message(1),
      { ...message(2), is_self: false },
      { ...message(3), server_id: '0' },
      { ...message(4), order_time: now() - 30 },
      { ...message(5), revision: 10 },
    ]
    expect(reconcilePendingSends([row], rows)[0].messageId).toBeUndefined()
    expect(
      reconcilePendingSends(
        [{ ...pending('failed'), status: 'failed' }],
        [message(6)]
      )[0].messageId
    ).toBeUndefined()
  })
})

describe('web send preview', () => {
  it('renders before HTTP completes and replaces the preview only after a real echo', async () => {
    const f = fixture()
    const screen = await render(
      <QueryClientProvider client={f.cache}>
        <ConversationFixture />
      </QueryClientProvider>
    )
    await expect
      .element(screen.getByRole('textbox', { name: '聊天消息' }))
      .toBeEnabled()
    await userEvent.fill(
      screen.getByRole('textbox', { name: '聊天消息' }),
      text
    )
    await userEvent.click(
      screen.getByRole('button', { name: '发送', exact: true })
    )
    await expect
      .element(screen.getByText('正在发送…', { exact: true }))
      .toBeVisible()
    expect(
      screen.container.querySelector('.pending-send .message-text')?.textContent
    ).toBe(text)
    expect(screen.container.querySelector('.pending-send b')).toBeNull()
    expect(screen.container.querySelectorAll('[data-message-id]')).toHaveLength(
      0
    )
    expect(f.send).toHaveBeenCalledTimes(1)
    f.accept()
    await expect
      .element(screen.getByText('已提交，等待微信回显', { exact: true }))
      .toBeVisible()
    expect(screen.container.querySelectorAll('.pending-send')).toHaveLength(1)
    await expect
      .element(screen.getByText('fixture accepted only'))
      .toBeVisible()
    await f.echo(message(1))
    await vi.waitFor(() =>
      expect(screen.container.querySelectorAll('.pending-send')).toHaveLength(0)
    )
    expect(
      screen.container.querySelectorAll('[data-message-id="1"]')
    ).toHaveLength(1)
    expect(screen.container.querySelectorAll('.message-text')).toHaveLength(1)
    expect(
      screen.container.querySelector('.chat-composer [role="status"]')
    ).toBeNull()
    expect(screen.container.querySelector('.chat-composer')).not.toHaveClass(
      'has-notice'
    )
    expect(f.send).toHaveBeenCalledTimes(1)
  })
  it('keeps a source echo when it arrives before the HTTP receipt', async () => {
    const f = fixture()
    const screen = await render(
      <QueryClientProvider client={f.cache}>
        <ConversationFixture />
      </QueryClientProvider>
    )
    await userEvent.fill(
      screen.getByRole('textbox', { name: '聊天消息' }),
      text
    )
    await userEvent.click(
      screen.getByRole('button', { name: '发送', exact: true })
    )
    await f.echo(message(1))
    await vi.waitFor(() =>
      expect(screen.container.querySelectorAll('.pending-send')).toHaveLength(0)
    )
    f.accept()
    await expect
      .element(screen.getByRole('textbox', { name: '聊天消息' }))
      .toHaveValue('')
    expect(screen.container.querySelectorAll('.pending-send')).toHaveLength(0)
    expect(
      screen.container.querySelectorAll('[data-message-id="1"]')
    ).toHaveLength(1)
    expect(
      screen.container.querySelector('.chat-composer [role="status"]')
    ).toBeNull()
    expect(screen.container.querySelector('.chat-composer')).not.toHaveClass(
      'has-notice'
    )
  })
  it.each(['failed', 'unknown'] as const)(
    'keeps %s visible without resending',
    async (state) => {
      const f = fixture()
      const screen = await render(
        <QueryClientProvider client={f.cache}>
          <ConversationFixture />
        </QueryClientProvider>
      )
      await userEvent.fill(
        screen.getByRole('textbox', { name: '聊天消息' }),
        text
      )
      await userEvent.click(
        screen.getByRole('button', { name: '发送', exact: true })
      )
      if (state === 'failed') f.fail()
      else f.unknown()
      await expect
        .element(
          screen.getByText(
            state === 'failed' ? '发送失败' : '结果未确认，请在微信核对',
            { exact: true }
          )
        )
        .toBeVisible()
      expect(
        screen.container.querySelector('.pending-send .message-text')
          ?.textContent
      ).toBe(text)
      expect(f.send).toHaveBeenCalledTimes(1)
      expect(
        screen.container.querySelectorAll('[data-message-id]')
      ).toHaveLength(0)
    }
  )
  it.each(['dispatching', 'accepted', 'failed', 'unknown'] as const)(
    'an older echo cannot clear the newer %s notice',
    async (state) => {
      const f = fixture()
      const screen = await render(
        <QueryClientProvider client={f.cache}>
          <ConversationFixture />
        </QueryClientProvider>
      )
      const input = screen.getByRole('textbox', { name: '聊天消息' })
      const send = screen.getByRole('button', { name: '发送', exact: true })
      await userEvent.fill(input, text)
      await userEvent.click(send)
      f.accept()
      await expect.element(input).toHaveValue('')
      const nextText = 'newer request fixture'
      await userEvent.fill(input, nextText)
      await userEvent.click(send)
      if (state === 'accepted') f.accept()
      else if (state === 'failed') f.fail()
      else if (state === 'unknown') f.unknown()
      const expected = {
        dispatching: '正在提交给微信…',
        accepted: 'fixture accepted only',
        failed: 'fixture rejected',
        unknown: 'fixture network timeout。结果未确认，不会自动重发。',
      }[state]
      await expect
        .element(screen.getByText(expected, { exact: true }))
        .toBeVisible()
      await f.echo(message(1))
      await vi.waitFor(() =>
        expect(screen.container.querySelectorAll('.pending-send')).toHaveLength(
          1
        )
      )
      expect(
        screen.container.querySelector(
          '.chat-composer [role="status"], .chat-composer [role="alert"]'
        )?.textContent
      ).toBe(expected)
      expect(f.send).toHaveBeenCalledTimes(2)
      if (state === 'dispatching' || state === 'accepted') {
        if (state === 'dispatching') f.accept()
        await f.echo(message(1), message(2, nextText))
        await vi.waitFor(() =>
          expect(
            screen.container.querySelector('.chat-composer [role="status"]')
          ).toBeNull()
        )
        expect(screen.container.querySelectorAll('.pending-send')).toHaveLength(
          0
        )
      } else {
        expect(
          screen.container.querySelector('.chat-composer [role="alert"]')
            ?.textContent
        ).toBe(expected)
      }
    }
  )
  it('restores an accepted preview on remount without posting again', async () => {
    const f = fixture()
    const first = await render(
      <QueryClientProvider client={f.cache}>
        <ConversationFixture />
      </QueryClientProvider>
    )
    await userEvent.fill(first.getByRole('textbox', { name: '聊天消息' }), text)
    await userEvent.click(
      first.getByRole('button', { name: '发送', exact: true })
    )
    f.accept()
    await expect
      .element(first.getByText('已提交，等待微信回显', { exact: true }))
      .toBeVisible()
    await first.unmount()
    const fresh = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const next = await render(
      <QueryClientProvider client={fresh}>
        <ConversationFixture />
      </QueryClientProvider>
    )
    await expect
      .element(next.getByText('已提交，等待微信回显', { exact: true }))
      .toBeVisible()
    expect(f.send).toHaveBeenCalledTimes(1)
  })
  it('an old account receipt cannot display its text in another account', async () => {
    const f = fixture()
    function Switcher() {
      const [other, setOther] = useState(false)
      return (
        <>
          <button onClick={() => setOther((value) => !value)}>
            switch fixture account
          </button>
          <ConversationFixture
            key={String(other)}
            identity={other ? 'other-fixture' : account}
          />
        </>
      )
    }
    const screen = await render(
      <QueryClientProvider client={f.cache}>
        <Switcher />
      </QueryClientProvider>
    )
    await userEvent.fill(
      screen.getByRole('textbox', { name: '聊天消息' }),
      text
    )
    await userEvent.click(
      screen.getByRole('button', { name: '发送', exact: true })
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'switch fixture account' })
    )
    f.accept()
    await expect
      .element(screen.getByRole('textbox', { name: '聊天消息' }))
      .toBeEnabled()
    expect(screen.container.querySelectorAll('.pending-send')).toHaveLength(0)
    expect(screen.container.textContent).not.toContain(text)
    await userEvent.click(
      screen.getByRole('button', { name: 'switch fixture account' })
    )
    await expect
      .element(screen.getByText('已提交，等待微信回显', { exact: true }))
      .toBeVisible()
    expect(f.send).toHaveBeenCalledTimes(1)
  })
})
