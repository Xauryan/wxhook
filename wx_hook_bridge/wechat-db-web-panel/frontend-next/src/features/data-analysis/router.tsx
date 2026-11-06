import { createContext, useContext } from 'react'
import {
  useLocation,
  useNavigate as useTanstackNavigate,
} from '@tanstack/react-router'
import type { ShellContext } from './components/AppShell'

export const AnalysisContext = createContext<ShellContext>({
  profileName: '本地账号',
})

export function useOutletContext<T>() {
  return useContext(AnalysisContext) as T
}

export function useNavigate() {
  const navigate = useTanstackNavigate()
  return (address: string) => {
    const url = new URL(address, window.location.origin)
    const section = url.pathname.split('/').filter(Boolean).pop() || 'overview'
    return navigate({
      to: '/analysis/$section',
      params: { section },
      search: Object.fromEntries(url.searchParams),
    })
  }
}

export function useSearchParams(): [
  URLSearchParams,
  (params: URLSearchParams) => void,
] {
  const location = useLocation()
  const navigate = useNavigate()
  return [
    new URLSearchParams(location.searchStr),
    (params) => void navigate(`${location.pathname}?${params.toString()}`),
  ]
}
