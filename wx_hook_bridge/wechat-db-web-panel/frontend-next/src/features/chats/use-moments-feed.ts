import { useMemo } from 'react'
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import type { LiveStatus } from './api'
import { momentsApi, MomentsApiError, type MomentList } from './moments-api'

export function useMomentsFeed(
  live: LiveStatus,
  q: string,
  scope: 'all' | 'self'
) {
  const client = useQueryClient()
  const head = useQuery({
    queryKey: ['wechat-moments', live.account, q, scope, 'head'],
    queryFn: ({ signal }) =>
      momentsApi.list(live.account, q, scope, null, signal),
    enabled: !!live.account && live.bridge_connected,
    staleTime: 30_000,
    gcTime: 30 * 60_000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      live.bridge_connected && query.state.data?.syncing ? 400 : false,
  })
  // Refresh only the head. Unchanged revisions keep all previously read pages.
  // A new snapshot gets new cursors, so older pages cannot cross that boundary.
  const older = useInfiniteQuery({
    queryKey: [
      'wechat-moments',
      live.account,
      q,
      scope,
      'older',
      head.data?.revision ?? 'unversioned',
    ],
    queryFn: ({ signal, pageParam }) =>
      momentsApi.list(live.account, q, scope, pageParam, signal),
    enabled: false,
    initialPageParam: head.data?.next_cursor ?? null,
    getNextPageParam: (page, _pages, lastCursor, cursors) =>
      page.has_more &&
      page.next_cursor &&
      page.next_cursor !== lastCursor &&
      !cursors.includes(page.next_cursor)
        ? page.next_cursor
        : undefined,
    staleTime: Infinity,
    gcTime: 30 * 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const refresh = useMutation({
    mutationFn: () => momentsApi.refresh(live.account),
    onSuccess: async (result) => {
      const filter = {
        queryKey: ['wechat-moments', live.account],
        predicate: (query: { queryKey: readonly unknown[] }) =>
          query.queryKey[query.queryKey.length - 1] === 'head',
      }
      client.setQueriesData<MomentList>(filter, (previous) =>
        previous
          ? { ...previous, syncing: result.syncing, error: result.error }
          : previous
      )
      await client.invalidateQueries(filter)
    },
  })
  const items = useMemo(
    () => [
      ...new Map(
        [
          ...(head.data?.items ?? []),
          ...(older.data?.pages.flatMap((page) => page.items) ?? []),
        ].map((item) => [item.id, item])
      ).values(),
    ],
    [head.data?.items, older.data]
  )
  const hasNextPage =
    !!head.data?.ready &&
    (older.data
      ? older.hasNextPage
      : !!head.data?.has_more && !!head.data.next_cursor)
  return {
    data: head.data,
    items,
    refresh,
    list: {
      error: head.error ?? older.error,
      isPending: head.isPending,
      isFetching: head.isFetching || older.isFetching,
      isFetchingNextPage: older.isFetchingNextPage,
      isFetchNextPageError: older.isFetchNextPageError,
      hasNextPage,
      refetch: async () => {
        if (
          older.error instanceof MomentsApiError &&
          older.error.status === 409
        ) {
          await client.resetQueries({
            queryKey: [
              'wechat-moments',
              live.account,
              q,
              scope,
              'older',
              head.data?.revision ?? 'unversioned',
            ],
            exact: true,
          })
        }
        return head.refetch()
      },
      fetchNextPage: () =>
        hasNextPage
          ? older.fetchNextPage({ cancelRefetch: false })
          : Promise.resolve(),
    },
  }
}
