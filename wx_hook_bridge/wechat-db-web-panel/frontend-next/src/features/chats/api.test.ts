import { afterEach, describe, expect, it, vi } from 'vitest'
import { chatApi } from './api'

const request = 'fixture-request'
const accepted = {
  request_id: request,
  status: 'accepted',
  detail: 'fixture accepted',
}
const send = () =>
  chatApi.send('fixture-account', 'fixture-peer', 'fixture only', request)

afterEach(() => vi.unstubAllGlobals())

describe('chat API response failures', () => {
  it.each([
    [500, 'Internal Server Error', 'text/plain'],
    [502, '<html>Bad Gateway</html>', 'text/html'],
  ])(
    'reports HTTP %s without exposing JSON parser errors or retrying',
    async (status, body, contentType) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(
          new Response(body, {
            status,
            headers: { 'Content-Type': contentType },
          })
        )
      vi.stubGlobal('fetch', fetchMock)
      await expect(send()).rejects.toThrow(`HTTP ${status}`)
      expect(fetchMock).toHaveBeenCalledTimes(1)
    }
  )

  it('keeps the structured server detail for an exhausted SQLite writer wait', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { detail: '本地消息库繁忙，本次请求未提交微信' },
          { status: 503 }
        )
      )
    vi.stubGlobal('fetch', fetchMock)
    await expect(send()).rejects.toThrow('本地消息库繁忙，本次请求未提交微信')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['Internal Server Error', 'application/json'],
    ['<html>proxy</html>', 'text/html'],
    ['null', 'application/json'],
    ['[]', 'application/json'],
  ])(
    'rejects an invalid successful response without inventing a receipt',
    async (body, contentType) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(
          new Response(body, { headers: { 'Content-Type': contentType } })
        )
      vi.stubGlobal('fetch', fetchMock)
      await expect(send()).rejects.toThrow('本地接口返回了无法识别的结果')
      expect(fetchMock).toHaveBeenCalledTimes(1)
    }
  )

  it.each([
    {},
    { ...accepted, request_id: 'another-request' },
    { ...accepted, status: 'delivered' },
    { ...accepted, detail: null },
  ])('rejects a malformed or unrelated receipt', async (payload) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(payload)))
    await expect(send()).rejects.toThrow('发送回执格式异常')
  })

  it('returns a valid accepted receipt with no delivery claim', async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(accepted))
    vi.stubGlobal('fetch', fetchMock)
    await expect(send()).resolves.toEqual(accepted)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      account: 'fixture-account',
      username: 'fixture-peer',
      text: 'fixture only',
      request_id: request,
    })
  })

  it('handles a plain-text receipt lookup failure without dispatching', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('Internal Server Error', { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(chatApi.receipt('fixture-account', request)).rejects.toThrow(
      'HTTP 500'
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][1].method).toBeUndefined()
  })
})
