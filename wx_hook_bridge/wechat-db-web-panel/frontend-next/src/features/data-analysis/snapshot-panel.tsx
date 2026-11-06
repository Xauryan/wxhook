import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { API_BASE } from './lib/api'

export type SnapshotJob = {
  id: string
  account: string
  status: string
  stage: string
  message: string
  file?: string
  completed_files: number
  total_files: number
  file_bytes?: number
  file_total_bytes?: number
  message_count?: number
  unresolved_count?: number
  unresolved_files?: string[]
  validation_warning_count?: number
  validation_warning_files?: string[]
  resource_files?: number
}
type Status = {
  capability: { available: boolean; account: string; reason: string }
  automation: {
    enabled: boolean
    account: string
    interval_minutes: number
    revision: number
  }
  job: SnapshotJob | null
}
const terminal = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])
const stages: Record<string, string> = {
  preflight: '核验账号和存储空间',
  keys: '核验密钥',
  copying: '复制数据库',
  decrypting: '解密与完整性检查',
  validating: '生成校验报告',
  indexing: '建立统计索引',
  publishing: '发布快照',
  complete: '生成完成',
  failed: '生成失败',
  cancelled: '已取消',
  interrupted: '任务中断',
}

async function request<T>(
  path = '',
  body?: unknown,
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(`${API_BASE}/analysis-snapshots${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    signal,
    headers:
      body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const value = await response.json()
  if (!response.ok)
    throw new Error(
      typeof value.detail === 'string' ? value.detail : '快照操作失败，请重试。'
    )
  return value as T
}

export function SnapshotPanel({ ready = false }: { ready?: boolean }) {
  const client = useQueryClient()
  const [interval, setInterval] = useState<number | null>(null)
  const [cancelledId, setCancelledId] = useState('')
  const observed = useRef('')
  const status = useQuery({
    queryKey: ['analysis-snapshots'],
    queryFn: ({ signal }) => request<Status>('', undefined, signal),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.job && !terminal.has(query.state.data.job.status)
        ? 1500
        : 10_000,
  })
  const data = status.data
  const job = data?.job
  const active = Boolean(job && !terminal.has(job.status))
  const account = data?.capability.account || ''
  const configuredInterval =
    interval ?? data?.automation.interval_minutes ?? 360
  const mutation = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) =>
      request(path, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['analysis-snapshots'] })
    },
  })
  useEffect(() => {
    if (job?.status === 'succeeded' && observed.current !== job.id) {
      observed.current = job.id
      void client.invalidateQueries({ queryKey: ['data-analysis'] })
    }
  }, [job?.id, job?.status, client])
  const saveAuto = (enabled: boolean) => {
    if (!data) return
    mutation.mutate({
      path: '/automation',
      body: {
        account: enabled ? account : data.automation.account,
        enabled,
        interval_minutes: configuredInterval,
        revision: data.automation.revision,
      },
    })
  }
  return (
    <section
      className='mb-6 space-y-4 rounded-xl border bg-card p-5'
      aria-label='解密快照管理'
    >
      <div className='flex flex-wrap items-start justify-between gap-3'>
        <div>
          <h2 className='flex items-center gap-2 text-lg font-semibold'>
            <Database size={20} />
            解密快照
          </h2>
          <p className='mt-1 text-sm text-muted-foreground'>
            从当前登录的微信生成本机数据库副本，校验完成后更新分析数据。
          </p>
        </div>
        <Button
          disabled={!data?.capability.available || active || mutation.isPending}
          onClick={() => mutation.mutate({ path: '', body: { account } })}
        >
          <RefreshCw className={active ? 'animate-spin' : ''} />
          {active ? '正在生成快照' : ready ? '一键更新快照' : '一键生成快照'}
        </Button>
      </div>
      {status.isPending ? (
        <p role='status' className='text-sm'>
          正在识别本机微信账号…
        </p>
      ) : status.isError ? (
        <div role='alert' className='text-sm'>
          快照服务未连接：{status.error.message}
          <Button variant='link' onClick={() => void status.refetch()}>
            重新检查
          </Button>
        </div>
      ) : (
        <p className='text-sm break-all'>
          {data?.capability.available
            ? `当前账号：${account}`
            : data?.capability.reason}
        </p>
      )}
      {job && (
        <div
          className='space-y-2 rounded-lg bg-muted/50 p-3'
          aria-live='polite'
        >
          <div className='flex flex-wrap items-center justify-between gap-2'>
            <strong className='text-sm'>
              {stages[job.stage] || job.stage}
            </strong>
            {active && (
              <Button
                size='sm'
                variant='outline'
                disabled={mutation.isPending || cancelledId === job.id}
                onClick={() =>
                  mutation.mutate(
                    { path: `/${job.id}/cancel`, body: {} },
                    {
                      onSuccess: () => setCancelledId(job.id),
                    }
                  )
                }
              >
                {cancelledId === job.id ? '正在取消…' : '取消任务'}
              </Button>
            )}
          </div>
          <p className='text-sm'>{job.message}</p>
          {job.account !== account && (
            <p className='text-sm text-muted-foreground'>
              此任务绑定账号：{job.account}
            </p>
          )}
          {active && job.total_files > 0 && (
            <>
              <progress
                aria-label={
                  job.stage === 'indexing' ? '统计索引生成中' : '数据库解密进度'
                }
                className='h-2 w-full'
                max={job.total_files}
                value={
                  job.stage === 'indexing' ? undefined : job.completed_files
                }
              />
              <p className='text-xs break-all text-muted-foreground'>
                数据库 {job.completed_files} / {job.total_files}
                {job.file ? ` · ${job.file}` : ''}
              </p>
              {job.stage === 'decrypting' && Boolean(job.file_total_bytes) && (
                <p className='text-xs text-muted-foreground'>
                  当前文件{' '}
                  {Math.min(
                    100,
                    Math.round(
                      ((job.file_bytes || 0) / job.file_total_bytes!) * 100
                    )
                  )}
                  %
                </p>
              )}
            </>
          )}
          {job.message_count !== undefined && (
            <p className='text-xs text-muted-foreground'>
              已解析 {job.message_count.toLocaleString()} 条消息
            </p>
          )}
          {job.resource_files !== undefined && (
            <p className='text-xs text-muted-foreground'>
              已扫描 {job.resource_files.toLocaleString()} 个本机媒体文件
            </p>
          )}
          {Boolean(job.unresolved_count) && (
            <details className='text-sm text-amber-700 dark:text-amber-400'>
              <summary>{job.unresolved_count} 个附属库未解密</summary>
              <ul className='mt-2 list-disc space-y-1 pl-5'>
                {job.unresolved_files?.map((file) => (
                  <li className='break-all' key={file}>
                    {file}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {Boolean(job?.validation_warning_count) && (
        <details className='text-sm text-amber-700 dark:text-amber-400'>
          <summary>
            {job?.validation_warning_count} 个库的原生分词器检查未完成
          </summary>
          <p className='mt-2'>
            逐页解密验证已完成；以下库的 SQLite
            完整性检查受原生分词器兼容性限制，未计为通过。
          </p>
          <ul className='mt-2 list-disc pl-5'>
            {job?.validation_warning_files?.map((file) => (
              <li className='break-all' key={file}>
                {file}
              </li>
            ))}
          </ul>
        </details>
      )}
      {data && (
        <div className='flex flex-wrap items-center gap-3 border-t pt-3 text-sm'>
          <label className='flex items-center gap-2'>
            <input
              type='checkbox'
              checked={data.automation.enabled}
              disabled={
                mutation.isPending ||
                (!data.capability.available && !data.automation.enabled)
              }
              onChange={(event) => saveAuto(event.target.checked)}
            />
            自动更新快照
          </label>
          <select
            aria-label='快照自动更新间隔'
            className='rounded-md border bg-background px-2 py-1'
            value={configuredInterval}
            disabled={mutation.isPending}
            onChange={(event) => setInterval(Number(event.target.value))}
          >
            <option value={60}>每小时</option>
            <option value={360}>每 6 小时</option>
            <option value={1440}>每天</option>
          </select>
          {data.automation.enabled &&
            interval !== null &&
            interval !== data.automation.interval_minutes && (
              <Button
                size='sm'
                variant='outline'
                disabled={mutation.isPending || !data.capability.available}
                onClick={() => saveAuto(true)}
              >
                保存间隔
              </Button>
            )}
          <p className='w-full text-xs text-muted-foreground'>
            {data.automation.enabled
              ? `已绑定 ${data.automation.account}；应用后台运行且该账号在线时执行，切换账号后等待。`
              : '开启后绑定当前账号；首次全量处理可能需要较长时间。'}
          </p>
        </div>
      )}
      {mutation.isError && (
        <p role='alert' className='text-sm text-destructive'>
          {mutation.error.message}
        </p>
      )}
    </section>
  )
}
