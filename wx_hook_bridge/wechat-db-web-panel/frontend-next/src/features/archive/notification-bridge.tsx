import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { chatApi } from '@/features/chats/api'
import { archiveApi, params, type NotificationPoll } from './api'

export function NotificationBridge() {
  const optIn = useQuery({
    queryKey: ['archive-notification-optin'],
    queryFn: ({ signal }) =>
      archiveApi<{ enabled: boolean }>('/notifications/enabled', signal),
    refetchInterval: 15000,
    retry: false,
  })
  const status = useQuery({
    queryKey: ['wechat-live-status-v2'],
    queryFn: ({ signal }) => chatApi.status(signal),
    refetchInterval: 5000,
    retry: 1,
    enabled: optIn.data?.enabled === true && !optIn.isError,
  })
  const account =
    optIn.data?.enabled &&
    !optIn.isError &&
    status.data?.bridge_connected &&
    !status.isError
      ? status.data.account
      : ''
  const config = useQuery({
    queryKey: ['archive-notification-config', account],
    queryFn: ({ signal }) =>
      archiveApi<NotificationPoll>(
        `/notifications?${params({ account: account! })}`,
        signal
      ),
    enabled: !!account,
    refetchInterval: 15000,
    retry: false,
  })
  const enabled = !!config.data?.config.enabled && !config.isError
  const revision = config.data?.config.revision
  const permission =
    typeof Notification !== 'undefined' && Notification.permission === 'granted'
  useEffect(() => {
    if (!account || !enabled || !permission) return
    const controller = new AbortController()
    let closed = false
    let cursor: number | undefined
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const result = await archiveApi<NotificationPoll>(
          `/notifications?${params({ account, ...(cursor === undefined ? {} : { after: cursor }) })}`,
          controller.signal
        )
        if (
          closed ||
          !result.config.enabled ||
          result.config.account !== account
        )
          return
        for (const event of result.events) {
          const key = `wechat-notification:${account}`
          let previous = 0
          try {
            previous = Number(localStorage.getItem(key) || 0)
          } catch {
            /* Storage is optional. */
          }
          if (event.sequence <= previous) continue
          try {
            localStorage.setItem(key, String(event.sequence))
          } catch {
            /* Notification tags still deduplicate. */
          }
          new Notification(event.title, {
            body: event.body,
            tag: `${account}:${event.sequence}`,
          })
        }
        cursor = result.cursor
      } catch {
        // A transient outage retries; the verified status query handles account changes.
      }
      if (!closed) timer = setTimeout(() => void poll(), 2500)
    }
    void poll()
    return () => {
      closed = true
      controller.abort()
      clearTimeout(timer)
    }
  }, [account, enabled, revision, permission])
  return null
}
