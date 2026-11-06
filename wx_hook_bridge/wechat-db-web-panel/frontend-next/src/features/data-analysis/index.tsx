import { lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { AlertCircle, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfigDrawer } from '@/components/config-drawer'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import './analysis.css'
import { api, fetchApi } from './lib/api'
import { profileName } from './lib/format'
import { AnalysisContext, useSearchParams } from './router'
import { SnapshotPanel } from './snapshot-panel'

const pages = {
  overview: lazy(() =>
    import('./pages/OverviewPage').then((m) => ({ default: m.OverviewPage }))
  ),
  contacts: lazy(() =>
    import('./pages/ContactsPage').then((m) => ({ default: m.ContactsPage }))
  ),
  media: lazy(() =>
    import('./pages/MediaPage').then((m) => ({ default: m.MediaPage }))
  ),
  contributions: lazy(() =>
    import('./pages/ContributionsPage').then((m) => ({
      default: m.ContributionsPage,
    }))
  ),
  conversations: lazy(() =>
    import('./pages/ConversationsPage').then((m) => ({
      default: m.ConversationsPage,
    }))
  ),
  'chat-list': lazy(() =>
    import('./pages/ChatListPage').then((m) => ({ default: m.ChatListPage }))
  ),
  search: lazy(() =>
    import('./pages/SearchPage').then((m) => ({ default: m.SearchPage }))
  ),
  settings: lazy(() =>
    import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage }))
  ),
}
const titles: Record<string, string> = {
  overview: '数据概览',
  contacts: '联系人分析',
  media: '媒体统计',
  contributions: '活跃热力图',
  conversations: '会话分析与导出',
  'chat-list': '会话统计',
  search: '全文搜索',
  settings: '数据库诊断',
}
type SnapshotHealth = {
  status: string
  configured?: boolean
  detail?: string
  account?: string
  snapshot_time?: string
  database_root?: string
  missing?: string[]
}

export function DataAnalysis({ section }: { section: string }) {
  const [params, setParams] = useSearchParams()
  const health = useQuery({
    queryKey: ['data-analysis', 'health'],
    queryFn: ({ signal }) =>
      fetchApi<SnapshotHealth>('/health', undefined, signal),
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      query.state.data?.status === 'ok' ? false : 10_000,
  })
  const ready =
    health.data?.status === 'ok' && health.data?.configured !== false
  const snapshotKey = `${health.data?.account}|${health.data?.snapshot_time}|${health.data?.database_root}`
  const profile = useQuery({
    queryKey: ['data-analysis', 'profile', snapshotKey],
    queryFn: ({ signal }) => api.profile(signal),
    enabled: ready,
    retry: false,
  })
  const Page = pages[section as keyof typeof pages]

  return (
    <>
      <Header>
        <Search className='me-auto' />
        <ThemeSwitch />
        <ConfigDrawer />
      </Header>
      <Main>
        <div className='mb-6 flex flex-wrap items-start justify-between gap-3'>
          <div>
            <h1 className='text-2xl font-bold tracking-tight'>
              {titles[section] || '数据分析'}
            </h1>
            <p className='mt-1 text-sm text-muted-foreground'>
              读取本机解密快照，统计范围以快照为准。
            </p>
          </div>
          <div className='flex items-center gap-2'>
            {section === 'overview' && (
              <select
                aria-label='分析时间范围'
                value={params.get('range') || '30'}
                className='rounded-md border bg-background px-3 py-2 text-sm'
                onChange={(event) => {
                  const next = new URLSearchParams(params)
                  next.set('range', event.target.value)
                  setParams(next)
                }}
              >
                <option value='30'>最近 30 天</option>
                <option value='90'>最近 90 天</option>
                <option value='all'>最近 366 天</option>
              </select>
            )}
            <Button
              variant='outline'
              disabled={health.isFetching}
              onClick={() => void health.refetch()}
            >
              <RefreshCw className={health.isFetching ? 'animate-spin' : ''} />
              核验快照
            </Button>
          </div>
        </div>
        {!health.isPending && !health.isError && (
          <SnapshotPanel ready={ready} />
        )}
        {!Page ? (
          <div role='alert'>
            该分析页面不存在。
            <Link to='/analysis/$section' params={{ section: 'overview' }}>
              返回数据概览
            </Link>
          </div>
        ) : health.isPending ? (
          <p role='status'>正在核验解密快照…</p>
        ) : health.isError ? (
          <div role='alert' className='rounded-lg border p-5'>
            <AlertCircle className='mb-2' />
            本地数据服务未连接：{health.error.message}
            <Button
              className='mt-3 block'
              variant='outline'
              onClick={() => void health.refetch()}
            >
              重新连接
            </Button>
          </div>
        ) : !ready ? (
          <section
            className='space-y-4 rounded-xl border p-6'
            aria-label='解密快照配置引导'
          >
            <h2 className='text-lg font-semibold'>尚未配置可用的解密快照</h2>
            <p className='text-sm text-muted-foreground'>
              登录微信后，点击上方「一键生成快照」。首份快照完成后即可查看分析数据。
            </p>
            {health.data?.detail &&
              health.data.detail !== 'No decrypted snapshot is configured' && (
                <details className='text-sm text-muted-foreground'>
                  <summary>查看诊断原因</summary>
                  <p className='mt-2 break-all'>{health.data.detail}</p>
                </details>
              )}
            <p className='text-sm'>
              没有可用快照时，统计页面不会显示示例数据。聊天、任务和自动化可继续从侧栏进入。
            </p>
          </section>
        ) : (
          <AnalysisContext.Provider
            key={snapshotKey}
            value={{
              snapshotKey,
              profile: profile.data?.profile,
              profileResponse: profile.data,
              profileName: profileName(profile.data?.profile),
            }}
          >
            <div className='data-analysis'>
              <Suspense fallback={<p role='status'>正在加载分析页面…</p>}>
                <Page />
              </Suspense>
            </div>
          </AnalysisContext.Provider>
        )}
      </Main>
    </>
  )
}
