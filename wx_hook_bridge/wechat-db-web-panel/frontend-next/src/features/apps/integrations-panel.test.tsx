import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@/styles/index.css'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page } from 'vitest/browser'
import { integrationsApi, type IntegrationStatus } from './api'
import { IntegrationsPanel } from './integrations-panel'

let state: IntegrationStatus
let client: QueryClient

beforeEach(async () => {
  await page.viewport(1280, 900)
  state = {
    account: 'fixture-a',
    profile: { username: 'fixture-a', display_name: 'Fixture account' },
    friends: {
      config: { enabled: false, reply_enabled: false, reply_text: '' },
      revision: 0,
      updated_at: 0,
    },
    groups: {
      config: { enabled: false, keyword: '进群', room: '' },
      revision: 0,
      updated_at: 0,
    },
    hongbao: {
      available: true,
      enabled: false,
      receive_only: false,
      delay_ms: '0',
      scope: 'native_process',
      detail: '原生进程级测试状态',
    },
    native_friends: { available: true, enabled: false },
    runtime: { running: true, available: true, error: null },
    actions: [],
  }
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  vi.spyOn(integrationsApi, 'status').mockImplementation(async () =>
    structuredClone(state)
  )
  vi.spyOn(integrationsApi, 'groups').mockResolvedValue({
    account: state.account,
    checked_at: Date.now() / 1000,
    items: [
      {
        username: 'owner@chatroom',
        name: 'Owned fixture',
        role: 'owner',
        can_manage: true,
        reason: '当前账号为群主',
      },
      {
        username: 'unknown@chatroom',
        name: 'Unverified fixture',
        role: 'unverified',
        can_manage: false,
        reason: '管理权限未确认',
      },
    ],
  })
  vi.spyOn(integrationsApi, 'save').mockImplementation(
    async (feature, account, revision, config) => {
      expect(account).toBe(state.account)
      const saved = {
        config,
        revision: revision + 1,
        updated_at: Date.now() / 1000,
      }
      state = {
        ...state,
        [feature]: saved,
        ...(feature === 'friends'
          ? { native_friends: { available: true, enabled: false } }
          : {}),
      }
      return saved
    }
  )
  vi.spyOn(integrationsApi, 'hongbao').mockImplementation(
    async (_account, enabled) => {
      state.hongbao.enabled = enabled
      return { ...state.hongbao }
    }
  )
  vi.spyOn(integrationsApi, 'hongbaoDelay').mockImplementation(
    async (_account, value) => {
      state.hongbao.delay_ms = value
      return { ...state.hongbao }
    }
  )
})

afterEach(() => {
  client.clear()
  vi.restoreAllMocks()
})

async function mount() {
  return render(
    <QueryClientProvider client={client}>
      <div style={{ padding: 16 }}>
        <IntegrationsPanel />
      </div>
    </QueryClientProvider>
  )
}

describe('real application integrations', () => {
  it('saves a random delay without toggling the native switch', async () => {
    const screen = await mount()
    await screen.getByRole('button', { name: '配置红包秒抢' }).click()
    await page
      .getByRole('textbox', { name: '领取延迟（毫秒）' })
      .fill('300-800')
    await page.getByRole('button', { name: '保存延迟' }).click()
    expect(integrationsApi.hongbaoDelay).toHaveBeenCalledWith(
      'fixture-a',
      '300-800',
      '0'
    )
    expect(integrationsApi.hongbao).not.toHaveBeenCalled()
    await expect
      .element(screen.getByText('领取延迟已保存为 300-800 ms。'))
      .toBeVisible()
    await expect
      .element(screen.getByRole('switch', { name: '红包秒抢开关' }))
      .not.toBeChecked()
  })

  it('rejects invalid delay and preserves the draft on a stale configuration response', async () => {
    const screen = await mount()
    await screen.getByRole('button', { name: '配置红包秒抢' }).click()
    await page
      .getByRole('textbox', { name: '领取延迟（毫秒）' })
      .fill('800-300')
    await expect
      .element(page.getByRole('button', { name: '保存延迟' }))
      .toBeDisabled()
    await page.getByRole('textbox', { name: '领取延迟（毫秒）' }).fill('600001')
    await expect
      .element(page.getByRole('button', { name: '保存延迟' }))
      .toBeDisabled()
    await page.getByRole('textbox', { name: '领取延迟（毫秒）' }).fill('500')
    vi.mocked(integrationsApi.hongbaoDelay).mockRejectedValueOnce(
      new Error('配置已变化，请刷新核对')
    )
    await page.getByRole('button', { name: '保存延迟' }).click()
    await expect.element(page.getByText('配置已变化，请刷新核对')).toBeVisible()
    await expect
      .element(page.getByRole('textbox', { name: '领取延迟（毫秒）' }))
      .toHaveValue('500')
  })

  it('shows existing native state without changing any switch on mount', async () => {
    state.native_friends.enabled = true
    state.hongbao.enabled = true
    const screen = await mount()
    await expect
      .element(screen.getByText('原生已开启', { exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('switch', { name: '好友自动通过开关' }))
      .toBeChecked()
    await expect
      .element(screen.getByRole('switch', { name: '红包秒抢开关' }))
      .toBeChecked()
    expect(integrationsApi.save).not.toHaveBeenCalled()
    expect(integrationsApi.hongbao).not.toHaveBeenCalled()
  })

  it('validates reply text and saves the current account and revision', async () => {
    const screen = await mount()
    await screen.getByRole('button', { name: '配置好友自动通过' }).click()
    await page
      .getByRole('switch', { name: '通过后自动回复', exact: true })
      .click()
    await expect
      .element(page.getByRole('button', { name: '保存配置' }))
      .toBeDisabled()
    await page
      .getByRole('textbox', { name: '回复内容' })
      .fill('欢迎加入，回复进群即可。')
    await page.getByRole('switch', { name: '自动通过新好友申请' }).click()
    await page.getByRole('button', { name: '保存配置' }).click()
    expect(integrationsApi.save).toHaveBeenCalledWith(
      'friends',
      'fixture-a',
      0,
      {
        enabled: true,
        reply_enabled: true,
        reply_text: '欢迎加入，回复进群即可。',
      }
    )
    await expect
      .element(screen.getByRole('switch', { name: '好友自动通过开关' }))
      .toBeChecked()
  })

  it('clears inherited native state immediately after an explicit disable', async () => {
    state.native_friends.enabled = true
    const screen = await mount()
    await expect
      .element(screen.getByText('原生已开启', { exact: true }))
      .toBeVisible()
    vi.mocked(integrationsApi.status).mockImplementation(
      () => new Promise(() => {})
    )
    await screen.getByRole('switch', { name: '好友自动通过开关' }).click()
    await expect
      .element(screen.getByRole('switch', { name: '好友自动通过开关' }))
      .not.toBeChecked()
    await expect
      .element(screen.getByText('原生已开启', { exact: true }))
      .not.toBeInTheDocument()
    expect(integrationsApi.save).toHaveBeenCalledWith(
      'friends',
      'fixture-a',
      0,
      state.friends.config
    )
  })

  it('keeps the old switch state and shows an error when persistence fails', async () => {
    vi.mocked(integrationsApi.save).mockRejectedValue(
      new Error('fixture 保存失败')
    )
    const screen = await mount()
    await screen.getByRole('switch', { name: '好友自动通过开关' }).click()
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('fixture 保存失败')
    await expect
      .element(screen.getByRole('switch', { name: '好友自动通过开关' }))
      .not.toBeChecked()
  })

  it('allows only a verified managed group and sends exact keyword configuration', async () => {
    const screen = await mount()
    await screen.getByRole('button', { name: '配置群聊自动拉人' }).click()
    await expect
      .element(page.getByRole('radio', { name: /Unverified fixture/ }))
      .toBeDisabled()
    await page.getByRole('switch', { name: '启用关键词自动拉人' }).click()
    await expect
      .element(page.getByRole('button', { name: '保存配置' }))
      .toBeDisabled()
    await page.getByRole('radio', { name: /Owned fixture/ }).click()
    await page.getByRole('textbox', { name: '私聊关键词' }).fill('加入')
    await page.getByRole('button', { name: '保存配置' }).click()
    expect(integrationsApi.save).toHaveBeenCalledWith(
      'groups',
      'fixture-a',
      0,
      { enabled: true, keyword: '加入', room: 'owner@chatroom' }
    )
    await expect
      .element(screen.getByRole('switch', { name: '群聊自动拉人开关' }))
      .toBeChecked()
  })

  it('requires confirmation before enabling native hongbao', async () => {
    const screen = await mount()
    await screen.getByRole('switch', { name: '红包秒抢开关' }).click()
    expect(integrationsApi.hongbao).not.toHaveBeenCalled()
    await expect
      .element(page.getByRole('dialog'))
      .toHaveTextContent('不按账号隔离')
    await page.getByRole('button', { name: '确认开启' }).click()
    expect(integrationsApi.hongbao).toHaveBeenCalledWith(
      'fixture-a',
      true,
      false
    )
    await expect
      .element(screen.getByRole('switch', { name: '红包秒抢开关' }))
      .toBeChecked()
  })

  it('disables unavailable native controls instead of pretending they are off', async () => {
    state.hongbao.enabled = null
    state.hongbao.available = false
    const screen = await mount()
    await expect
      .element(screen.getByText('状态未知', { exact: true }))
      .toBeVisible()
    await expect
      .element(screen.getByRole('switch', { name: '红包秒抢开关' }))
      .toBeDisabled()
  })

  it('discards the old account editor when the verified account changes', async () => {
    const screen = await mount()
    await screen.getByRole('button', { name: '配置好友自动通过' }).click()
    await page
      .getByRole('textbox', { name: '回复内容' })
      .fill('Old account draft')
    state = {
      ...state,
      account: 'fixture-b',
      profile: { username: 'fixture-b', display_name: 'Second account' },
    }
    client.setQueryData(['app-integrations'], structuredClone(state))
    await expect.element(page.getByRole('dialog')).not.toBeInTheDocument()
    await expect.element(screen.getByText('Second account')).toBeVisible()
    expect(integrationsApi.save).not.toHaveBeenCalled()
  })

  it('shows read failures and does not expose false controls', async () => {
    vi.mocked(integrationsApi.status).mockRejectedValue(
      new Error('fixture disconnected')
    )
    const screen = await mount()
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('fixture disconnected')
    await expect
      .element(screen.getByRole('switch', { name: '好友自动通过开关' }))
      .not.toBeInTheDocument()
  })

  it('fits narrow screens and filters the three actual applications', async () => {
    await page.viewport(375, 812)
    const screen = await mount()
    await expect
      .element(screen.getByRole('heading', { name: '红包秒抢' }))
      .toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(375)
    await screen.getByRole('textbox', { name: '搜索应用' }).fill('不存在的应用')
    await expect
      .element(screen.getByText('没有匹配的应用，请调整搜索或筛选条件。'))
      .toBeVisible()
  })
})
