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
import { chatApi, type LiveStatus, type Session } from './api'
import { contactsApi, type Contact, type ContactList } from './contacts-api'
import { ContactsWorkspace } from './contacts-workspace'
import { Chats } from './index'
import { useAccountBoundary } from './use-account-boundary'

vi.mock('./use-live-events', () => ({ useLiveEvents: () => true }))
vi.mock('@/features/archive/notification-bridge', () => ({
  NotificationBridge: () => null,
}))

const alice: Contact = {
  username: 'alice',
  display_name: 'Alice',
  nickname: 'Alice',
  alias: 'alice-alias',
  remark: '',
  initial: 'A',
  is_self: false,
  can_send: true,
  can_edit_remark: true,
  gender: 'female',
  signature: 'Fixture signature',
  region: { country: 'CN', province: 'Guangdong', city: 'Zhaoqing' },
  added_date: '2026-09-14',
  source_scene: 14,
  source_label: '通过群聊添加',
  common_groups_known: true,
  common_groups: [
    { username: 'group@chatroom', display_name: 'Fixture group' },
  ],
}
const zhang: Contact = {
  ...alice,
  username: 'zhang',
  display_name: '张三',
  nickname: '张三',
  initial: 'Z',
  alias: '',
  common_groups: [],
  signature: '',
  region: { country: '', province: '', city: '' },
  source_label: null,
  source_scene: null,
  added_date: null,
}
const self: Contact = {
  ...zhang,
  username: 'fixture-a',
  display_name: 'Self',
  nickname: 'Self',
  initial: 'S',
  is_self: true,
  can_send: false,
  can_edit_remark: false,
}
const bob: Contact = {
  ...alice,
  username: 'bob',
  display_name: 'Bob',
  nickname: 'Bob',
  initial: 'B',
}
const live = (account = 'fixture-a'): LiveStatus => ({
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
const result = (items: Contact[], account = 'fixture-a'): ContactList => ({
  account,
  ready: true,
  directory_count: items.length,
  total: items.length,
  items,
  updated_at: 1000,
})
const openChat = vi.fn<(session: Session) => void>()

beforeEach(async () => {
  await page.viewport(1280, 800)
  openChat.mockReset()
  vi.spyOn(contactsApi, 'list').mockImplementation(async (account, q) => {
    const contacts = account === 'fixture-b' ? [bob] : [alice, self, zhang]
    const filtered = contacts.filter((item) =>
      `${item.display_name} ${item.alias} ${item.username}`
        .toLowerCase()
        .includes(q.toLowerCase())
    )
    return { ...result(filtered, account), directory_count: contacts.length }
  })
  vi.spyOn(contactsApi, 'detail').mockImplementation(
    async (_account, username) => ({
      ...[alice, zhang, self, bob].find((item) => item.username === username)!,
    })
  )
  vi.spyOn(contactsApi, 'refresh').mockResolvedValue(
    result([alice, self, zhang])
  )
  vi.spyOn(contactsApi, 'newFriends').mockResolvedValue({
    account: 'fixture-a',
    ready: true,
    detail: 'Fixture current-account request history',
    items: [
      {
        id: 'request',
        username: 'alice',
        display_name: 'Alice',
        content: 'Hello from fixture',
        timestamp: 1000,
        in_contacts: true,
      },
    ],
  })
  vi.spyOn(contactsApi, 'remark').mockResolvedValue({
    request_id: 'fixture-request',
    status: 'confirmed',
    detail: '已从微信通讯录回读确认备注',
  })
})
afterEach(() => vi.restoreAllMocks())

function ScopedDirectory({ account }: { account: string }) {
  const [selected, setSelected] = useState<string | null>(null)
  return (
    <div
      className='chat-workspace'
      style={{
        height: 750,
        containerType: 'inline-size',
        containerName: 'content',
      }}
    >
      <div className='chat-page' style={{ height: '100%' }}>
        <ContactsWorkspace
          live={live(account)}
          selected={selected}
          select={setSelected}
          openChat={openChat}
        />
      </div>
    </div>
  )
}
function DirectoryHarness() {
  const [account, setAccount] = useState('fixture-a')
  useAccountBoundary(account, true)
  return (
    <>
      <button onClick={() => setAccount('fixture-b')}>
        Switch fixture account
      </button>
      <ScopedDirectory key={account} account={account} />
    </>
  )
}
async function directory() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <DirectoryHarness />
    </QueryClientProvider>
  )
}

describe('verified contacts directory', () => {
  it('shows a true alphabetic directory, including contacts without a conversation', async () => {
    const screen = await directory()
    await expect
      .element(screen.getByRole('button', { name: '查看联系人：Alice' }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('button', { name: '查看联系人：张三' }))
      .toBeVisible()
    expect(
      screen.container.querySelector('.contacts-section-toggle[aria-expanded]')
        ?.textContent
    ).toContain('3')
    expect(screen.container.querySelectorAll('.contact-letter')).toHaveLength(3)
    await screen.getByRole('textbox', { name: '搜索通讯录' }).fill('zhang')
    await expect
      .element(screen.getByRole('button', { name: '查看联系人：张三' }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('button', { name: '查看联系人：Alice' }))
      .not.toBeInTheDocument()
    await screen.getByRole('button', { name: '清空通讯录搜索' }).click()
    await expect
      .element(screen.getByRole('button', { name: '查看联系人：Alice' }))
      .toBeVisible()
    await screen.getByRole('button', { name: '跳到 Z 分组' }).click()
    expect(openChat).not.toHaveBeenCalled()
  })

  it('displays verified profile fields and opens the selected contact or common group', async () => {
    const screen = await directory()
    await screen.getByRole('button', { name: '查看联系人：Alice' }).click()
    await expect
      .element(screen.getByRole('heading', { name: 'Alice' }))
      .toBeVisible()
    await expect.element(screen.getByText('地区：广东 肇庆')).toBeVisible()
    await expect
      .element(screen.getByText('Fixture signature', { exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByText('2026-09-14', { exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('button', { name: '语音聊天（暂未接入）' }))
      .toBeDisabled()
    await screen.getByRole('button', { name: '1 个', exact: true }).click()
    await screen.getByRole('button', { name: 'Fixture group' }).click()
    expect(openChat).toHaveBeenLastCalledWith(
      expect.objectContaining({ username: 'group@chatroom', is_group: true })
    )
    await screen.getByRole('button', { name: '发消息', exact: true }).click()
    expect(openChat).toHaveBeenLastCalledWith(
      expect.objectContaining({ username: 'alice', is_group: false })
    )
    expect(contactsApi.remark).not.toHaveBeenCalled()
  })

  it('uses explicit missing states instead of copying a different profile', async () => {
    const screen = await directory()
    await screen.getByRole('button', { name: '查看联系人：张三' }).click()
    await expect
      .element(screen.getByRole('heading', { name: '张三' }))
      .toBeVisible()
    await expect.element(screen.getByText('地区：本地未提供')).toBeVisible()
    expect(
      screen.container.querySelector('.contact-profile')?.textContent
    ).not.toContain('2026-09-14')
    expect(
      screen.container.querySelector('.contact-profile')?.textContent
    ).not.toContain('Fixture signature')
  })

  it('opens address-book management and only saves after explicit confirmation', async () => {
    const screen = await directory()
    await screen
      .getByRole('button', { name: '通讯录管理', exact: true })
      .click()
    await expect.element(screen.getByRole('table')).toBeVisible()
    expect(screen.container.querySelectorAll('tbody tr')).toHaveLength(3)
    await screen
      .getByRole('row')
      .filter({ hasText: 'alice-alias' })
      .getByRole('button', { name: '修改备注' })
      .click()
    await expect.element(page.getByRole('dialog')).toBeVisible()
    expect(contactsApi.remark).not.toHaveBeenCalled()
    await page.getByRole('textbox', { name: '备注名' }).fill('Fixture remark')
    await page.getByRole('button', { name: '保存到微信' }).click()
    await expect
      .element(page.getByText('已从微信通讯录回读确认备注'))
      .toBeVisible()
    expect(contactsApi.remark).toHaveBeenCalledWith(
      'alice',
      expect.objectContaining({
        account: 'fixture-a',
        remark: 'Fixture remark',
        expected_remark: '',
        request_id: expect.any(String),
      })
    )
    await page.getByRole('button', { name: '完成', exact: true }).click()
    await expect.element(page.getByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps uncertain remark edits pending and reuses their request id for readback', async () => {
    vi.mocked(contactsApi.remark).mockResolvedValue({
      request_id: 'receipt',
      status: 'unknown',
      detail: '请求结果不确定，请核验微信通讯录',
    })
    const screen = await directory()
    await screen.getByRole('button', { name: '查看联系人：Alice' }).click()
    await screen.getByRole('button', { name: '添加备注名' }).click()
    await page.getByRole('textbox', { name: '备注名' }).fill('Pending fixture')
    await page.getByRole('button', { name: '保存到微信' }).click()
    await expect
      .element(page.getByRole('button', { name: '重新核验' }))
      .toBeVisible()
    await expect
      .element(page.getByRole('textbox', { name: '备注名' }))
      .toBeDisabled()
    const firstRequest = vi.mocked(contactsApi.remark).mock.calls[0][1]
    await page.getByRole('button', { name: '重新核验' }).click()
    expect(vi.mocked(contactsApi.remark).mock.calls[1][1]).toEqual(firstRequest)
    await expect
      .element(page.getByText('已从微信通讯录回读确认备注'))
      .not.toBeInTheDocument()
  })

  it('shows actual request history and does not offer a fake accept operation', async () => {
    const screen = await directory()
    await screen.getByRole('button', { name: '新的朋友', exact: true }).click()
    await expect.element(screen.getByText('Hello from fixture')).toBeVisible()
    await screen
      .getByRole('button', { name: '查看联系人', exact: true })
      .click()
    await expect
      .element(screen.getByRole('heading', { name: 'Alice' }))
      .toBeVisible()
    expect(contactsApi.remark).not.toHaveBeenCalled()
  })

  it('distinguishes an unavailable request source from an empty request history', async () => {
    vi.mocked(contactsApi.newFriends).mockResolvedValue({
      account: 'fixture-a',
      ready: false,
      items: [],
      detail: '当前账号本地申请记录尚未就绪',
    })
    const screen = await directory()
    await screen.getByRole('button', { name: '新的朋友', exact: true }).click()
    await expect
      .element(screen.getByText('当前账号本地申请记录尚未就绪'))
      .toBeVisible()
    await expect
      .element(screen.getByText('本地快照暂无好友申请记录'))
      .not.toBeInTheDocument()
  })

  it('discards an old account selection and a delayed contact response', async () => {
    let resolve!: (value: Contact) => void
    vi.mocked(contactsApi.detail).mockImplementation(
      async (_account, username) =>
        username === 'alice'
          ? new Promise<Contact>((done) => {
              resolve = done
            })
          : bob
    )
    const screen = await directory()
    await screen.getByRole('button', { name: '查看联系人：Alice' }).click()
    await vi.waitFor(() => expect(resolve).toBeTypeOf('function'))
    await screen.getByRole('button', { name: 'Switch fixture account' }).click()
    await expect
      .element(screen.getByRole('button', { name: '查看联系人：Bob' }))
      .toBeVisible()
    resolve(alice)
    await screen.getByRole('button', { name: '查看联系人：Bob' }).click()
    await expect
      .element(screen.getByRole('heading', { name: 'Bob' }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('heading', { name: 'Alice' }))
      .not.toBeInTheDocument()
  })

  it('shows loading rather than a fabricated zero count before verification', async () => {
    vi.mocked(contactsApi.list).mockResolvedValue({
      ...result([]),
      ready: false,
      directory_count: null,
    })
    const screen = await directory()
    await expect
      .element(screen.getByText('待核验', { exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByText('正在核验当前微信通讯录…'))
      .toBeVisible()
    await expect
      .element(screen.getByText('当前通讯录暂无联系人'))
      .not.toBeInTheDocument()
  })

  it('has no page overflow on mobile and can return from details', async () => {
    await page.viewport(375, 812)
    const screen = await directory()
    await screen.getByRole('button', { name: '查看联系人：Alice' }).click()
    await expect
      .element(screen.getByRole('button', { name: '返回通讯录' }))
      .toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(375)
    const right = screen.container
      .querySelector('.contact-profile')!
      .getBoundingClientRect().right
    expect(right).toBeLessThanOrEqual(375)
    await screen.getByRole('button', { name: '返回通讯录' }).click()
    await expect
      .element(screen.getByRole('textbox', { name: '搜索通讯录' }))
      .toBeVisible()
  })
})

it('integrates contact navigation with the real chat workspace without sending automatically', async () => {
  vi.spyOn(chatApi, 'status').mockResolvedValue(live())
  vi.spyOn(chatApi, 'list').mockResolvedValue({ items: [], total: 0 })
  vi.spyOn(chatApi, 'send').mockRejectedValue(new Error('must not send'))
  vi.spyOn(chatApi, 'detail').mockImplementation(async (account, username) => ({
    account,
    session: {
      username,
      display_name: 'Alice',
      is_group: false,
      can_send: true,
      message_count: 0,
    },
    messages: [],
    next_cursor: null,
    boundary_cursor: null,
    has_more: false,
    revision: 0,
    elapsed_ms: 1,
    history_revision: 'fixture',
  }))
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
      <ThemeProvider defaultTheme='light' storageKey='contacts-test-theme'>
        <FontProvider>
          <DirectionProvider>
            <RouterProvider router={router} />
          </DirectionProvider>
        </FontProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
  await screen.getByRole('button', { name: '联系人', exact: true }).click()
  await screen.getByRole('button', { name: '查看联系人：Alice' }).click()
  await screen.getByRole('button', { name: '发消息', exact: true }).click()
  await expect
    .element(screen.getByRole('textbox', { name: '聊天消息' }))
    .toBeVisible()
  expect(chatApi.detail).toHaveBeenCalledWith(
    'fixture-a',
    'alice',
    null,
    expect.any(AbortSignal)
  )
  expect(chatApi.send).not.toHaveBeenCalled()
  expect(
    vi.mocked(chatApi.list).mock.calls.every((call) => call[2] === 'all')
  ).toBe(true)
})
