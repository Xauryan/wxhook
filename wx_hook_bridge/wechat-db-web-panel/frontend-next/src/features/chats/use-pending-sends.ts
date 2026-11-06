import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react'
import type { Message, SendReceipt } from './api'
import {
  readPendingSends,
  reconcilePendingSends,
  type PendingSend,
} from './pending-sends'

const snapshots = new Map<string, PendingSend[]>()
const subscribers = new Map<string, Set<() => void>>()

function snapshot(key: string): PendingSend[] {
  let current = snapshots.get(key)
  if (!current) {
    current = readPendingSends(key)
    snapshots.set(key, current)
  }
  return current
}
function publish(
  key: string,
  update: (current: PendingSend[]) => PendingSend[]
) {
  const current = snapshot(key)
  const next = update(current)
  if (next === current) return
  snapshots.set(key, next)
  try {
    sessionStorage.setItem(key, JSON.stringify(next))
  } catch {
    /* Keep in-memory submissions visible. */
  }
  // Local user actions must render in the same turn, including background tabs
  // where timer-based query-cache notifications can be throttled by Chrome.
  subscribers.get(key)?.forEach((notify) => notify())
}

export function usePendingSends(
  account: string,
  username: string,
  messages: Message[],
  revision: number
) {
  const key = 'wechat-pending-sends-v1:' + JSON.stringify([account, username])
  const subscribe = useCallback(
    (notify: () => void) => {
      let listeners = subscribers.get(key)
      if (!listeners) {
        listeners = new Set()
        subscribers.set(key, listeners)
      }
      listeners.add(notify)
      return () => {
        listeners.delete(notify)
        if (!listeners.size) subscribers.delete(key)
      }
    },
    [key]
  )
  const getSnapshot = useCallback(() => snapshot(key), [key])
  const data = useSyncExternalStore(subscribe, getSnapshot)
  const reconciled = useMemo(
    () => reconcilePendingSends(data, messages),
    [data, messages]
  )
  useEffect(() => {
    if (reconciled !== data)
      publish(key, (current) => reconcilePendingSends(current, messages))
  }, [data, key, messages, reconciled])
  const submit = useCallback(
    (request: string, text: string) => {
      publish(key, (current) =>
        current.some((item) => item.request === request)
          ? current
          : [
              ...current,
              {
                request,
                text,
                createdAt: Date.now(),
                status: 'dispatching',
                baselineRevision: revision,
                knownIds: messages
                  .filter((item) => item.is_self && item.content === text)
                  .map((item) => item.id),
              },
            ]
      )
    },
    [key, messages, revision]
  )
  const update = useCallback(
    (request: string, status: SendReceipt['status']) => {
      publish(key, (current) =>
        current.map((item) =>
          item.request === request && item.status !== status
            ? { ...item, status }
            : item
        )
      )
    },
    [key]
  )
  return {
    messages: useMemo(
      () => reconciled.filter((item) => !item.messageId),
      [reconciled]
    ),
    confirmedRequests: useMemo(
      () =>
        new Set(
          reconciled
            .filter((item) => item.messageId)
            .map((item) => item.request)
        ),
      [reconciled]
    ),
    submit,
    update,
  }
}
