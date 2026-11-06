import { useContext } from 'react'
import {
  useQuery,
  type DefaultError,
  type QueryKey,
  type UseQueryOptions,
} from '@tanstack/react-query'
import { AnalysisContext } from './router'

// Every cached result belongs to one account and one published snapshot.
export function useAnalysisQuery<
  TQueryFnData = unknown,
  TError = DefaultError,
  TData = TQueryFnData,
  TQueryKey extends QueryKey = QueryKey,
>(options: UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>) {
  const { snapshotKey } = useContext(AnalysisContext)
  return useQuery({
    ...options,
    queryKey: [
      'data-analysis',
      snapshotKey,
      ...options.queryKey,
    ] as unknown as TQueryKey,
    refetchOnWindowFocus: false,
  })
}
