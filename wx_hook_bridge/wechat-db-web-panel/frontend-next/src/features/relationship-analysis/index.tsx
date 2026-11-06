import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, LoaderCircle, RefreshCw } from 'lucide-react'
import { useTheme } from '@/context/theme-provider'
import { Button } from '@/components/ui/button'
import { ConfigDrawer } from '@/components/config-drawer'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { base, chatApi } from '@/features/chats/api'
import { useAccountBoundary } from '@/features/chats/use-account-boundary'

async function readResponse(response: Response, contentType: string) {
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : `本地接口暂不可用（HTTP ${response.status}）`
    )
  }
  if (!response.headers.get('content-type')?.includes(contentType)) {
    throw new Error('关系分析接口返回了无法识别的结果。')
  }
  return response
}

export function RelationshipAnalysis() {
  const status = useQuery({
    queryKey: ['wechat-live-status-v2'],
    queryFn: ({ signal }) => chatApi.status(signal),
    refetchInterval: 5000,
    refetchOnWindowFocus: false,
    retry: 1,
  })
  const unavailable =
    status.isError || status.errorUpdatedAt > status.dataUpdatedAt
  const account = status.data?.account ?? ''
  const verified = !!account && !!status.data?.bridge_connected && !unavailable
  useAccountBoundary(account, verified)
  return (
    <>
      <Header>
        <Search className='me-auto' />
        <ThemeSwitch />
        <ConfigDrawer />
      </Header>
      <Main fluid className='space-y-4'>
        {verified ? (
          <AnalysisWorkspace key={account} account={account} />
        ) : (
          <div className='rounded-lg border p-8' role='status'>
            <h1 className='mb-2 text-2xl font-semibold'>关系分析</h1>
            <p className='text-muted-foreground'>
              {unavailable
                ? '本地服务未连接，请恢复服务后重新读取账号。'
                : status.isPending
                  ? '正在核验当前微信账号…'
                  : '微信未连接，请登录微信后重新读取账号。'}
            </p>
            <Button className='mt-4' onClick={() => void status.refetch()}>
              重新读取账号
            </Button>
          </div>
        )}
      </Main>
    </>
  )
}

function AnalysisWorkspace({ account }: { account: string }) {
  const { resolvedTheme } = useTheme()
  const [revision, setRevision] = useState(0)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  const [exportStage, setExportStage] = useState('')
  const [height, setHeight] = useState(1150)
  const iframe = useRef<HTMLIFrameElement>(null)
  const current = useRef(true)
  useEffect(() => {
    current.current = true
    return () => {
      current.current = false
    }
  }, [])
  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (
        event.source === iframe.current?.contentWindow &&
        event.data?.type === 'wechat-relationships-height' &&
        Number.isFinite(event.data.height)
      ) {
        setHeight(Math.max(600, Math.min(3500, event.data.height + 20)))
      }
    }
    window.addEventListener('message', listener)
    return () => window.removeEventListener('message', listener)
  }, [])
  const analysis = useQuery({
    queryKey: ['wechat-relationships', account, revision],
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ account, refresh: 'true' })
      const response = await readResponse(
        await fetch(`${base}/chat/relationships/view?${params}`, {
          signal,
          cache: 'no-store',
        }),
        'text/html'
      )
      return response.text()
    },
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })
  async function exportAll() {
    setExporting(true)
    setExportError('')
    setExportStage('正在生成导出文件…')
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 90_000)
    try {
      const params = new URLSearchParams({ account })
      const response = await readResponse(
        await fetch(`${base}/chat/relationships?${params}`, {
          cache: 'no-store',
          signal: controller.signal,
        }),
        'application/json'
      )
      const data = await response.json()
      if (!current.current) return
      if (data?.meta?.account !== account)
        throw new Error('导出账号核验失败，请重新读取账号。')
      const anchor = document.createElement('a')
      // Successful ZIP responses supply Content-Disposition after the server
      // rechecks the account. The browser owns the binary download.
      anchor.href = `${base}/chat/relationships/export?${params}`
      anchor.target = '_blank'
      anchor.rel = 'noopener'
      document.body.appendChild(anchor)
      setExporting(false)
      setExportStage('已发起下载，请在浏览器中完成保存。')
      // Let the ready state render before the browser opens its save dialog.
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => resolve())
      )
      if (!current.current) {
        anchor.remove()
        return
      }
      anchor.click()
      anchor.remove()
    } catch (error) {
      if (current.current)
        setExportError(
          controller.signal.aborted
            ? '导出等待超时，请恢复本地连接后重试。'
            : error instanceof Error
              ? error.message
              : '导出失败'
        )
    } finally {
      clearTimeout(timeout)
      if (current.current) setExporting(false)
    }
  }
  return (
    <section>
      <div className='mb-3 flex flex-wrap items-center justify-between gap-3'>
        <p className='text-sm text-muted-foreground'>
          当前微信账号的群、群友交集与跨群关系
        </p>
        <div className='flex gap-2'>
          <Button
            variant='outline'
            disabled={analysis.isFetching || exporting}
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw className='size-4' />
            重新分析
          </Button>
          <Button
            disabled={!analysis.data || analysis.isError || exporting}
            onClick={() => void exportAll()}
          >
            {exporting ? (
              <LoaderCircle className='size-4 animate-spin' />
            ) : (
              <Download className='size-4' />
            )}
            导出群与成员
          </Button>
        </div>
      </div>
      {exportError ? (
        <p role='alert' className='mb-3 text-sm text-destructive'>
          {exportError}
        </p>
      ) : null}
      {exportStage && !exportError ? (
        <p role='status' className='mb-3 text-sm text-muted-foreground'>
          {exportStage}
        </p>
      ) : null}
      {analysis.isPending ? (
        <div className='rounded-lg border p-10' role='status'>
          <LoaderCircle className='mb-3 size-5 animate-spin' />
          正在核验当前通讯录并计算群友交集…
        </div>
      ) : analysis.isError ? (
        <div role='alert' className='rounded-lg border p-8'>
          <h1 className='mb-2 text-2xl font-semibold'>关系分析</h1>
          <p className='text-destructive'>{analysis.error.message}</p>
          <Button className='mt-4' onClick={() => void analysis.refetch()}>
            重试分析
          </Button>
        </div>
      ) : (
        <iframe
          ref={iframe}
          title='关系分析：微信群关系图与群友交集'
          srcDoc={analysis.data.replace(
            'data-theme="system"',
            `data-theme="${resolvedTheme}"`
          )}
          sandbox='allow-scripts allow-downloads'
          className='w-full rounded-lg border'
          style={{ height }}
        />
      )}
    </section>
  )
}
