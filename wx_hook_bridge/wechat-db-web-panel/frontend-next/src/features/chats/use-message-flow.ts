import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { chatApi, type Changes, type Message, type SessionDetail } from './api'
import { mergeMessages } from './message-flow'

export function useMessageFlow(account: string, username: string) {
  const client = useQueryClient()
  const key = useMemo(
    () => ['wechat-flow', account, username] as const,
    [account, username]
  )
  const requests = useRef({
    generation: 0,
    older: false,
    controllers: new Set<AbortController>(),
  })
  const [pageState, setPageState] = useState({
    scope: key,
    loading: false,
    error: null as Error | null,
  })
  useEffect(
    () => () => {
      requests.current.generation++
      requests.current.older = false
      requests.current.controllers.forEach((controller) => controller.abort())
    },
    [key]
  )
  const updatePage = useCallback(
    (loading: boolean, error: Error | null = null) =>
      setPageState({ scope: key, loading, error }),
    [key]
  )
  // QueryClient is only a cache accessor, not part of the remote identity.
  // eslint-disable-next-line @tanstack/query/exhaustive-deps
  const query = useQuery({
    queryKey: key,
    enabled: !!account && !!username,
    staleTime: Infinity,
    gcTime: 30 * 60_000,
    refetchOnWindowFocus: false,
    queryFn: async ({ signal }) => {
      const cached = client.getQueryData<SessionDetail>(key)
      if (!cached) {
        const page = await chatApi.detail(account, username, null, signal)
        if (page.account !== account) throw new Error('微信账号已切换')
        // A recall jump can populate this key while the first-page request is
        // pending. Keep that explicit context instead of replacing it with latest.
        return client.getQueryData<SessionDetail>(key) ?? page
      }
      // First entry reads one page. All subsequent refreshes consume only the
      // durable delta cursor; cached history is never downloaded again.
      let revision = cached.revision
      let incoming: Message[] = []
      const deleted: string[] = []
      let delta: Changes
      let pages = 0
      do {
        delta = await chatApi.changes(
          account,
          username,
          revision,
          signal,
          cached.messages[0]?.id
        )
        if (delta.account !== account) throw new Error('微信账号已切换')
        if (delta.has_more && delta.revision <= revision)
          throw new Error('消息增量游标未推进，请重试')
        incoming = mergeMessages(incoming, delta.messages)
        deleted.push(...delta.deleted)
        revision = delta.revision
        if (++pages >= 50 && delta.has_more)
          throw new Error('消息增量过多，请回到最新消息后重试')
      } while (delta.has_more)
      const current = client.getQueryData<SessionDetail>(key) ?? cached
      const removed = [...new Set([...(current.removed_ids ?? []), ...deleted])]
      const floor = current.messages[0]
      const ceiling = current.detached
        ? current.messages[current.messages.length - 1]
        : undefined
      const loadedIds = new Set(current.messages.map((item) => item.id))
      const visible = incoming.filter(
        (item) =>
          loadedIds.has(item.id) ||
          ((!floor ||
            item.order_time > floor.order_time ||
            (item.order_time === floor.order_time &&
              Number(item.id) >= Number(floor.id))) &&
            (!ceiling ||
              item.order_time < ceiling.order_time ||
              (item.order_time === ceiling.order_time &&
                Number(item.id) <= Number(ceiling.id))))
      )
      const boundary =
        current.messages[0]?.id === cached.messages[0]?.id &&
        !removed.includes(cached.messages[0]?.id ?? '') &&
        delta.boundary_cursor !== undefined
          ? delta.boundary_cursor
          : current.next_cursor
      return {
        ...current,
        session: delta.session ?? current.session,
        elapsed_ms: delta.elapsed_ms ?? current.elapsed_ms,
        boundary_cursor: boundary,
        next_cursor: boundary,
        has_more: !!boundary,
        revision: Math.max(revision, current.revision),
        removed_ids: removed,
        messages: mergeMessages(current.messages, visible, removed),
      }
    },
  })

  async function loadOlder() {
    const current = client.getQueryData<SessionDetail>(key)
    if (!current?.next_cursor || requests.current.older) return
    requests.current.older = true
    updatePage(true)
    const version = requests.current.generation
    const controller = new AbortController()
    requests.current.controllers.add(controller)
    try {
      const older = await chatApi.detail(
        account,
        username,
        current.next_cursor,
        controller.signal
      )
      if (version !== requests.current.generation || controller.signal.aborted)
        return
      if (older.account !== account) throw new Error('微信账号已切换')
      client.setQueryData<SessionDetail>(key, (previous) =>
        previous
          ? {
              ...previous,
              next_cursor: older.next_cursor,
              has_more: older.has_more,
              elapsed_ms: older.elapsed_ms,
              messages: mergeMessages(
                previous.messages,
                older.messages,
                previous.removed_ids
              ),
            }
          : older
      )
    } catch (error) {
      if (version === requests.current.generation && !controller.signal.aborted)
        updatePage(
          false,
          error instanceof Error ? error : new Error('历史加载失败')
        )
    } finally {
      requests.current.controllers.delete(controller)
      if (version === requests.current.generation)
        requests.current.older = false
      if (version === requests.current.generation)
        setPageState((previous) =>
          previous.scope === key ? { ...previous, loading: false } : previous
        )
    }
  }

  const locate = useCallback(
    async (server?: string, messageId?: string) => {
      if (!server && !messageId) return
      const existing = client
        .getQueryData<SessionDetail>(key)
        ?.messages.find((message) =>
          server ? message.server_id === server : message.id === messageId
        )
      if (existing) return existing.id
      const version = ++requests.current.generation
      requests.current.controllers.forEach((controller) => controller.abort())
      await client.cancelQueries({ queryKey: key })
      updatePage(false)
      const controller = new AbortController()
      requests.current.controllers.add(controller)
      try {
        const target = await chatApi.detail(
          account,
          username,
          null,
          controller.signal,
          server,
          undefined,
          server ? undefined : messageId
        )
        if (
          version !== requests.current.generation ||
          controller.signal.aborted
        )
          return
        if (target.account !== account) throw new Error('微信账号已切换')
        client.setQueryData<SessionDetail>(key, { ...target, detached: true })
        return target.messages.find((item) =>
          server ? item.server_id === server : item.id === messageId
        )?.id
      } catch (error) {
        if (
          version === requests.current.generation &&
          !controller.signal.aborted
        )
          updatePage(
            false,
            error instanceof Error ? error : new Error('未找到原消息')
          )
      } finally {
        requests.current.controllers.delete(controller)
      }
    },
    [account, username, client, key, updatePage]
  )

  async function latest() {
    const version = ++requests.current.generation
    requests.current.controllers.forEach((controller) => controller.abort())
    await client.cancelQueries({ queryKey: key })
    const controller = new AbortController()
    requests.current.controllers.add(controller)
    try {
      const page = await chatApi.detail(
        account,
        username,
        null,
        controller.signal
      )
      if (version !== requests.current.generation || controller.signal.aborted)
        return
      if (page.account !== account) throw new Error('微信账号已切换')
      client.setQueryData(key, page)
      updatePage(false)
    } finally {
      requests.current.controllers.delete(controller)
    }
  }
  return {
    data: query.data,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
    isFetching: query.isFetching,
    refetch: query.refetch,
    loadingOlder: pageState.scope === key && pageState.loading,
    pageError: pageState.scope === key ? pageState.error : null,
    loadOlder,
    locate,
    latest,
  }
}
