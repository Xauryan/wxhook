import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { base, chatApi, type LiveStatus, type SessionDetail } from './api'

export function useLiveEvents(account: string) {
  const queryClient = useQueryClient()
  const [connected, setConnected] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!account) return
    const storageKey = `live-chat-cursor:${account}`
    const statusKey = ['wechat-live-status-v2']
    const initialStatus = queryClient.getQueryData<LiveStatus>(statusKey)
    let cursor = String(
      initialStatus?.account === account ? initialStatus.newest_sequence : 0
    )
    try {
      cursor = sessionStorage.getItem(storageKey) || cursor
    } catch {
      /* No-storage browsers still reconnect. */
    }
    const url = new URL(`${base}/chat/events`, location.origin)
    url.searchParams.set('account', account)
    if (/^\d+$/.test(cursor)) url.searchParams.set('after', cursor)
    const source = new EventSource(url)
    let timer: ReturnType<typeof setTimeout> | undefined
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    let opened = false
    let closed = false
    let flushing = false
    let rosterDirty = false
    let pending: Set<string> | null = new Set()
    let pendingRevision = 0
    let contactsRevision = initialStatus?.contacts_revision
    const reconnect = () => {
      if (!closed && !reconnectTimer)
        reconnectTimer = setTimeout(
          () => setAttempt((value) => value + 1),
          Math.min(2000 * 2 ** Math.min(attempt, 3), 15000)
        )
    }

    const refresh = (sessions: string[] | null, revision = 0) => {
      rosterDirty = true
      if (sessions === null) pending = null
      else if (pending) sessions.forEach((session) => pending!.add(session))
      pendingRevision = Math.max(pendingRevision, revision)
      if (!timer && !flushing && !closed)
        timer = setTimeout(() => void flush(), 120)
    }
    const flush = async () => {
      timer = undefined
      if (closed) return
      flushing = true
      const sessions = pending
      const revision = pendingRevision
      const updateRoster = rosterDirty
      pending = new Set()
      pendingRevision = 0
      rosterDirty = false
      const filter = {
        queryKey: ['wechat-flow', account],
        predicate: (query: { queryKey: readonly unknown[] }) =>
          sessions === null || sessions.has(String(query.queryKey[2])),
      }
      try {
        await Promise.all([
          updateRoster
            ? queryClient.invalidateQueries(
                { queryKey: ['wechat-roster-v2', account] },
                { cancelRefetch: false }
              )
            : undefined,
          queryClient.invalidateQueries(filter, { cancelRefetch: false }),
        ])
        // An event may arrive after an already-running request took its DB
        // snapshot. Catch it up once instead of losing it or cancelling reads.
        if (!closed && revision > 0) {
          await queryClient.refetchQueries(
            {
              ...filter,
              type: 'active',
              predicate: (query) =>
                filter.predicate(query) &&
                !!query.state.data &&
                (query.state.data as SessionDetail).revision < revision,
            },
            { cancelRefetch: false }
          )
        }
      } finally {
        flushing = false
        if (!closed && rosterDirty && !timer)
          timer = setTimeout(() => void flush(), 120)
      }
    }
    source.onopen = () => {
      setConnected(true)
      if (!queryClient.getQueryData<LiveStatus>(statusKey)?.bridge_connected)
        void queryClient.invalidateQueries(
          { queryKey: statusKey },
          { cancelRefetch: false }
        )
      if (
        opened ||
        queryClient
          .getQueriesData({ queryKey: ['wechat-flow', account] })
          .some(([, data]) => data)
      )
        refresh(null)
      opened = true
    }
    source.onerror = () => {
      setConnected(false)
      if (source.readyState === EventSource.CLOSED) reconnect()
    }
    source.addEventListener('chat.updated', (event) => {
      try {
        const message = JSON.parse(event.data)
        if (message.account !== account) return
        try {
          sessionStorage.setItem(storageKey, event.lastEventId)
        } catch {
          /* Reconnect without persisted cursor. */
        }
        const sessions =
          Array.isArray(message.sessions) &&
          message.sessions.every((value: unknown) => typeof value === 'string')
            ? (message.sessions as string[])
            : null
        refresh(sessions, Number(message.cursor) || 0)
      } catch {
        /* Ignore malformed events, never invent messages. */
      }
    })
    source.addEventListener('chat.contacts', (event) => {
      try {
        const message = JSON.parse(event.data)
        if (
          message.account !== account ||
          message.revision === contactsRevision
        )
          return
        contactsRevision = message.revision
        void queryClient.invalidateQueries(
          { queryKey: ['wechat-contacts', account] },
          { cancelRefetch: false }
        )
        refresh([])
        void queryClient.invalidateQueries(
          { queryKey: statusKey },
          { cancelRefetch: false }
        )
      } catch {
        /* Ignore malformed account notifications. */
      }
    })
    source.addEventListener('chat.recalls', (event) => {
      try {
        const message = JSON.parse(event.data)
        if (message.account !== account) return
        void queryClient.invalidateQueries(
          { queryKey: ['wechat-recalls', account] },
          { cancelRefetch: false }
        )
      } catch {
        /* Invalid recall notifications cannot change account scope. */
      }
    })
    source.addEventListener('chat.history', (event) => {
      try {
        const message = JSON.parse(event.data)
        if (message.account !== account) return
        // Sync progress is not a message change. Media follows the affected
        // message revision, so unchanged images remain mounted and cached.
        queryClient.setQueryData<LiveStatus>(statusKey, (previous) =>
          previous?.account === account
            ? {
                ...previous,
                history_syncing: message.history_syncing,
                history_error: message.history_error,
                history_revision: message.history_revision,
                history_count: message.history_count,
                history_updated_at: message.history_updated_at,
              }
            : previous
        )
      } catch {
        /* Invalid notifications never change account scope. */
      }
    })
    source.addEventListener('chat.account_changed', () => {
      setConnected(false)
      // A bridge timeout used to arrive with the same event as an actual account
      // change. Confirm identity before clearing anything; SSE can then reconnect.
      void queryClient
        .fetchQuery({
          queryKey: statusKey,
          queryFn: ({ signal }) => chatApi.status(signal),
          staleTime: 0,
        })
        .then((status) => {
          if (
            status.bridge_connected &&
            status.account &&
            status.account !== account &&
            queryClient.getQueryData<LiveStatus>(statusKey)?.account !== account
          ) {
            closed = true
            clearTimeout(timer)
            clearTimeout(reconnectTimer)
            source.close()
          } else if (!closed && source.readyState === EventSource.CLOSED)
            reconnect()
        })
        .catch(reconnect)
    })
    return () => {
      closed = true
      clearTimeout(timer)
      clearTimeout(reconnectTimer)
      source.close()
    }
  }, [account, queryClient, attempt])
  return connected
}
