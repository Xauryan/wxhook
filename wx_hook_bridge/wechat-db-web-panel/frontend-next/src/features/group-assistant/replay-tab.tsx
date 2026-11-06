import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  modeNames,
  replyApi,
  type BatchSummary,
  type Case,
  type ReplyRule,
  type RunSummary,
  type SearchMode,
} from '@/features/group-replies/api'
import { RunDetailSheet } from './run-detail'
import { ChipToggle, EmptyState, ErrorText } from './shared'
import { formatTime, newRequestId, reasonText } from './utils'

const batchStates: Record<string, string> = {
  completed: '已完成',
  cancelled: '已停止',
  running: '进行中',
}
const triggerNames = { original: '原消息', ask: '加 /ask' } as const
type Trigger = keyof typeof triggerNames

export function ReplayTab({
  account,
  rule,
  batches,
  onSetup,
}: {
  account: string
  rule?: ReplyRule
  batches: BatchSummary[]
  onSetup: () => void
}) {
  const client = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [selected, setSelected] = useState('')
  const [runId, setRunId] = useState('')
  if (!rule)
    return (
      <EmptyState
        title='先开启群问答'
        description='回放测试会拿这个群的真实历史问题，按当前问答设置跑一遍，对比不同检索方式的效果。'
        action={<Button onClick={onSetup}>去开启群问答</Button>}
      />
    )
  const active = selected || batches[0]?.id
  return (
    <div className='space-y-4'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <p className='max-w-xl text-sm text-muted-foreground'>
          挑几条群里的真实历史问题，按当前设置重新跑一遍，对比不同检索方式。不会往群里发消息。
        </p>
        <Button onClick={() => setCreating(true)}>
          <Plus /> 新建回放
        </Button>
      </div>
      {batches.length === 0 ? (
        <EmptyState
          title='还没有回放记录'
          description='点“新建回放”选几条历史问题试试。'
        />
      ) : (
        <div className='grid gap-4 xl:grid-cols-[220px_minmax(0,1fr)]'>
          <nav className='space-y-1' aria-label='回放记录'>
            {batches.map((batch) => (
              <button
                key={batch.id}
                onClick={() => setSelected(batch.id)}
                aria-current={batch.id === active}
                className={cn(
                  'w-full rounded-lg border px-3 py-2 text-left text-sm',
                  batch.id === active
                    ? 'border-primary bg-primary/5'
                    : 'border-transparent hover:bg-muted'
                )}
              >
                <span className='block'>{formatTime(batch.created_at)}</span>
                <span className='text-xs text-muted-foreground'>
                  {batch.done}/{batch.total} · 回答 {batch.replies} ·{' '}
                  {batchStates[batch.status] ?? batch.status}
                </span>
              </button>
            ))}
          </nav>
          {active && <BatchResults key={active} id={active} onRun={setRunId} />}
        </div>
      )}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className='flex max-h-[90vh] flex-col sm:max-w-2xl'>
          <DialogHeader>
            <DialogTitle>新建回放</DialogTitle>
            <DialogDescription>
              每条问题按当时的消息顺序读取前文，走和实时回复相同的判断、检索与核验流程。
            </DialogDescription>
          </DialogHeader>
          {creating && (
            <CasesLoader
              account={account}
              room={rule.room}
              onCreated={(batch) => {
                setCreating(false)
                setSelected(batch.id)
                void client.invalidateQueries({ queryKey: ['group-replies'] })
              }}
            />
          )}
        </DialogContent>
      </Dialog>
      <RunDetailSheet id={runId} onClose={() => setRunId('')} />
    </div>
  )
}

function CasesLoader({
  account,
  room,
  onCreated,
}: {
  account: string
  room: string
  onCreated: (batch: BatchSummary) => void
}) {
  const cases = useQuery({
    queryKey: ['reply-cases', account, room],
    queryFn: () => replyApi.cases(account, room),
  })
  if (cases.error) return <ErrorText error={cases.error} />
  if (!cases.data)
    return <p className='text-sm text-muted-foreground'>正在读取历史问题…</p>
  return (
    <ReplayForm
      account={account}
      room={room}
      cases={cases.data.items}
      onCreated={onCreated}
    />
  )
}

function ReplayForm({
  account,
  room,
  cases,
  onCreated,
}: {
  account: string
  room: string
  cases: Case[]
  onCreated: (batch: BatchSummary) => void
}) {
  const [selected, setSelected] = useState<number[]>(() => {
    const categories = new Set<string>()
    return cases
      .filter((item) => {
        if (categories.has(item.category)) return false
        categories.add(item.category)
        return true
      })
      .map((item) => item.message_id)
  })
  const [filter, setFilter] = useState('')
  const [manualIds, setManualIds] = useState('')
  const [modes, setModes] = useState<SearchMode[]>(['bwiki', 'deepseek', 'gpt'])
  const [triggers, setTriggers] = useState<Trigger[]>(['original', 'ask'])
  const [requestId, setRequestId] = useState(newRequestId)
  const changed = () => setRequestId(newRequestId())
  const toggle = <T,>(list: T[], value: T, on: boolean) =>
    on ? [...list, value] : list.filter((item) => item !== value)
  const ids = [
    ...new Set([
      ...selected,
      ...manualIds
        .split(/[\s,，]+/)
        .filter(Boolean)
        .map(Number),
    ]),
  ]
  const run = useMutation({
    mutationFn: () =>
      replyApi.replay({
        account,
        room,
        message_ids: ids,
        search_modes: modes,
        trigger_modes: triggers,
        request_id: requestId,
      }),
    onSuccess: onCreated,
  })
  const visible = cases.filter(
    (item) => !filter || item.text.includes(filter) || item.category === filter
  )
  return (
    <form
      className='flex min-h-0 flex-1 flex-col gap-5'
      onSubmit={(event) => {
        event.preventDefault()
        run.mutate()
      }}
    >
      <div className='space-y-2'>
        <Label>对比哪些检索方式</Label>
        <div className='flex flex-wrap gap-2'>
          {(Object.entries(modeNames) as [SearchMode, string][]).map(
            ([mode, name]) => (
              <ChipToggle
                key={mode}
                checked={modes.includes(mode)}
                onChange={(on) => {
                  setModes(toggle(modes, mode, on))
                  changed()
                }}
              >
                {name}
              </ChipToggle>
            )
          )}
        </div>
      </div>
      <div className='space-y-2'>
        <Label>触发方式</Label>
        <div className='flex flex-wrap gap-2'>
          {(Object.entries(triggerNames) as [Trigger, string][]).map(
            ([trigger, name]) => (
              <ChipToggle
                key={trigger}
                checked={triggers.includes(trigger)}
                onChange={(on) => {
                  setTriggers(toggle(triggers, trigger, on))
                  changed()
                }}
              >
                {name}
              </ChipToggle>
            )
          )}
        </div>
      </div>
      <div className='flex min-h-0 flex-1 flex-col gap-2'>
        <div className='flex flex-wrap items-center gap-2'>
          <Label className='mr-auto'>
            历史问题（已选 {selected.length} / {cases.length}）
          </Label>
          <Button
            type='button'
            size='sm'
            variant='ghost'
            onClick={() => {
              setSelected(cases.map((item) => item.message_id))
              changed()
            }}
          >
            全选
          </Button>
          <Button
            type='button'
            size='sm'
            variant='ghost'
            onClick={() => {
              setSelected([])
              changed()
            }}
          >
            清空
          </Button>
        </div>
        <Input
          aria-label='筛选历史问题'
          placeholder='按内容或类别筛选'
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <div className='min-h-32 flex-1 space-y-1 overflow-auto rounded-lg border p-1'>
          {visible.length === 0 && (
            <p className='p-3 text-sm text-muted-foreground'>
              没有匹配的历史问题
            </p>
          )}
          {visible.map((item) => (
            <label
              key={item.message_id}
              className='flex items-start gap-3 rounded p-2 text-sm hover:bg-muted/50'
            >
              <input
                type='checkbox'
                className='mt-1'
                checked={selected.includes(item.message_id)}
                onChange={(event) => {
                  setSelected(
                    toggle(selected, item.message_id, event.target.checked)
                  )
                  changed()
                }}
              />
              <span className='min-w-0'>
                <span className='block break-words'>{item.text}</span>
                <span className='text-xs text-muted-foreground'>
                  {item.category} · {formatTime(item.timestamp)}
                </span>
              </span>
            </label>
          ))}
        </div>
        <Collapsible>
          <CollapsibleTrigger className='flex items-center gap-1 text-xs text-muted-foreground [&[data-state=open]>svg]:rotate-180'>
            按消息 ID 添加 <ChevronDown className='size-3' />
          </CollapsibleTrigger>
          <CollapsibleContent className='pt-2'>
            <Input
              aria-label='额外历史消息 ID'
              value={manualIds}
              onChange={(event) => {
                setManualIds(event.target.value)
                changed()
              }}
              placeholder='多个 ID 用逗号分隔'
            />
          </CollapsibleContent>
        </Collapsible>
      </div>
      <ErrorText error={run.error} />
      <DialogFooter>
        <span className='mr-auto self-center text-xs text-muted-foreground'>
          共 {ids.length * modes.length * triggers.length} 次运行
        </span>
        <Button
          type='submit'
          disabled={
            run.isPending || !modes.length || !triggers.length || !ids.length
          }
        >
          {run.isPending ? '创建中…' : '开始回放'}
        </Button>
      </DialogFooter>
    </form>
  )
}

function BatchResults({
  id,
  onRun,
}: {
  id: string
  onRun: (id: string) => void
}) {
  const query = useQuery({
    queryKey: ['reply-batch', id],
    queryFn: () => replyApi.batch(id),
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 3000 : false,
  })
  const [filter, setFilter] = useState<'all' | 'reply' | 'silent'>('all')
  const cancel = useMutation({
    mutationFn: () => replyApi.cancel(id),
    onSuccess: () => query.refetch(),
  })
  if (query.error) return <ErrorText error={query.error} />
  if (!query.data)
    return <p className='text-sm text-muted-foreground'>加载回放结果…</p>
  const grouped = new Map<string, RunSummary[]>()
  for (const run of query.data.runs) {
    if (filter === 'reply' && !run.should_reply) continue
    if (filter === 'silent' && (run.should_reply || run.status !== 'completed'))
      continue
    const key = `${run.message_id}:${run.trigger_mode}`
    grouped.set(key, [...(grouped.get(key) ?? []), run])
  }
  const finished = query.data.runs.filter((run) =>
    ['completed', 'cancelled'].includes(run.status)
  ).length
  return (
    <div className='min-w-0 space-y-4'>
      <div className='flex flex-wrap items-center gap-3'>
        <span className='mr-auto text-sm'>
          已处理 {finished} / {query.data.total}
        </span>
        <div className='flex gap-1'>
          {(
            [
              ['all', '全部'],
              ['reply', '会回答'],
              ['silent', '不回答'],
            ] as const
          ).map(([value, label]) => (
            <ChipToggle
              key={value}
              checked={filter === value}
              onChange={() => setFilter(value)}
            >
              {label}
            </ChipToggle>
          ))}
        </div>
        {query.data.status === 'running' && (
          <Button
            size='sm'
            variant='outline'
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            停止
          </Button>
        )}
      </div>
      {grouped.size === 0 && (
        <p className='text-sm text-muted-foreground'>没有符合筛选的结果</p>
      )}
      {[...grouped.entries()].map(([key, runs]) => (
        <article key={key} className='space-y-3 rounded-xl border p-4'>
          <div className='flex items-start gap-2'>
            <Badge variant='outline'>
              {triggerNames[runs[0].trigger_mode]}
            </Badge>
            <p className='min-w-0 font-medium break-words whitespace-pre-wrap'>
              {runs[0].input}
            </p>
          </div>
          <div className='grid gap-3 lg:grid-cols-2 2xl:grid-cols-3'>
            {runs.map((run) => (
              <button
                key={run.id}
                onClick={() => onRun(run.id)}
                className='space-y-2 rounded-lg bg-muted/40 p-3 text-left hover:bg-muted'
              >
                <span className='flex items-center justify-between gap-2'>
                  <span className='text-sm font-medium'>
                    {modeNames[run.search_mode]}
                  </span>
                  <RunBadge run={run} />
                </span>
                <span className='line-clamp-6 block text-sm break-words whitespace-pre-wrap'>
                  {run.answer ?? (
                    <span className='text-muted-foreground'>
                      {reasonText(run.reason)}
                    </span>
                  )}
                </span>
                {run.elapsed_ms != null && (
                  <span className='block text-xs text-muted-foreground'>
                    {(run.elapsed_ms / 1000).toFixed(1)} 秒 · 点击查看详情
                  </span>
                )}
              </button>
            ))}
          </div>
        </article>
      ))}
    </div>
  )
}

function RunBadge({ run }: { run: RunSummary }) {
  if (run.status === 'completed')
    return (
      <Badge variant={run.should_reply ? 'default' : 'secondary'}>
        {run.should_reply ? '会回答' : '不回答'}
      </Badge>
    )
  return (
    <Badge variant='outline'>
      {run.status === 'running'
        ? '处理中'
        : run.status === 'cancelled'
          ? '已停止'
          : '等待中'}
    </Badge>
  )
}
