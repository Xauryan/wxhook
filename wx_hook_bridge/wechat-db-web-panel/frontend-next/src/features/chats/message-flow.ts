import type { Message } from './api'

const dayFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  timeZone: 'Asia/Shanghai',
})
const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: 'Asia/Shanghai',
})
const fullFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'Asia/Shanghai',
})

export function displayTime(value?: string, full = false) {
  if (!value || Number.isNaN(new Date(value).getTime())) return ''
  const date = new Date(value)
  const now = Date.now()
  if (dayFormatter.format(date) === dayFormatter.format(now))
    return timeFormatter.format(date)
  if (dayFormatter.format(date) === dayFormatter.format(now - 86400000))
    return `昨天 ${timeFormatter.format(date)}`
  return (full ? fullFormatter : dayFormatter).format(date)
}

export function mergeMessages(
  current: Message[],
  incoming: Message[],
  deleted: string[] = []
): Message[] {
  if (!incoming.length && !deleted.length) return current
  const removed = new Set(deleted)
  let changed = current.some((item) => removed.has(item.id))
  const values = new Map(
    current
      .filter((item) => !removed.has(item.id))
      .map((item) => [item.id, item])
  )
  for (const item of incoming) {
    const old = values.get(item.id)
    if (
      !removed.has(item.id) &&
      (!old || item.revision >= old.revision) &&
      old !== item &&
      (!old ||
        item.revision !== old.revision ||
        JSON.stringify(item) !== JSON.stringify(old))
    ) {
      values.set(item.id, item)
      changed = true
    }
  }
  if (!changed) return current
  return [...values.values()].sort(
    (a, b) => a.order_time - b.order_time || Number(a.id) - Number(b.id)
  )
}

export function isNearBottom(
  element: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>
) {
  return element.scrollHeight - element.scrollTop - element.clientHeight < 100
}

export type ScrollAnchor = { id: string; offset: number }
export function captureAnchor(element: HTMLElement): ScrollAnchor | null {
  const top = element.getBoundingClientRect().top
  const nodes = element.querySelectorAll<HTMLElement>('[data-message-id]')
  let left = 0
  let right = nodes.length
  // Reading every row's geometry defeats deferred layout and stalls long chats.
  while (left < right) {
    const middle = (left + right) >>> 1
    if (nodes[middle].getBoundingClientRect().bottom > top) right = middle
    else left = middle + 1
  }
  const node = nodes[left]
  return node
    ? {
        id: node.dataset.messageId!,
        offset: node.getBoundingClientRect().top - top,
      }
    : null
}
export function restoreAnchor(
  element: HTMLElement,
  anchor: ScrollAnchor | null
) {
  if (!anchor) return
  const node = element.querySelector<HTMLElement>(
    `[data-message-id="${CSS.escape(anchor.id)}"]`
  )
  if (node)
    element.scrollTop +=
      node.getBoundingClientRect().top -
      element.getBoundingClientRect().top -
      anchor.offset
}
