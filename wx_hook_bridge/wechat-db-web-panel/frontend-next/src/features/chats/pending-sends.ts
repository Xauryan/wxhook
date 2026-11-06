import type { Message, SendReceipt } from './api'

export type PendingSend = {
  request: string
  text: string
  createdAt: number
  status: SendReceipt['status']
  baselineRevision: number
  knownIds: string[]
  messageId?: string
}

export function readPendingSends(key: string): PendingSend[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) || '[]')
    if (!Array.isArray(value)) return []
    return value.filter(
      (item): item is PendingSend =>
        !!item &&
        typeof item.request === 'string' &&
        typeof item.text === 'string' &&
        item.text.length <= 10000 &&
        Number.isFinite(item.createdAt) &&
        Number.isFinite(item.baselineRevision) &&
        ['dispatching', 'accepted', 'unknown', 'failed'].includes(
          item.status
        ) &&
        Array.isArray(item.knownIds) &&
        item.knownIds.every((id: unknown) => typeof id === 'string') &&
        (item.messageId === undefined || typeof item.messageId === 'string') &&
        (!item.messageId || item.createdAt > Date.now() - 86400000)
    )
  } catch {
    return []
  }
}

export function reconcilePendingSends(
  records: PendingSend[],
  messages: Message[]
): PendingSend[] {
  const claimed = new Set(
    records.flatMap((item) => (item.messageId ? [item.messageId] : []))
  )
  const candidates = messages
    .filter(
      (item) =>
        item.is_self === true &&
        item.category === 'text' &&
        /^[1-9]\d*$/.test(item.server_id || '')
    )
    .sort((a, b) => a.order_time - b.order_time || Number(a.id) - Number(b.id))
  let changed = false
  const next = records.map((record) => {
    if (record.messageId || record.status === 'failed') return record
    const message = candidates.find(
      (item) =>
        !claimed.has(item.id) &&
        !record.knownIds.includes(item.id) &&
        item.revision > record.baselineRevision &&
        item.content === record.text &&
        item.order_time >= record.createdAt / 1000 - 5 &&
        item.order_time <= record.createdAt / 1000 + 120
    )
    if (!message) return record
    claimed.add(message.id)
    changed = true
    return { ...record, messageId: message.id }
  })
  // Only local previews are reconciled. Real messages keep their native IDs,
  // including repeated text and messages from other devices.
  return changed ? next : records
}
