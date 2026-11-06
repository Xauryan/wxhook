import { describe, expect, it, vi } from 'vitest'
import type { Message } from './api'
import {
  captureAnchor,
  displayTime,
  isNearBottom,
  mergeMessages,
} from './message-flow'

const message = (id: string, revision = 1, time = 100): Message => ({
  id,
  revision,
  order_time: time,
  category: 'text',
  content: 'same text',
})
describe('account-scoped message flow', () => {
  it('retains cached array and message identities when a delta is unchanged', () => {
    const current = [message('1'), message('2')]
    expect(mergeMessages(current, [])).toBe(current)
    expect(mergeMessages(current, [message('1')])).toBe(current)
    const next = mergeMessages(current, [message('3')])
    expect(next[0]).toBe(current[0])
    expect(next[1]).toBe(current[1])
  })

  it('locates a viewport anchor without measuring every loaded message', () => {
    const element = document.createElement('div')
    let reads = 0
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
      top: 0,
    } as DOMRect)
    for (let i = 0; i < 2000; i++) {
      const row = document.createElement('div')
      row.dataset.messageId = String(i)
      vi.spyOn(row, 'getBoundingClientRect').mockImplementation(() => {
        reads++
        return { top: (i - 1500) * 50, bottom: (i - 1500 + 1) * 50 } as DOMRect
      })
      element.append(row)
    }
    expect(captureAnchor(element)?.id).toBe('1500')
    expect(reads).toBeLessThan(15)
    vi.restoreAllMocks()
  })
  it('uses Shanghai-local times for today, yesterday and older messages', () => {
    const clock = vi
      .spyOn(Date, 'now')
      .mockReturnValue(Date.parse('2026-09-22T03:00:00Z'))
    try {
      expect(displayTime('2026-09-22T01:12:00Z')).toBe('09:12')
      expect(displayTime('2026-09-21T12:02:00Z')).toBe('昨天 20:02')
      expect(displayTime('2026-09-20T12:02:00Z')).toBe('2026/09/20')
      expect(displayTime('2026-09-20T12:02:00Z', true)).toBe('2026/09/20 20:02')
      expect(displayTime('invalid')).toBe('')
    } finally {
      clock.mockRestore()
    }
  })
  it('merges overlapping history and realtime updates by stable identity', () => {
    const result = mergeMessages(
      [message('1'), message('2')],
      [message('2', 2), message('3')]
    )
    expect(result.map((item) => item.id)).toEqual(['1', '2', '3'])
    expect(result[1].revision).toBe(2)
  })
  it('does not deduplicate repeated text or truncate bodies', () => {
    const full = 'literal <tag> & \n' + 'x'.repeat(2000)
    const result = mergeMessages(
      [{ ...message('1'), content: full }],
      [message('2')]
    )
    expect(result).toHaveLength(2)
    expect(result[0].content).toBe(full)
  })
  it('an older page cannot overwrite a newer edit or resurrect a tombstone', () => {
    const result = mergeMessages(
      [message('1', 10), message('2')],
      [message('1', 1), message('2')],
      ['2']
    )
    expect(result).toEqual([message('1', 10)])
  })
  it('keeps deterministic ordering for equal timestamps', () => {
    expect(
      mergeMessages([message('20')], [message('3'), message('1', 1, 90)]).map(
        (item) => item.id
      )
    ).toEqual(['1', '3', '20'])
  })
  it('does not consider reading older messages to be following latest', () => {
    expect(
      isNearBottom({ scrollHeight: 4000, clientHeight: 700, scrollTop: 1000 })
    ).toBe(false)
    expect(
      isNearBottom({ scrollHeight: 4000, clientHeight: 700, scrollTop: 3270 })
    ).toBe(true)
  })
})
