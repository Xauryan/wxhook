import { useDeferredValue, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, LoaderCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { base, chatApi } from '@/features/chats/api'
import {
  archiveApi,
  categoryName,
  params,
  saveFile,
  type Automation,
  type Catalog,
  type Count,
  type ExportJob,
  type ExportTemplate,
  type NotificationConfig,
  type NotificationPoll,
  type PreviewMessage,
  type Report,
  type Scope,
} from './api'
import { reportHtml } from './report-export'

const selectClass = 'h-10 w-full rounded-md border bg-background px-3 text-sm'
const titles: Record<string, string> = {
  reports: '报告中心',
  exports: '导出中心',
  notifications: '本机提醒',
}
const number = (value: number) => value.toLocaleString('zh-CN')

function ErrorBox({ error }: { error: Error | null }) {
  return error ? (
    <p
      role='alert'
      className='rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive'
    >
      {error.message}
    </p>
  ) : null
}

export default function ArchiveWorkspace({ section }: { section: string }) {
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const search = useDeferredValue(q)
  const catalog = useQuery({
    queryKey: ['archive-catalog', search, offset],
    queryFn: ({ signal }) =>
      archiveApi<Catalog>(`/catalog?${params({ q: search, offset })}`, signal),
    refetchInterval: 30000,
    retry: 1,
  })
  return (
    <>
      <Header>
        <Search className='me-auto' />
        <ThemeSwitch />
      </Header>
      <Main fluid className='space-y-5 pb-10'>
        <div>
          <h1 className='text-2xl font-semibold'>{titles[section]}</h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            本机处理 · 保留原始记录 · 基于已解密快照
          </p>
        </div>
        {section === 'notifications' ? (
          <Notifications />
        ) : (
          <>
            <ErrorBox error={catalog.error} />
            {catalog.isPending && (
              <p role='status' className='flex items-center gap-2'>
                <LoaderCircle className='size-4 animate-spin' />
                正在读取快照…
              </p>
            )}
            {catalog.data && !catalog.isError && (
              <>
                <p className='rounded-md bg-muted p-3 text-xs break-all'>
                  账号 {catalog.data.account} · 快照{' '}
                  {catalog.data.snapshot_time
                    ? new Date(catalog.data.snapshot_time).toLocaleString(
                        'zh-CN',
                        { hour12: false }
                      )
                    : '未知'}
                </p>
                <div className='flex flex-wrap items-end gap-3'>
                  <label className='min-w-0 flex-1 text-sm'>
                    搜索会话
                    <Input
                      value={q}
                      onChange={(e) => {
                        setQ(e.target.value)
                        setOffset(0)
                      }}
                      placeholder='名称或微信 ID'
                    />
                  </label>
                  <Button
                    variant='outline'
                    disabled={!offset || catalog.isFetching}
                    onClick={() => setOffset(Math.max(0, offset - 100))}
                  >
                    上一页
                  </Button>
                  <Button
                    variant='outline'
                    disabled={
                      offset + 100 >= catalog.data.total_sessions ||
                      catalog.isFetching
                    }
                    onClick={() => setOffset(offset + 100)}
                  >
                    下一页
                  </Button>
                  <span className='text-xs text-muted-foreground'>
                    共 {number(catalog.data.total_sessions)} 个会话
                  </span>
                </div>
                {section === 'reports' ? (
                  <Reports key={catalog.data.scope} catalog={catalog.data} />
                ) : (
                  <Exports key={catalog.data.scope} catalog={catalog.data} />
                )}
              </>
            )}
          </>
        )}
      </Main>
    </>
  )
}

function Bars({ rows, label }: { rows: Count[]; label: string }) {
  const max = Math.max(1, ...rows.map((r) => r.count))
  return (
    <div role='list' aria-label={label} className='space-y-2'>
      {rows.length ? (
        rows.map((row) => (
          <div
            key={row.label}
            role='listitem'
            className='grid grid-cols-[5rem_1fr_4.5rem] items-center gap-2 text-xs'
          >
            <span className='truncate' title={String(row.label)}>
              {row.label}
            </span>
            <div className='h-3 rounded-sm bg-muted'>
              <div
                className='h-full rounded-sm bg-primary/75'
                style={{ width: `${(100 * row.count) / max}%` }}
              />
            </div>
            <span className='text-right tabular-nums'>{number(row.count)}</span>
          </div>
        ))
      ) : (
        <p className='text-sm text-muted-foreground'>当前范围没有记录</p>
      )}
    </div>
  )
}

export function Reports({ catalog }: { catalog: Catalog }) {
  const [kind, setKind] = useState('annual')
  const [year, setYear] = useState(catalog.years[0] || 0)
  const [session, setSession] = useState('')
  const options = catalog.sessions.filter(
    (s) => Boolean(s.is_group) === (kind === 'group')
  )
  const selected = options.some((s) => s.username === session) ? session : ''
  const needsSession = kind === 'dual' || kind === 'group'
  const report = useQuery({
    queryKey: [
      'archive-report',
      catalog.account,
      catalog.scope,
      kind,
      year,
      selected,
    ],
    queryFn: ({ signal }) =>
      archiveApi<Report>(
        `/report?${params({ account: catalog.account, scope: catalog.scope, kind, year, session: selected })}`,
        signal
      ),
    enabled: !needsSession || !!selected,
    retry: false,
  })
  const data = report.data
  const exportReport = (format: 'json' | 'html') => {
    if (!data || report.isError) return
    if (format === 'json')
      saveFile(
        `微信报告-${kind}-${year || '全部'}.json`,
        JSON.stringify(data, null, 2),
        'application/json'
      )
    else
      saveFile(
        `微信报告-${kind}-${year || '全部'}.html`,
        reportHtml(data),
        'text/html'
      )
  }
  return (
    <div className='space-y-5'>
      <div className='grid gap-3 sm:grid-cols-3'>
        <label className='text-sm'>
          报告类型
          <select
            className={selectClass}
            value={kind}
            onChange={(e) => {
              setKind(e.target.value)
              setSession('')
            }}
          >
            <option value='annual'>年度 / 长期报告</option>
            <option value='dual'>双人报告</option>
            <option value='group'>群聊画像</option>
            <option value='footprint'>个人足迹</option>
          </select>
        </label>
        <label className='text-sm'>
          统计年份
          <select
            className={selectClass}
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
          >
            <option value={0}>全部年份</option>
            {catalog.years.map((y) => (
              <option key={y} value={y}>
                {y} 年
              </option>
            ))}
          </select>
        </label>
        {needsSession && (
          <label className='text-sm'>
            选择{kind === 'group' ? '群聊' : '联系人'}
            <select
              className={selectClass}
              value={selected}
              onChange={(e) => setSession(e.target.value)}
            >
              <option value=''>请选择，可用上方搜索定位</option>
              {options.map((s) => (
                <option key={s.username} value={s.username}>
                  {s.display_name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <ErrorBox error={report.error} />
      {report.isError && (
        <Button variant='outline' onClick={() => void report.refetch()}>
          重新统计
        </Button>
      )}
      {needsSession && !selected ? (
        <p
          role='status'
          className='rounded-md border p-6 text-muted-foreground'
        >
          选择一个会话后生成报告。
        </p>
      ) : report.isPending ? (
        <p role='status'>正在统计本地数据，大量记录可能需要数秒…</p>
      ) : (
        data &&
        !report.isError && (
          <>
            <div className='flex flex-wrap items-center justify-between gap-3'>
              <h2 className='text-lg font-medium'>
                {data.session?.display_name ||
                  (kind === 'footprint' ? '我发出的消息' : '聊天记录')}{' '}
                · {year || '全部年份'}
              </h2>
              <div className='flex gap-2'>
                <Button
                  size='sm'
                  variant='outline'
                  onClick={() => exportReport('json')}
                >
                  <Download className='size-4' />
                  JSON 报告
                </Button>
                <Button
                  size='sm'
                  variant='outline'
                  onClick={() => exportReport('html')}
                >
                  HTML 报告
                </Button>
              </div>
            </div>
            <div className='grid grid-cols-2 gap-3 xl:grid-cols-4'>
              {[
                ['消息总数', data.totals.message_count],
                [
                  '我发送 / 收到',
                  `${number(data.totals.sent_count)} / ${number(data.totals.received_count)}`,
                ],
                ['活跃天数', data.totals.active_days],
                ['文本字符数', data.totals.text_characters],
              ].map(([label, value]) => (
                <Card key={String(label)}>
                  <CardHeader className='pb-2'>
                    <CardTitle className='text-sm font-normal text-muted-foreground'>
                      {label}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className='text-xl font-semibold break-words tabular-nums'>
                    {typeof value === 'number' ? number(value) : value}
                  </CardContent>
                </Card>
              ))}
            </div>
            <div className='grid gap-4 lg:grid-cols-2'>
              <Card>
                <CardHeader>
                  <CardTitle>
                    月度趋势{data.monthly.length > 60 ? '（最近 60 个月）' : ''}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Bars rows={data.monthly.slice(-60)} label='各月消息数量' />
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>时间与记录</CardTitle>
                </CardHeader>
                <CardContent className='space-y-4 text-sm'>
                  <p>
                    留存范围：{data.totals.first_date || '无记录'} —{' '}
                    {data.totals.last_date || '无记录'}
                  </p>
                  <p>
                    消息最多的一天：
                    {data.peak_day
                      ? `${data.peak_day.label} · ${number(data.peak_day.count)} 条`
                      : '无记录'}
                  </p>
                  <p>
                    最长连续活跃：{data.longest_streak.days} 天{' '}
                    {data.longest_streak.start &&
                      `（${data.longest_streak.start} — ${data.longest_streak.end}）`}
                  </p>
                  <p>
                    {kind === 'group'
                      ? `可识别发言人数：${number(data.totals.observed_speakers)}（非群成员总数）`
                      : `有记录的会话：${number(data.totals.session_count)}`}
                  </p>
                  <h3 className='font-medium'>消息类型</h3>
                  <Bars
                    rows={data.categories.map((row) => ({
                      ...row,
                      label: categoryName(String(row.label)),
                    }))}
                    label='消息类型分布'
                  />
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>24 小时分布</CardTitle>
                </CardHeader>
                <CardContent>
                  <Bars
                    rows={Array.from({ length: 24 }, (_, hour) => ({
                      label: `${hour}:00`,
                      count:
                        data.hourly.find((r) => Number(r.label) === hour)
                          ?.count || 0,
                    }))}
                    label='各小时消息数量'
                  />
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>
                    {kind === 'group' ? '发言排行' : '会话排行'}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Bars
                    rows={(kind === 'group' ? data.speakers : data.ranking).map(
                      (r) => ({ label: r.name, count: r.count })
                    )}
                    label='消息活跃排行'
                  />
                </CardContent>
              </Card>
            </div>
            <Card>
              <CardHeader>
                <CardTitle>高频短句</CardTitle>
              </CardHeader>
              <CardContent>
                <p className='mb-3 text-xs text-muted-foreground'>
                  最近符合长度条件的 {number(data.phrase_sample_count)}{' '}
                  条文本中的重复短句，最多采样 20000 条。
                </p>
                <div className='flex flex-wrap gap-2'>
                  {data.phrases.length ? (
                    data.phrases.map((r) => (
                      <span
                        key={r.text}
                        className='max-w-full rounded-md bg-muted px-3 py-2 text-sm break-words'
                      >
                        {r.text}{' '}
                        <span className='text-muted-foreground'>
                          ×{r.count}
                        </span>
                      </span>
                    ))
                  ) : (
                    <p className='text-sm text-muted-foreground'>
                      没有重复短句
                    </p>
                  )}
                </div>
              </CardContent>
            </Card>
            {!!data.first_messages.length && (
              <Card>
                <CardHeader>
                  <CardTitle>本地最早留存的记录</CardTitle>
                </CardHeader>
                <CardContent className='space-y-3'>
                  {data.first_messages.map((m) => (
                    <p key={m.message_key} className='text-sm break-words'>
                      <span className='text-muted-foreground'>
                        {m.local_date} · {m.sender_name}{' '}
                      </span>
                      {m.summary}
                    </p>
                  ))}
                </CardContent>
              </Card>
            )}
            <div className='space-y-1 text-xs text-muted-foreground'>
              {data.notes.map((note) => (
                <p key={note}>{note}</p>
              ))}
            </div>
          </>
        )
      )}
    </div>
  )
}

export function Exports({ catalog }: { catalog: Catalog }) {
  const client = useQueryClient()
  const scope = { account: catalog.account, scope: catalog.scope }
  const query = params(scope)
  const [source, setSource] = useState('messages')
  const [format, setFormat] = useState('json')
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [all, setAll] = useState(false)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [direction, setDirection] = useState('all')
  const [contactKind, setContactKind] = useState('all')
  const [edits, setEdits] = useState(false)
  const [interval, setIntervalMinutes] = useState(1440)
  const template = (): ExportTemplate => ({
    ...scope,
    request_id: crypto.randomUUID().replace(/-/g, ''),
    source,
    format,
    sessions: Object.keys(selected),
    all_sessions: all,
    start_date: start,
    end_date: end,
    direction,
    contact_kind: contactKind,
    use_local_edits: edits,
  })
  const jobs = useQuery({
    queryKey: ['archive-jobs', catalog.scope, query],
    queryFn: ({ signal }) =>
      archiveApi<{ jobs: ExportJob[] }>(`/exports?${query}`, signal),
    refetchInterval: 2000,
  })
  const automation = useQuery({
    queryKey: ['archive-automation', catalog.scope, query],
    queryFn: ({ signal }) =>
      archiveApi<Automation>(`/exports/automation?${query}`, signal),
  })
  const create = useMutation({
    mutationFn: () => archiveApi<ExportJob>('/exports', undefined, template()),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ['archive-jobs', catalog.scope] }),
  })
  const cancel = useMutation({
    mutationFn: (id: string) =>
      archiveApi(`/exports/${id}/cancel?${query}`, undefined, {}),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ['archive-jobs', catalog.scope] }),
  })
  const auto = useMutation({
    mutationFn: (enabled: boolean) =>
      archiveApi<Automation>('/exports/automation', undefined, {
        revision: automation.data?.revision,
        enabled,
        interval_minutes: interval,
        template: enabled
          ? template()
          : { ...automation.data?.template, ...scope },
      }),
    onSuccess: (value) =>
      client.setQueryData(['archive-automation', catalog.scope, query], value),
  })
  const valid = source === 'contacts' || all || Object.keys(selected).length > 0
  const busy = jobs.data?.jobs.some((job) => job.status === 'running')
  const formats: Record<string, string> = {
    json: 'JSON',
    csv: 'CSV',
    html: 'HTML',
    txt: 'TXT',
    markdown: 'Markdown',
    xlsx: 'Excel (.xlsx)',
    pgsql: 'PostgreSQL 脚本',
    chatlab: 'ChatLab JSON',
    'chatlab-jsonl': 'ChatLab JSONL（大记录）',
  }
  return (
    <div className='space-y-4'>
      <Card>
        <CardHeader>
          <CardTitle>创建导出</CardTitle>
        </CardHeader>
        <CardContent className='space-y-4'>
          <div className='grid gap-3 sm:grid-cols-3'>
            <label className='text-sm'>
              导出内容
              <select
                className={selectClass}
                value={source}
                onChange={(e) => {
                  setSource(e.target.value)
                  if (format.startsWith('chatlab')) setFormat('json')
                }}
              >
                <option value='messages'>聊天记录</option>
                <option value='contacts'>联系人名录</option>
              </select>
            </label>
            <label className='text-sm'>
              文件格式
              <select
                className={selectClass}
                value={format}
                onChange={(e) => setFormat(e.target.value)}
              >
                {Object.entries(formats)
                  .filter(
                    ([key]) =>
                      source !== 'contacts' || !key.startsWith('chatlab')
                  )
                  .map(([key, name]) => (
                    <option key={key} value={key}>
                      {name}
                    </option>
                  ))}
              </select>
            </label>
            {source === 'contacts' ? (
              <label className='text-sm'>
                联系人范围
                <select
                  className={selectClass}
                  value={contactKind}
                  onChange={(e) => setContactKind(e.target.value)}
                >
                  <option value='all'>全部快照联系人</option>
                  <option value='friends'>快照通讯录好友</option>
                  <option value='groups'>群聊</option>
                  <option value='official'>公众号</option>
                  <option value='history'>历史联系人候选</option>
                </select>
              </label>
            ) : (
              <label className='text-sm'>
                消息方向
                <select
                  className={selectClass}
                  value={direction}
                  onChange={(e) => setDirection(e.target.value)}
                >
                  <option value='all'>全部</option>
                  <option value='sent'>我发送的</option>
                  <option value='received'>我收到的</option>
                </select>
              </label>
            )}
          </div>
          {source === 'messages' ? (
            <>
              <div className='grid gap-3 sm:grid-cols-2'>
                <label className='text-sm'>
                  开始日期（含）
                  <Input
                    type='date'
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label className='text-sm'>
                  结束日期（含）
                  <Input
                    type='date'
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </label>
              </div>
              <label className='flex items-center gap-2 text-sm'>
                <input
                  type='checkbox'
                  checked={all}
                  onChange={(e) => setAll(e.target.checked)}
                />
                导出全部会话（记录较多时耗时较长）
              </label>
              {!all && (
                <>
                  <div className='max-h-64 space-y-1 overflow-y-auto rounded-md border p-2'>
                    {catalog.sessions.map((session) => (
                      <label
                        key={session.username}
                        className='flex min-w-0 items-center gap-2 rounded px-2 py-2 text-sm hover:bg-muted'
                      >
                        <input
                          type='checkbox'
                          aria-label={`选择会话：${session.display_name}`}
                          checked={session.username in selected}
                          disabled={
                            !(session.username in selected) &&
                            Object.keys(selected).length >= 50
                          }
                          onChange={(e) =>
                            setSelected((current) => {
                              const next = { ...current }
                              if (e.target.checked)
                                next[session.username] = session.display_name
                              else delete next[session.username]
                              return next
                            })
                          }
                        />
                        <span
                          className='min-w-0 flex-1 truncate'
                          title={session.username}
                        >
                          {session.display_name}
                        </span>
                        <span className='text-xs text-muted-foreground'>
                          {number(session.message_count)} 条
                        </span>
                      </label>
                    ))}
                  </div>
                  <p className='text-xs text-muted-foreground'>
                    已选 {Object.keys(selected).length}/50 个 ·
                    搜索和翻页保留选择
                  </p>
                  <div className='flex flex-wrap gap-2'>
                    {Object.entries(selected).map(([id, name]) => (
                      <button
                        className='rounded-md border px-2 py-1 text-xs'
                        key={id}
                        onClick={() =>
                          setSelected((current) => {
                            const next = { ...current }
                            delete next[id]
                            return next
                          })
                        }
                      >
                        {name} ×
                      </button>
                    ))}
                  </div>
                </>
              )}
              <label className='flex items-center gap-2 text-sm'>
                <input
                  type='checkbox'
                  checked={edits}
                  onChange={(e) => setEdits(e.target.checked)}
                />
                使用本地修订副本（额外保留原文与备注）
              </label>
            </>
          ) : (
            <p className='text-sm text-muted-foreground'>
              分类依据快照标记；历史联系人候选不代表已删好友，也不代表当前微信通讯录。
            </p>
          )}
          <p className='text-xs text-muted-foreground'>
            导出 ZIP
            内含来源清单和校验值。媒体导出文件名与链接，不包含媒体文件。原始数据库保持只读。
          </p>
          <ErrorBox error={create.error} />
          <Button
            disabled={
              !valid ||
              busy ||
              create.isPending ||
              (!!start && !!end && start > end)
            }
            onClick={() => create.mutate()}
          >
            {create.isPending ? '正在创建…' : '创建后台导出'}
          </Button>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>自动导出</CardTitle>
        </CardHeader>
        <CardContent className='space-y-3'>
          <p className='text-sm text-muted-foreground'>
            {automation.data?.enabled
              ? `已启用，每 ${automation.data.interval_minutes / 60} 小时按已保存规则导出。`
              : '默认关闭。启用后保存当前选择，只在绑定的微信账号连接时执行，并跟随该账号的新快照。'}
          </p>
          <div className='flex flex-wrap gap-3'>
            <label className='text-sm'>
              执行间隔
              <select
                className={selectClass}
                value={interval}
                onChange={(e) => setIntervalMinutes(Number(e.target.value))}
              >
                <option value={60}>每小时</option>
                <option value={360}>每 6 小时</option>
                <option value={1440}>每天</option>
              </select>
            </label>
            <Button
              className='self-end'
              variant='outline'
              disabled={!automation.data || !valid || auto.isPending}
              onClick={() => auto.mutate(true)}
            >
              保存当前规则并启用
            </Button>
            <Button
              className='self-end'
              variant='outline'
              disabled={!automation.data?.enabled || auto.isPending}
              onClick={() => auto.mutate(false)}
            >
              关闭自动导出
            </Button>
          </div>
          <ErrorBox error={auto.error || automation.error} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>导出任务</CardTitle>
        </CardHeader>
        <CardContent className='space-y-3'>
          <ErrorBox error={jobs.error || cancel.error} />
          {jobs.data?.jobs.length ? (
            jobs.data.jobs.map((job) => (
              <div
                key={job.id}
                className='flex flex-wrap items-center justify-between gap-3 rounded-md border p-3'
              >
                <div className='min-w-0'>
                  <p className='text-sm font-medium'>
                    {job.format.toUpperCase()} · {number(job.rows)} 条 ·{' '}
                    {
                      (
                        {
                          running: '执行中',
                          succeeded: '完成',
                          failed: '失败',
                          cancelled: '已取消',
                          interrupted: '已中断',
                        } as Record<string, string>
                      )[job.status]
                    }
                  </p>
                  <p className='mt-1 text-xs text-muted-foreground'>
                    {job.message}
                  </p>
                </div>
                {job.status === 'running' ? (
                  <Button
                    variant='outline'
                    size='sm'
                    disabled={cancel.isPending}
                    onClick={() => cancel.mutate(job.id)}
                  >
                    取消任务
                  </Button>
                ) : (
                  job.status === 'succeeded' && (
                    <Button variant='outline' size='sm' asChild>
                      <a
                        href={`${base}/archive/exports/${job.id}/download?${query}`}
                        download
                      >
                        下载 ZIP
                      </a>
                    </Button>
                  )
                )}
              </div>
            ))
          ) : (
            <p className='text-sm text-muted-foreground'>
              当前快照暂无导出任务
            </p>
          )}
        </CardContent>
      </Card>
      {Object.keys(selected).length === 1 && source === 'messages' && !all && (
        <LocalCopies
          key={Object.keys(selected)[0]}
          scope={scope}
          session={Object.keys(selected)[0]}
        />
      )}
    </div>
  )
}

function LocalCopies({ scope, session }: { scope: Scope; session: string }) {
  const [editing, setEditing] = useState<PreviewMessage | null>(null)
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const preview = useQuery({
    queryKey: ['archive-preview', scope, session],
    queryFn: ({ signal }) =>
      archiveApi<{ messages: PreviewMessage[] }>(
        `/preview?${params({ ...scope, session })}`,
        signal
      ),
  })
  const edit = useMutation({
    mutationFn: (reset: boolean) =>
      archiveApi('/edit', undefined, {
        ...scope,
        message_key: editing?.message_key,
        original_hash: editing?.original_hash,
        revision: editing?.edit.revision,
        text: reset ? null : text,
        note: reset ? '' : note,
      }),
    onSuccess: () => {
      setEditing(null)
      void preview.refetch()
    },
  })
  return (
    <Card>
      <CardHeader>
        <CardTitle>本地修订副本</CardTitle>
      </CardHeader>
      <CardContent className='space-y-3'>
        <p className='text-xs text-muted-foreground'>
          预览最近 20
          条记录。修改仅影响本机副本；导出时需勾选“使用本地修订副本”。
        </p>
        <ErrorBox error={preview.error || edit.error} />
        {preview.data?.messages.map((message) => (
          <div key={message.message_key} className='rounded-md border p-3'>
            <p className='text-xs text-muted-foreground'>
              {message.local_date} · {message.sender_name}
            </p>
            <p className='my-2 max-h-24 overflow-auto text-sm break-words whitespace-pre-wrap'>
              {message.edit.text ?? message.content ?? message.summary}
            </p>
            <Button
              size='sm'
              variant='outline'
              onClick={() => {
                setEditing(message)
                setText(message.edit.text ?? message.content)
                setNote(message.edit.note)
              }}
            >
              修订副本{message.edit.revision ? '（已修订）' : ''}
            </Button>
          </div>
        ))}
        {editing && (
          <div className='space-y-3 rounded-md border-2 p-4'>
            <h3 className='font-medium'>编辑本地副本</h3>
            <label className='block text-sm'>
              副本文本
              <Textarea
                autoFocus
                value={text}
                onChange={(e) => setText(e.target.value)}
                maxLength={100000}
              />
            </label>
            <label className='block text-sm'>
              备注
              <Textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={2000}
              />
            </label>
            <div className='flex flex-wrap gap-2'>
              <Button
                disabled={edit.isPending}
                onClick={() => edit.mutate(false)}
              >
                保存副本
              </Button>
              <Button
                variant='outline'
                disabled={edit.isPending}
                onClick={() => edit.mutate(true)}
              >
                恢复原文
              </Button>
              <Button variant='ghost' onClick={() => setEditing(null)}>
                关闭编辑
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function Notifications() {
  const status = useQuery({
    queryKey: ['wechat-live-status-v2'],
    queryFn: ({ signal }) => chatApi.status(signal),
    refetchInterval: 5000,
  })
  const account =
    status.data?.bridge_connected && !status.isError ? status.data.account : ''
  const config = useQuery({
    queryKey: ['archive-notification-config', account],
    queryFn: ({ signal }) =>
      archiveApi<NotificationPoll>(
        `/notifications?${params({ account: account! })}`,
        signal
      ),
    enabled: !!account,
    retry: false,
  })
  return (
    <>
      <ErrorBox error={status.error || config.error} />
      {account && config.data && !config.isError ? (
        <NotificationForm
          key={`${account}:${config.data.config.revision}`}
          initial={config.data.config}
        />
      ) : (
        <p role='status'>请连接微信后配置本机提醒。</p>
      )}
    </>
  )
}

export function NotificationForm({ initial }: { initial: NotificationConfig }) {
  const client = useQueryClient()
  const [value, setValue] = useState(initial)
  const [sessions, setSessions] = useState(initial.sessions.join('\n'))
  const [permission, setPermission] = useState(
    typeof Notification === 'undefined'
      ? 'unsupported'
      : Notification.permission
  )
  const save = useMutation({
    mutationFn: () =>
      archiveApi<NotificationConfig>('/notifications', undefined, {
        ...value,
        sessions: sessions.split(/\s+/).filter(Boolean),
      }),
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: ['archive-notification-optin'] }),
        client.invalidateQueries({
          queryKey: ['archive-notification-config', initial.account],
        }),
      ]),
  })
  return (
    <Card>
      <CardHeader>
        <CardTitle>新消息桌面提醒</CardTitle>
      </CardHeader>
      <CardContent className='space-y-4'>
        <p className='text-xs break-all text-muted-foreground'>
          绑定账号：{value.account} ·
          仅提醒刚收到的新消息，历史导入与重放不提醒。
        </p>
        <label className='flex items-center gap-2 text-sm'>
          <input
            type='checkbox'
            checked={value.enabled}
            onChange={(e) => setValue({ ...value, enabled: e.target.checked })}
          />
          启用本机提醒
        </label>
        <label className='block text-sm'>
          会话规则
          <select
            className={selectClass}
            value={value.mode}
            onChange={(e) =>
              setValue({
                ...value,
                mode: e.target.value as NotificationConfig['mode'],
              })
            }
          >
            <option value='all'>所有会话</option>
            <option value='allowlist'>只提醒下方会话</option>
            <option value='blocklist'>排除下方会话</option>
          </select>
        </label>
        {value.mode !== 'all' && (
          <label className='block text-sm'>
            会话微信 ID（每行一个，最多 200 个）
            <Textarea
              value={sessions}
              onChange={(e) => setSessions(e.target.value)}
              placeholder={'wxid_example\n12345@chatroom'}
            />
          </label>
        )}
        <label className='flex items-center gap-2 text-sm'>
          <input
            type='checkbox'
            checked={value.preview}
            onChange={(e) => setValue({ ...value, preview: e.target.checked })}
          />
          在提醒中显示消息摘要（默认隐藏）
        </label>
        <label className='flex items-center gap-2 text-sm'>
          <input
            type='checkbox'
            checked={value.quiet_enabled}
            onChange={(e) =>
              setValue({ ...value, quiet_enabled: e.target.checked })
            }
          />
          启用免打扰时段（本机时间）
        </label>
        {value.quiet_enabled && (
          <div className='flex gap-3'>
            {(['quiet_start', 'quiet_end'] as const).map((key, i) => (
              <label key={key} className='text-sm'>
                {i ? '结束小时' : '开始小时'}
                <Input
                  type='number'
                  min={0}
                  max={23}
                  value={value[key]}
                  onChange={(e) =>
                    setValue({ ...value, [key]: Number(e.target.value) })
                  }
                />
              </label>
            ))}
          </div>
        )}
        <p className='text-sm text-muted-foreground'>
          浏览器权限：
          {
            (
              {
                granted: '已允许',
                denied: '已拒绝，请在浏览器站点设置中修改',
                default: '尚未授权',
                unsupported: '当前浏览器不支持',
              } as Record<string, string>
            )[permission]
          }
          。提醒需要本页面所在应用保持打开。
        </p>
        <div className='flex flex-wrap gap-3'>
          <Button
            variant='outline'
            disabled={permission !== 'default'}
            onClick={() =>
              void Notification.requestPermission().then(setPermission)
            }
          >
            允许浏览器提醒
          </Button>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            保存提醒设置
          </Button>
        </div>
        <ErrorBox error={save.error} />
        {save.isSuccess && (
          <p role='status' className='text-sm'>
            提醒设置已保存。
          </p>
        )}
      </CardContent>
    </Card>
  )
}
