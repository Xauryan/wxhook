import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getRouteApi, useNavigate } from '@tanstack/react-router'
import {
  ArrowLeft,
  ArrowUpRight,
  ListTodo,
  Plus,
  RefreshCw,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  api,
  ApiError,
  prefix,
  write,
  type Me,
  type Member,
  type Task,
  type Snapshot,
  type Activity,
} from './api'
import { Members } from './members'

const taskRoute = getRouteApi('/_authenticated/tasks/')

const states: Record<string, string> = {
  draft: '草稿',
  open: '待领取',
  in_progress: '进行中',
  in_review: '待确认',
  changes_requested: '待修改',
  done: '已完成',
  cancelled: '已取消',
}
const deliveryStates: Record<string, string> = {
  active: '待确认',
  accepted: '已确认完成',
  rejected: '已退回',
  withdrawn: '已撤回',
}
const workStates: Record<string, string> = {
  todo: '待开始',
  doing: '进行中',
  done: '已完成',
}
const actionNames: Record<string, string> = {
  created: '创建任务',
  publish: '发布任务',
  claim: '认领负责人',
  join: '加入协作',
  leave: '退出协作',
  submit: '提交成果',
  complete: '确认完成',
  'request-changes': '退回修改',
  withdraw: '撤回成果',
  comment: '添加讨论',
  'add-work': '分配工作',
  'update-work': '更新分工',
  'owner-transfer': '发起转交',
  'accept-transfer': '接受转交',
  'assign-owner': '指派负责人',
  'cancel-transfer': '取消转交',
  edit: '编辑任务',
  copy: '复制任务',
  cancel: '取消任务',
  reopen: '重开任务',
}
const selectClass =
  'h-9 min-w-0 max-w-full rounded-md border bg-background px-3 text-sm'
const areaClass =
  'min-h-24 w-full rounded-md border bg-background p-3 text-sm font-normal'
function monthNow() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
  })
    .format(new Date())
    .slice(0, 7)
}
function date(value: string | number) {
  return new Date(
    typeof value === 'number' ? value * 1000 : value
  ).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
}
function Alert({ children }: { children: ReactNode }) {
  return (
    <div
      role='alert'
      className='rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive'
    >
      {children}
    </div>
  )
}
function Field({ title, children }: { title: string; children: ReactNode }) {
  return (
    <label className='grid min-w-0 gap-2 text-sm font-medium'>
      {title}
      {children}
    </label>
  )
}
function Panel({
  title,
  action,
  children,
}: {
  title: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section className='min-w-0 rounded-lg border p-4 sm:p-5'>
      <div className='mb-4 flex flex-wrap items-center justify-between gap-3'>
        <h2 className='font-semibold'>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )
}

export function Center() {
  const client = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/me') })
  if (me.isPending) return <div className='p-10'>正在加载任务管理…</div>
  if (!me.data || me.error)
    return (
      <EmailIdentity
        error={me.error}
        onSuccess={() => {
          client.clear()
          void me.refetch()
        }}
      />
    )
  return <Workspace me={me.data} />
}

function EmailIdentity({
  error: upstream,
  onSuccess,
}: {
  error: Error | null
  onSuccess: () => void
}) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [ownerMode, setOwnerMode] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    const fields = new FormData(event.currentTarget)
    try {
      await api(ownerMode ? '/auth/owner-session' : '/auth/identify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          ownerMode
            ? { login: fields.get('login'), password: fields.get('password') }
            : { email: fields.get('email') }
        ),
      })
      onSuccess()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className='mx-auto flex w-full max-w-md flex-col gap-5 px-6 py-12'>
      <ListTodo className='size-8' />
      <h1 className='text-2xl font-semibold'>
        {ownerMode ? '所有者登录' : '任务管理'}
      </h1>
      {!ownerMode && (
        <p className='text-sm text-muted-foreground'>
          填写你提交代码时使用的 Git 邮箱，即可查看和参与任务。
        </p>
      )}
      {upstream &&
        !(upstream instanceof ApiError && upstream.status === 401) && (
          <Alert>{upstream.message}</Alert>
        )}
      <form onSubmit={submit} className='space-y-4'>
        {ownerMode ? (
          <>
            <Field title='管理账号'>
              <Input name='login' autoComplete='username' required />
            </Field>
            <Field title='管理密码'>
              <Input
                name='password'
                type='password'
                autoComplete='current-password'
                required
              />
            </Field>
          </>
        ) : (
          <>
            <Field title='Git 邮箱'>
              <Input
                name='email'
                type='email'
                autoComplete='email'
                placeholder='you@example.com'
                required
                maxLength={254}
              />
            </Field>
            <p className='text-xs text-muted-foreground'>
              仅用作任务署名，不验证邮箱归属。请始终填写同一个邮箱。
            </p>
          </>
        )}
        {error && <Alert>{error}</Alert>}
        <Button type='submit' className='w-full' disabled={busy}>
          {busy ? '正在进入…' : '进入任务管理'}
        </Button>
      </form>
      <Button
        variant='link'
        size='sm'
        disabled={busy}
        onClick={() => {
          setOwnerMode(!ownerMode)
          setError('')
        }}
      >
        {ownerMode ? '使用 Git 邮箱进入' : '所有者入口'}
      </Button>
    </section>
  )
}
type Operation = { action: string; title: string; task?: Task }
function Workspace({ me }: { me: Me }) {
  const routeNavigate = useNavigate()
  const selected = taskRoute.useSearch().task || ''
  const client = useQueryClient()
  const [section, setSection] = useState('month')
  const [month, setMonth] = useState(monthNow)
  const [status, setStatus] = useState('')
  const [cursor, setCursor] = useState(0)
  const [activityCursor, setActivityCursor] = useState(0)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [operation, setOperation] = useState<Operation | null>(null)
  const members = useQuery({
    queryKey: ['members'],
    queryFn: () => api<Member[]>('/members'),
    refetchInterval: connected ? false : 30000,
  })
  const filter = new URLSearchParams({ cursor: String(cursor) })
  if (section === 'month') filter.set('month', month)
  if (['lead', 'created', 'joined', 'outstanding'].includes(section))
    filter.set('mine', section)
  if (section === 'review') filter.set('status', 'in_review')
  else if (status) filter.set('status', status)
  const list = useQuery({
    queryKey: ['tasks', filter.toString()],
    queryFn: () => api<Snapshot>('/tasks?' + filter),
    refetchInterval: connected ? false : 30000,
  })
  const detail = useQuery({
    queryKey: ['task', selected],
    queryFn: () => api<Task>('/tasks/' + selected),
    enabled: !!selected,
    refetchInterval: connected ? false : 30000,
  })
  const activity = useQuery({
    queryKey: ['activity', selected, activityCursor],
    queryFn: () =>
      api<{ items: Activity[]; next_cursor: number | null }>(
        `/tasks/${selected}/activity?before=${activityCursor}`
      ),
    enabled: !!selected,
    refetchInterval: connected ? false : 30000,
  })
  const name = (id?: string | null) =>
    members.data?.find((m) => m.id === id)?.name || (id ? '历史成员' : '待领取')
  const eventCursor = list.data?.event_cursor
  useEffect(() => {
    if (eventCursor === undefined) return
    const stream = new EventSource(prefix + '/events?after=' + eventCursor)
    stream.onopen = () => setConnected(true)
    stream.onerror = () => setConnected(false)
    const refresh = () => {
      for (const key of ['tasks', 'task', 'activity', 'members'])
        void client.invalidateQueries({ queryKey: [key] })
    }
    stream.addEventListener('change', refresh)
    stream.addEventListener('reset', refresh)
    stream.addEventListener('auth-error', () => {
      stream.close()
      setConnected(false)
      client.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' })
      void client.invalidateQueries({ queryKey: ['me'] })
    })
    return () => stream.close()
  }, [eventCursor, client])
  function navigate(id: string) {
    void routeNavigate({ to: '/tasks', search: id ? { task: id } : {} })
    setActivityCursor(0)
    setError('')
  }
  async function refresh() {
    await Promise.all(
      ['tasks', 'task', 'activity', 'members'].map((key) =>
        client.invalidateQueries({ queryKey: [key] })
      )
    )
  }
  async function execute(task: Task, action: string, values = {}) {
    setBusy(true)
    setError('')
    try {
      await write(me, `/tasks/${task.id}/actions/${action}`, {
        expected_version: task.version,
        ...values,
      })
      await refresh()
    } catch (e) {
      setError((e as Error).message)
      if (e instanceof ApiError && e.status === 409) await refresh()
    } finally {
      setBusy(false)
    }
  }
  const nav = [
    ['month', '月度任务'],
    ['created', '我创建'],
    ['lead', '我负责'],
    ['joined', '我参与'],
    ['outstanding', '跨月未完成'],
    ...(me.owner
      ? [
          ['review', '待我确认'],
          ['members', '成员管理'],
        ]
      : []),
  ]
  const task = detail.data
  const active =
    !!task &&
    ['open', 'in_progress', 'in_review', 'changes_requested'].includes(
      task.status
    )
  const lead = !!task && (me.owner || task.lead_id === me.id)
  const canEdit = !!task && (me.owner || task.created_by === me.id)
  const joined = task?.members[me.id]?.active
  const delivery = task?.deliveries.find((d) => d.state === 'active')
  function open(action: string, title: string) {
    setOperation({ action, title, task })
  }
  return (
    <div className='min-w-0 space-y-5 [overflow-wrap:anywhere]'>
      <header className='flex min-w-0 flex-wrap items-center gap-3'>
        <h1 className='grow text-2xl font-bold tracking-tight'>任务管理</h1>
        <div className='order-last flex min-w-0 basis-full items-center gap-2 text-sm sm:order-none sm:max-w-xs sm:basis-auto'>
          <span
            className='min-w-0 truncate text-muted-foreground'
            title={me.name}
          >
            {me.name}
          </span>
          <Badge variant='secondary' className='shrink-0'>
            {me.owner ? '所有者' : '成员'}
          </Badge>
          <Button
            size='sm'
            variant='ghost'
            className='shrink-0'
            onClick={async () => {
              try {
                await write(me, '/auth/logout', {})
                client.clear()
                location.assign('/tasks')
              } catch (e) {
                setError((e as Error).message)
              }
            }}
          >
            退出
          </Button>
        </div>
        {!selected && section !== 'members' && (
          <Button
            onClick={() =>
              setOperation({ action: 'create', title: '新建任务' })
            }
          >
            <Plus />
            新建任务
          </Button>
        )}
      </header>
      <nav
        aria-label='任务分类'
        className='flex min-w-0 gap-5 overflow-x-auto border-b'
      >
        {nav.map(([key, title]) => (
          <button
            key={key}
            className={
              'shrink-0 border-b-2 px-1 pb-3 text-sm transition-colors ' +
              (section === key
                ? 'border-foreground font-semibold'
                : 'border-transparent text-muted-foreground hover:text-foreground')
            }
            aria-current={section === key ? 'page' : undefined}
            onClick={() => {
              setSection(key)
              setCursor(0)
              setStatus('')
              navigate('')
            }}
          >
            {title}
          </button>
        ))}
      </nav>
      <div>
        <div className='space-y-4'>
          {error && <Alert>{error}</Alert>}
          {members.error && <Alert>{members.error.message}</Alert>}
          {section === 'members' && me.owner ? (
            <Members me={me} />
          ) : !selected ? (
            <>
              <div className='flex flex-wrap items-center justify-between gap-3'>
                <div className='flex min-w-0 flex-wrap items-center gap-2'>
                  {section === 'month' && (
                    <Input
                      aria-label='任务月份'
                      className='w-40'
                      type='month'
                      value={month}
                      onChange={(e) => {
                        setMonth(e.target.value)
                        setCursor(0)
                      }}
                    />
                  )}
                  {section !== 'review' && (
                    <select
                      aria-label='任务状态'
                      className={selectClass}
                      value={status}
                      onChange={(e) => {
                        setStatus(e.target.value)
                        setCursor(0)
                      }}
                    >
                      <option value=''>全部状态</option>
                      {Object.entries(states).map(([key, title]) => (
                        <option key={key} value={key}>
                          {title}
                        </option>
                      ))}
                    </select>
                  )}
                  <Button
                    variant='ghost'
                    size='icon'
                    aria-label='刷新任务'
                    title='刷新任务'
                    onClick={() => void refresh()}
                  >
                    <RefreshCw
                      className={list.isFetching ? 'animate-spin' : ''}
                    />
                  </Button>
                </div>
              </div>
              {list.error && (
                <Alert>任务加载失败：{list.error.message}。请刷新重试。</Alert>
              )}
              {list.isPending ? (
                <p>正在加载任务…</p>
              ) : (
                <div className='overflow-hidden rounded-lg border'>
                  <Table className='table-fixed'>
                    <TableHeader>
                      <TableRow>
                        <TableHead>任务</TableHead>
                        <TableHead className='hidden w-[24%] md:table-cell'>
                          负责人
                        </TableHead>
                        <TableHead className='w-24 sm:w-28'>状态</TableHead>
                        <TableHead className='hidden w-20 lg:table-cell'>
                          协作
                        </TableHead>
                        <TableHead className='hidden w-32 xl:table-cell'>
                          截止日期
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {list.data?.items.map((t) => (
                        <TableRow key={t.id}>
                          <TableCell className='py-4 whitespace-normal'>
                            <button
                              className='block w-full min-w-0 text-left hover:underline'
                              onClick={() => navigate(t.id)}
                            >
                              <span className='mb-1 block font-mono text-xs text-muted-foreground'>
                                {t.number} · {t.month}
                              </span>
                              <span className='line-clamp-2 font-medium [overflow-wrap:anywhere]'>
                                {t.title}
                              </span>
                            </button>
                            <p
                              className='mt-1.5 truncate text-xs text-muted-foreground'
                              title={
                                t.created_by ? name(t.created_by) : '未记录'
                              }
                            >
                              创建人：
                              {t.created_by ? name(t.created_by) : '未记录'}
                            </p>
                            <p className='mt-1 truncate text-xs text-muted-foreground md:hidden'>
                              负责人：{name(t.lead_id)}
                            </p>
                          </TableCell>
                          <TableCell className='hidden md:table-cell'>
                            <span
                              className='block truncate'
                              title={name(t.lead_id)}
                            >
                              {name(t.lead_id)}
                            </span>
                          </TableCell>
                          <TableCell>
                            <Badge variant='outline'>{states[t.status]}</Badge>
                          </TableCell>
                          <TableCell className='hidden text-muted-foreground lg:table-cell'>
                            {
                              Object.entries(t.members).filter(
                                ([id, m]) => m.active && id !== t.lead_id
                              ).length
                            }
                          </TableCell>
                          <TableCell className='hidden text-xs text-muted-foreground xl:table-cell'>
                            {new Date(t.deadline).toLocaleDateString('zh-CN', {
                              timeZone: 'Asia/Shanghai',
                            })}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {!list.error && list.data?.items.length === 0 && (
                    <p className='p-12 text-center text-sm text-muted-foreground'>
                      暂无任务
                    </p>
                  )}
                </div>
              )}
              <div className='flex gap-2'>
                {cursor > 0 && (
                  <Button variant='outline' onClick={() => setCursor(0)}>
                    第一页
                  </Button>
                )}
                {list.data?.next_cursor && (
                  <Button
                    variant='outline'
                    onClick={() => setCursor(list.data!.next_cursor!)}
                  >
                    下一页
                  </Button>
                )}
              </div>
            </>
          ) : (
            <>
              <Button
                variant='ghost'
                className='-ml-3'
                onClick={() => navigate('')}
              >
                <ArrowLeft />
                返回任务列表
              </Button>
              {detail.error && <Alert>{detail.error.message}</Alert>}
              {detail.isPending && <p>正在加载任务…</p>}
              {task && (
                <>
                  <div className='space-y-5 rounded-lg border p-4 sm:p-6'>
                    <div className='min-w-0 space-y-3'>
                      <p className='flex items-center gap-3 font-mono text-xs text-muted-foreground'>
                        {task.number} / {task.month}
                        <Badge variant='outline'>{states[task.status]}</Badge>
                      </p>
                      <h2 className='text-xl font-semibold [overflow-wrap:anywhere] sm:text-2xl'>
                        {task.title}
                      </h2>
                      <dl className='grid min-w-0 gap-3 text-sm sm:grid-cols-2 xl:grid-cols-3'>
                        <div className='min-w-0'>
                          <dt className='text-xs text-muted-foreground'>
                            创建人
                          </dt>
                          <dd className='mt-1'>
                            {task.created_by ? name(task.created_by) : '未记录'}
                          </dd>
                        </div>
                        <div className='min-w-0'>
                          <dt className='text-xs text-muted-foreground'>
                            负责人
                          </dt>
                          <dd className='mt-1'>{name(task.lead_id)}</dd>
                        </div>
                        <div>
                          <dt className='text-xs text-muted-foreground'>
                            截止时间
                          </dt>
                          <dd className='mt-1'>{date(task.deadline)}</dd>
                        </div>
                      </dl>
                    </div>
                    <div className='flex flex-wrap items-center gap-2 border-t pt-4'>
                      {canEdit &&
                        !delivery &&
                        !['done', 'cancelled'].includes(task.status) && (
                          <Button
                            variant='outline'
                            onClick={() => open('edit', '编辑任务')}
                          >
                            编辑任务
                          </Button>
                        )}
                      {active && !task.lead_id && (
                        <Button
                          disabled={busy}
                          onClick={() => void execute(task, 'claim')}
                        >
                          认领负责人
                        </Button>
                      )}
                      {active && !joined && (
                        <Button
                          variant='outline'
                          disabled={busy}
                          onClick={() => void execute(task, 'join')}
                        >
                          加入协作
                        </Button>
                      )}
                      {active && joined && task.lead_id !== me.id && (
                        <Button
                          variant='outline'
                          disabled={busy}
                          onClick={() => void execute(task, 'leave')}
                        >
                          退出协作
                        </Button>
                      )}
                      {active && lead && task.lead_id && !delivery && (
                        <Button onClick={() => open('submit', '提交成果')}>
                          提交成果
                        </Button>
                      )}
                      {active && lead && delivery && (
                        <Button
                          variant='outline'
                          onClick={() => open('withdraw', '撤回成果')}
                        >
                          撤回成果
                        </Button>
                      )}
                      {canEdit && task.status === 'draft' && (
                        <Button
                          disabled={busy}
                          onClick={() => void execute(task, 'publish')}
                        >
                          发布任务
                        </Button>
                      )}
                      {me.owner && task.status === 'in_review' && delivery && (
                        <>
                          <Button
                            onClick={() => open('complete', '确认任务完成')}
                          >
                            确认完成
                          </Button>
                          <Button
                            variant='outline'
                            onClick={() => open('request-changes', '退回修改')}
                          >
                            退回修改
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                  {task.lead_unavailable && (
                    <Alert>
                      负责人身份已停用，请所有者重新指派。历史记录仍保留。
                    </Alert>
                  )}
                  <div className='grid gap-6 xl:grid-cols-[minmax(0,1fr)_280px]'>
                    <div className='min-w-0 space-y-6'>
                      <Panel title='目标与验收标准'>
                        <h3 className='text-sm font-medium'>任务目标</h3>
                        <p className='mt-2 text-sm leading-7 break-words whitespace-pre-wrap text-muted-foreground'>
                          {task.objective}
                        </p>
                        <h3 className='mt-5 text-sm font-medium'>验收标准</h3>
                        <p className='mt-2 text-sm leading-7 break-words whitespace-pre-wrap text-muted-foreground'>
                          {task.criteria}
                        </p>
                      </Panel>
                      <Panel
                        title='协作分工'
                        action={
                          active &&
                          lead && (
                            <Button
                              size='sm'
                              variant='outline'
                              onClick={() => open('add-work', '添加分工')}
                            >
                              添加分工
                            </Button>
                          )
                        }
                      >
                        {!task.work_items.length && (
                          <p className='text-sm text-muted-foreground'>
                            负责人可以将工作分给参与成员。
                          </p>
                        )}
                        <div className='divide-y'>
                          {task.work_items.map((w) => (
                            <div
                              key={w.id}
                              className='flex flex-wrap items-center justify-between gap-3 py-3'
                            >
                              <div className='min-w-0 flex-1 basis-40'>
                                <p className='text-sm break-words'>{w.title}</p>
                                <p className='text-xs text-muted-foreground'>
                                  {name(w.assignee)}
                                </p>
                              </div>
                              <select
                                aria-label={w.title + '进度'}
                                className={selectClass}
                                value={w.state}
                                disabled={
                                  busy ||
                                  !active ||
                                  (!lead && !(joined && w.assignee === me.id))
                                }
                                onChange={(e) =>
                                  void execute(task, 'update-work', {
                                    item_id: w.id,
                                    work_state: e.target.value,
                                  })
                                }
                              >
                                {Object.entries(workStates).map(([k, v]) => (
                                  <option key={k} value={k}>
                                    {v}
                                  </option>
                                ))}
                              </select>
                            </div>
                          ))}
                        </div>
                      </Panel>
                      <Panel title='成果记录'>
                        {!task.deliveries.length && (
                          <p className='text-sm text-muted-foreground'>
                            尚未提交成果。
                          </p>
                        )}
                        <div className='space-y-5'>
                          {task.deliveries
                            .slice()
                            .reverse()
                            .map((d) => (
                              <article
                                key={d.id}
                                className='rounded-md border p-4'
                              >
                                <div className='flex flex-wrap justify-between gap-2'>
                                  <span className='text-sm'>
                                    {name(d.author)} · {date(d.at)}
                                  </span>
                                  <Badge variant='outline'>
                                    {deliveryStates[d.state]}
                                  </Badge>
                                </div>
                                <p className='my-3 text-sm leading-6 break-words whitespace-pre-wrap'>
                                  {d.summary}
                                </p>
                                <ul className='space-y-2'>
                                  {d.pr_links.map((link) => (
                                    <li key={link}>
                                      <a
                                        href={link}
                                        target='_blank'
                                        rel='noopener noreferrer'
                                        className='inline-flex max-w-full items-start gap-1 text-sm underline'
                                      >
                                        <span className='break-all'>
                                          {link}
                                        </span>
                                        <ArrowUpRight className='size-4 shrink-0' />
                                      </a>
                                    </li>
                                  ))}
                                </ul>
                                {d.reviewed_by && (
                                  <p className='mt-3 text-xs text-muted-foreground'>
                                    由 {name(d.reviewed_by)} 于{' '}
                                    {date(d.reviewed_at!)}{' '}
                                    {d.state === 'accepted'
                                      ? '确认完成'
                                      : '退回修改'}
                                  </p>
                                )}
                                {d.reason && (
                                  <p className='mt-2 text-sm break-words whitespace-pre-wrap'>
                                    说明：{d.reason}
                                  </p>
                                )}
                              </article>
                            ))}
                        </div>
                      </Panel>
                      <Panel
                        title='讨论'
                        action={
                          active && (
                            <Button
                              size='sm'
                              variant='outline'
                              onClick={() => open('comment', '添加讨论')}
                            >
                              添加讨论
                            </Button>
                          )
                        }
                      >
                        {!task.comments.length && (
                          <p className='text-sm text-muted-foreground'>
                            记录问题、进度和协作约定。
                          </p>
                        )}
                        <div className='space-y-4'>
                          {task.comments.map((c) => (
                            <div key={c.id}>
                              <p className='text-xs text-muted-foreground'>
                                {name(c.author)} · {date(c.at)}
                              </p>
                              <p className='mt-1 text-sm break-words whitespace-pre-wrap'>
                                {c.text}
                              </p>
                            </div>
                          ))}
                        </div>
                      </Panel>
                    </div>
                    <div className='min-w-0 space-y-6'>
                      <Panel title='参与成员'>
                        <div className='space-y-3'>
                          {Object.entries(task.members).map(([id, m]) => (
                            <p
                              key={id}
                              className='flex justify-between gap-2 text-sm'
                            >
                              <span className='min-w-0 [overflow-wrap:anywhere]'>
                                {name(id)}
                              </span>
                              <span className='shrink-0 text-xs text-muted-foreground'>
                                {id === task.lead_id
                                  ? '负责人'
                                  : m.active
                                    ? '协作者'
                                    : '已退出'}
                              </span>
                            </p>
                          ))}
                        </div>
                        {active && lead && task.lead_id && !task.transfer && (
                          <Button
                            className='mt-4'
                            variant='outline'
                            size='sm'
                            onClick={() => open('owner-transfer', '转交负责人')}
                          >
                            转交负责人
                          </Button>
                        )}
                        {task.transfer && (
                          <div className='mt-4 space-y-2 text-sm'>
                            <p>等待 {name(task.transfer.to)} 接受</p>
                            {task.transfer.to === me.id && (
                              <Button
                                size='sm'
                                onClick={() =>
                                  void execute(task, 'accept-transfer')
                                }
                              >
                                接受转交
                              </Button>
                            )}
                            {lead && (
                              <Button
                                size='sm'
                                variant='ghost'
                                onClick={() =>
                                  void execute(task, 'cancel-transfer')
                                }
                              >
                                取消转交
                              </Button>
                            )}
                          </div>
                        )}
                      </Panel>
                      {me.owner && (
                        <Panel title='所有者操作'>
                          <div className='grid gap-2'>
                            {active && (
                              <Button
                                variant='outline'
                                onClick={() =>
                                  open('assign-owner', '指派负责人')
                                }
                              >
                                指派负责人
                              </Button>
                            )}
                            <Button
                              variant='outline'
                              onClick={() => open('copy', '复制到其他月份')}
                            >
                              复制任务
                            </Button>
                            {['done', 'cancelled'].includes(task.status) ? (
                              <Button
                                variant='outline'
                                onClick={() => open('reopen', '重新打开任务')}
                              >
                                重新打开
                              </Button>
                            ) : (
                              <Button
                                variant='ghost'
                                onClick={() => open('cancel', '取消任务')}
                              >
                                取消任务
                              </Button>
                            )}
                          </div>
                        </Panel>
                      )}
                      <Panel title='活动记录'>
                        {activity.error && (
                          <Alert>{activity.error.message}</Alert>
                        )}
                        <ol className='space-y-3'>
                          {activity.data?.items.map((e) => (
                            <li key={e.id} className='text-xs leading-5'>
                              <p>
                                {name(e.actor)} ·{' '}
                                {actionNames[e.action.replace('task.', '')] ||
                                  '历史操作'}
                              </p>
                              <p className='text-muted-foreground'>
                                {date(e.at)}
                              </p>
                              {typeof e.detail.reason === 'string' && (
                                <p className='break-words'>{e.detail.reason}</p>
                              )}
                            </li>
                          ))}
                        </ol>
                        <div className='mt-4 flex gap-2'>
                          {activityCursor > 0 && (
                            <Button
                              size='sm'
                              variant='ghost'
                              onClick={() => setActivityCursor(0)}
                            >
                              最新记录
                            </Button>
                          )}
                          {activity.data?.next_cursor && (
                            <Button
                              size='sm'
                              variant='ghost'
                              onClick={() =>
                                setActivityCursor(activity.data!.next_cursor!)
                              }
                            >
                              更早记录
                            </Button>
                          )}
                        </div>
                      </Panel>
                    </div>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
      {operation && (
        <OperationForm
          operation={operation}
          me={me}
          members={members.data || []}
          month={month}
          onClose={() => setOperation(null)}
          onSaved={async (t) => {
            if (operation.action === 'create') {
              setSection('created')
              setStatus('')
              setCursor(0)
            }
            await refresh()
            navigate(t.id)
            setOperation(null)
          }}
        />
      )}
    </div>
  )
}

function OperationForm({
  operation,
  me,
  members,
  month,
  onClose,
  onSaved,
}: {
  operation: Operation
  me: Me
  members: Member[]
  month: string
  onClose: () => void
  onSaved: (t: Task) => Promise<void>
}) {
  const { action, task } = operation
  const [version, setVersion] = useState(task?.version)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const editing = action === 'create' || action === 'edit'
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setBusy(true)
    setError('')
    const body = Object.fromEntries(new FormData(e.currentTarget)) as Record<
      string,
      unknown
    >
    try {
      if (editing)
        body.deadline = new Date(
          String(body.deadline) + ':00+08:00'
        ).toISOString()
      if (task) body.expected_version = version
      if (action === 'submit')
        body.pr_links = String(body.pr_links)
          .split(/\r?\n/)
          .map((s) => s.trim())
          .filter(Boolean)
      const path =
        action === 'create'
          ? '/tasks'
          : action === 'edit'
            ? '/tasks/' + task!.id
            : `/tasks/${task!.id}/actions/${action}`
      await onSaved(
        await write<Task>(me, path, body, action === 'edit' ? 'PATCH' : 'POST')
      )
    } catch (e) {
      setError((e as Error).message)
      setConflict(e instanceof ApiError && e.status === 409)
    } finally {
      setBusy(false)
    }
  }
  const deadline = task
    ? new Intl.DateTimeFormat('sv-SE', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
        .format(new Date(task.deadline))
        .replace(' ', 'T')
    : ''
  return (
    <Dialog
      open
      onOpenChange={(value) => {
        if (!value && !busy) onClose()
      }}
    >
      <DialogContent
        className='max-h-[90svh] overflow-y-auto sm:max-w-xl'
        {...(action === 'complete' ? {} : { 'aria-describedby': undefined })}
      >
        <DialogHeader>
          <DialogTitle>{operation.title}</DialogTitle>
          {action === 'complete' && (
            <DialogDescription>
              确认已查看 PR，且成果满足验收标准。
            </DialogDescription>
          )}
        </DialogHeader>
        <form className='space-y-4' onSubmit={submit}>
          {editing && (
            <>
              <Field title='任务标题'>
                <Input
                  name='title'
                  required
                  maxLength={200}
                  defaultValue={task?.title}
                />
              </Field>
              <div className='grid grid-cols-2 gap-4'>
                <Field title='归属月份'>
                  <Input
                    type='month'
                    name='month'
                    required
                    defaultValue={task?.month || month}
                  />
                </Field>
                <Field title='优先级'>
                  <select
                    name='priority'
                    defaultValue={task?.priority || 'medium'}
                    className={selectClass}
                  >
                    <option value='low'>低</option>
                    <option value='medium'>中</option>
                    <option value='high'>高</option>
                  </select>
                </Field>
              </div>
              <Field title='截止时间（北京时间）'>
                <Input
                  type='datetime-local'
                  name='deadline'
                  required
                  defaultValue={deadline}
                />
              </Field>
              <Field title='任务目标'>
                <textarea
                  className={areaClass}
                  name='objective'
                  required
                  maxLength={8000}
                  defaultValue={task?.objective}
                />
              </Field>
              <Field title='验收标准'>
                <textarea
                  className={areaClass}
                  name='criteria'
                  required
                  maxLength={8000}
                  defaultValue={task?.criteria}
                />
              </Field>
            </>
          )}
          {['comment', 'submit'].includes(action) && (
            <Field title={action === 'submit' ? '成果说明' : '讨论内容'}>
              <textarea
                className={areaClass}
                name='text'
                required
                maxLength={8000}
              />
            </Field>
          )}
          {action === 'submit' && (
            <Field title='PR 链接（每行一个）'>
              <textarea
                className={areaClass}
                name='pr_links'
                aria-label='PR 链接（每行一个）'
                required
                placeholder='https://github.com/owner/repo/pull/123'
              />
            </Field>
          )}
          {[
            'cancel',
            'reopen',
            'withdraw',
            'request-changes',
            'assign-owner',
            'complete',
          ].includes(action) && (
            <Field
              title={action === 'complete' ? '确认说明（可选）' : '操作原因'}
            >
              <textarea
                className={areaClass}
                name='reason'
                required={action !== 'complete'}
                maxLength={2000}
              />
            </Field>
          )}
          {action === 'copy' && (
            <Field title='归属月份'>
              <Input
                name='month'
                type='month'
                required
                defaultValue={monthNow()}
              />
            </Field>
          )}
          {action === 'add-work' && (
            <Field title='分工内容'>
              <Input name='title' required maxLength={200} />
            </Field>
          )}
          {['add-work', 'owner-transfer', 'assign-owner'].includes(action) && (
            <Field title='选择成员'>
              <select
                className={selectClass}
                name='target_id'
                required
                defaultValue=''
              >
                <option value='' disabled>
                  选择成员
                </option>
                {members
                  .filter(
                    (m) =>
                      !m.disabled &&
                      (action !== 'add-work' || task?.members[m.id]?.active)
                  )
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name} / {m.login}
                    </option>
                  ))}
              </select>
            </Field>
          )}
          {error && <Alert>{error}</Alert>}
          {conflict && task && (
            <Button
              type='button'
              variant='outline'
              onClick={async () => {
                try {
                  const latest = await api<Task>('/tasks/' + task.id)
                  setVersion(latest.version)
                  setConflict(false)
                  setError(
                    '已获取最新版本，请核对填写内容后再次确认。当前状态：' +
                      states[latest.status]
                  )
                } catch (e) {
                  setError((e as Error).message)
                }
              }}
            >
              保留填写并刷新版本
            </Button>
          )}
          <div className='flex justify-end gap-3'>
            <Button
              type='button'
              variant='outline'
              disabled={busy}
              onClick={onClose}
            >
              取消
            </Button>
            <Button type='submit' disabled={busy}>
              {busy
                ? '正在提交…'
                : action === 'create'
                  ? '保存草稿'
                  : '确认提交'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
