import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { momentsApi, MomentsApiError, type MomentList } from './moments-api'

const payload: MomentList = {
  account: 'api-fixture-account',
  ready: true,
  syncing: false,
  total: 0,
  items: [],
  next_cursor: null,
  has_more: false,
  source: 'active_sns_db',
  write_supported: false,
  write_detail: 'Fixture read-only source',
}

const fetchFixture = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchFixture.mockReset()
  vi.stubGlobal('fetch', fetchFixture)
})

afterEach(() => vi.unstubAllGlobals())

describe('moments API response boundaries with isolated fetch fixtures', () => {
  it('rejects a next-page HTTP 200 payload carrying a snapshot error', async () => {
    fetchFixture.mockResolvedValue(
      Response.json({ ...payload, error: 'Fixture snapshot unavailable' })
    )
    await expect(
      momentsApi.list(payload.account, '', 'all', 'fixture-next')
    ).rejects.toThrow('Fixture snapshot unavailable')
  })

  it('retains first-page snapshot error metadata for the cached-feed UI', async () => {
    const stale = { ...payload, error: 'Fixture snapshot unavailable' }
    fetchFixture.mockResolvedValue(Response.json(stale))
    await expect(
      momentsApi.list(payload.account, '', 'all', null)
    ).resolves.toEqual(stale)
  })

  it('rejects data belonging to a different active account', async () => {
    fetchFixture.mockResolvedValue(
      Response.json({ ...payload, account: 'different-fixture-account' })
    )
    await expect(
      momentsApi.list(payload.account, '', 'all', null)
    ).rejects.toThrow('微信账号已变化')
  })

  it('rejects a refresh HTTP 200 payload carrying an error', async () => {
    fetchFixture.mockResolvedValue(
      Response.json({ ...payload, error: 'Fixture refresh failed' })
    )
    await expect(momentsApi.refresh(payload.account)).rejects.toThrow(
      'Fixture refresh failed'
    )
    expect(fetchFixture).toHaveBeenCalledWith(
      expect.stringContaining(
        '/chat/moments/refresh?account=api-fixture-account'
      ),
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('preserves HTTP status on cursor conflicts for a first-page retry', async () => {
    fetchFixture.mockResolvedValue(
      Response.json({ detail: 'Fixture cursor expired' }, { status: 409 })
    )
    const request = momentsApi.list(
      payload.account,
      '',
      'all',
      'fixture-expired'
    )
    await expect(request).rejects.toBeInstanceOf(MomentsApiError)
    await expect(request).rejects.toMatchObject({
      status: 409,
      message: 'Fixture cursor expired',
    })
  })

  it('accepts a background retry acknowledgement carrying the previous error', async () => {
    const retrying = {
      ...payload,
      syncing: true,
      error: 'Previous read failed',
    }
    fetchFixture.mockResolvedValue(Response.json(retrying))
    await expect(momentsApi.refresh(payload.account)).resolves.toEqual(retrying)
  })
})
