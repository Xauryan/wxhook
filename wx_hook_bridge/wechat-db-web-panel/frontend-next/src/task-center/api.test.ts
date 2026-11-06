import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, write, type Me } from './api'

const me: Me = {
  id: 'member-a',
  login: 'alice',
  name: 'Alice',
  owner: false,
  csrf: 'fixture-csrf',
  title: '任务中心',
}

describe('central task writes', () => {
  beforeEach(() => sessionStorage.clear())
  afterEach(() => vi.unstubAllGlobals())

  it('retries an ambiguous response with the original body and idempotency key', async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('connection closed'))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: 'task' }), { status: 200 })
      )
    vi.stubGlobal('fetch', fetcher)
    await expect(
      write(me, '/tasks/task/actions/comment', {
        expected_version: 4,
        text: 'one',
      })
    ).rejects.toThrow()
    await write(me, '/tasks/task/actions/comment', {
      expected_version: 5,
      text: 'one',
    })
    expect(fetcher.mock.calls[0][1].body).toBe(fetcher.mock.calls[1][1].body)
    expect(fetcher.mock.calls[0][1].headers['Idempotency-Key']).toBe(
      fetcher.mock.calls[1][1].headers['Idempotency-Key']
    )
    expect(sessionStorage.length).toBe(0)
  })

  it('does not treat a rejected mutation as success', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ message: 'conflict' }), { status: 409 })
        )
    )
    await expect(
      write(me, '/tasks/task/actions/claim', { expected_version: 1 })
    ).rejects.toBeInstanceOf(ApiError)
    expect(sessionStorage.length).toBe(0)
  })

  it('does not reuse commands across identities', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('offline'))
    vi.stubGlobal('fetch', fetcher)
    await write(me, '/tasks', { title: 'one' }).catch(() => {})
    await write({ ...me, id: 'member-b' }, '/tasks', { title: 'one' }).catch(
      () => {}
    )
    expect(fetcher.mock.calls[0][1].headers['Idempotency-Key']).not.toBe(
      fetcher.mock.calls[1][1].headers['Idempotency-Key']
    )
  })
})
