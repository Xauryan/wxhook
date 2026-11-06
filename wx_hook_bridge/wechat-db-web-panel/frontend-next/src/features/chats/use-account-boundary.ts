import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export function useAccountBoundary(account: string, verified: boolean) {
  const client = useQueryClient()
  const last = useRef('')
  useEffect(() => {
    if (!verified || !account || last.current === account) return
    const previous = last.current
    last.current = account
    if (!previous) return
    // Run at the parent account boundary, after the previous workspace unmounts.
    // Evicting inside its SSE callback lets old query observers rebuild the cache.
    const filters = [
      'wechat-flow',
      'wechat-roster-v2',
      'wechat-media',
      'wechat-contacts',
      'wechat-favorites',
      'wechat-favorite-media',
      'wechat-moments',
      'wechat-recalls',
      'wechat-relationships',
    ].map((prefix) => ({
      queryKey: [prefix, previous],
    }))
    void Promise.all(
      filters.map((filter) => client.cancelQueries(filter))
    ).then(() => {
      if (last.current !== previous)
        filters.forEach((filter) => client.removeQueries(filter))
    })
  }, [account, verified, client])
}
