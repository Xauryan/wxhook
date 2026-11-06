import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Settings2 } from 'lucide-react'
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
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import type { ModelProfile } from '@/features/ai/api'
import {
  reportApi,
  type ReportConfig,
  type ReportJob,
  type ReportRule,
  type ReportStatus,
} from '@/features/group-reports/api'
import { EmptyState, ErrorText, FieldGroup, Panel } from './shared'
import {
  formatTime,
  newRequestId,
  profileName,
  selectStyle,
  textProfiles,
} from './utils'

const states: Record<string, string> = {
  queued: '等待生成',
  preparing: '读取记录',
  analyzing: 'AI 分析',
  rendering: '渲染报告',
  ready: '待发送',
  sending: '提交微信中',
  accepted: '已发送',
  unknown: '发送待核对',
  failed: '生成失败',
  skipped: '消息不足',
}
const formats: Record<ReportConfig['output_format'], string> = {
  image: '图片',
  text: '文字',
  pdf: 'PDF',
}
const skins: Record<ReportConfig['skin'], string> = {
  md3: 'Material Design 3',
  anime: '二次元',
  newspaper: '报纸',
  art: '艺术',
  scrapbook: '手账',
}
const ranges = [
  { value: '24', label: '最近 24 小时' },
  { value: '12', label: '最近 12 小时' },
  { value: '3', label: '最近 3 小时' },
  { value: 'custom', label: '自定义时间段' },
]
const localInput = (milliseconds: number) =>
  new Date(milliseconds + 8 * 3600000).toISOString().slice(0, 16)

type Props = {
  account: string
  room: string
  name: string
  rule?: ReportRule
  jobs: ReportJob[]
  prompts: ReportStatus['prompts']
  profiles: ModelProfile[]
}

export function ReportTab({
  account,
  room,
  name,
  rule,
  jobs,
  prompts,
  profiles,
}: Props) {
  const client = useQueryClient()
  const [settings, setSettings] = useState(false)
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      reportApi.save({
        account,
        room,
        revision: rule!.revision,
        config: { ...rule!.config, enabled },
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['group-reports'] }),
  })
  const sheet = (
    <ReportSettings
      key={`${room}:${rule?.revision ?? 0}`}
      open={settings}
      onOpenChange={setSettings}
      account={account}
      room={room}
      name={name}
      existing={rule}
      prompts={prompts}
      profiles={profiles}
    />
  )
  if (!rule)
    return (
      <>
        <EmptyState
          title='这个群还没开启群日报'
          description='AI 会按时段总结群聊天，提炼话题、金句和群友称号，可以定时发到群里，也可以随时手动生成。'
          action={<Button onClick={() => setSettings(true)}>开启群日报</Button>}
        />
        {sheet}
      </>
    )
  const config = rule.config
  return (
    <div className='space-y-4'>
      <Panel
        title={
          <span className='flex items-center gap-3'>
            <Switch
              id='report-enabled-inline'
              checked={config.enabled}
              disabled={toggle.isPending}
              onCheckedChange={(value) => toggle.mutate(value)}
            />
            <Label htmlFor='report-enabled-inline' className='text-base'>
              每日定时发送
            </Label>
          </span>
        }
        action={
          <Button variant='outline' onClick={() => setSettings(true)}>
            <Settings2 /> 设置
          </Button>
        }
      >
        <dl className='grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2'>
          <Summary label='生成时间'>
            每天 {config.schedule_time}，统计之前 {config.window_hours} 小时
          </Summary>
          <Summary label='下次生成'>
            {config.enabled ? formatTime(rule.next_run) : '定时已暂停'}
          </Summary>
          <Summary label='报告样式'>
            {formats[config.output_format]} · {skins[config.skin]}
          </Summary>
          <Summary label='模型'>
            {profileName(profiles, config.ai_profile_id)}
          </Summary>
          <Summary label='群指令'>
            {config.manual_enabled
              ? `已开启，${config.admin_ids.length} 名管理员可用`
              : '未开启'}
          </Summary>
        </dl>
        <ErrorText error={toggle.error} />
      </Panel>
      <GeneratePanel account={account} room={room} />
      <JobList jobs={jobs} />
      {sheet}
    </div>
  )
}

function Summary({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className='flex gap-3'>
      <dt className='w-16 shrink-0 text-muted-foreground'>{label}</dt>
      <dd className='min-w-0'>{children}</dd>
    </div>
  )
}

function GeneratePanel({ account, room }: { account: string; room: string }) {
  const client = useQueryClient()
  const [range, setRange] = useState('24')
  const [start, setStart] = useState(() => localInput(Date.now() - 86400000))
  const [end, setEnd] = useState(() => localInput(Date.now()))
  const [send, setSend] = useState(false)
  const [requestId, setRequestId] = useState(newRequestId)
  const changed = () => setRequestId(newRequestId())
  const run = useMutation({
    mutationFn: () => {
      const now = Date.now()
      const [from, to] =
        range === 'custom'
          ? [start, end]
          : [localInput(now - Number(range) * 3600000), localInput(now)]
      return reportApi.run(
        account,
        room,
        `${from}:00+08:00`,
        `${to}:00+08:00`,
        send,
        requestId
      )
    },
    onSuccess: () => {
      changed()
      void client.invalidateQueries({ queryKey: ['group-reports'] })
    },
  })
  return (
    <Panel title='立即生成一份'>
      <form
        className='flex flex-wrap items-end gap-3'
        onSubmit={(event) => {
          event.preventDefault()
          run.mutate()
        }}
      >
        <div className='w-40 space-y-2'>
          <Label htmlFor='report-range'>时间范围</Label>
          <select
            id='report-range'
            className={selectStyle}
            value={range}
            onChange={(event) => {
              setRange(event.target.value)
              changed()
            }}
          >
            {ranges.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
        {range === 'custom' && (
          <>
            <div className='space-y-2'>
              <Label htmlFor='report-start'>开始（北京时间）</Label>
              <Input
                id='report-start'
                type='datetime-local'
                required
                value={start}
                onChange={(event) => {
                  setStart(event.target.value)
                  changed()
                }}
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='report-end'>结束（北京时间）</Label>
              <Input
                id='report-end'
                type='datetime-local'
                required
                value={end}
                onChange={(event) => {
                  setEnd(event.target.value)
                  changed()
                }}
              />
            </div>
          </>
        )}
        <label className='flex h-9 items-center gap-2 text-sm'>
          <input
            type='checkbox'
            checked={send}
            onChange={(event) => {
              setSend(event.target.checked)
              changed()
            }}
          />
          生成后直接发到群里
        </label>
        <Button type='submit' disabled={run.isPending}>
          {run.isPending ? '提交中…' : send ? '生成并发送' : '生成预览'}
        </Button>
      </form>
      <ErrorText error={run.error} />
      {run.isSuccess && (
        <p role='status' className='text-sm text-muted-foreground'>
          已开始生成，进度见下方列表。
        </p>
      )}
    </Panel>
  )
}

function JobList({ jobs }: { jobs: ReportJob[] }) {
  const client = useQueryClient()
  const [preview, setPreview] = useState<ReportJob | null>(null)
  const [notice, setNotice] = useState('')
  const send = useMutation({
    mutationFn: reportApi.send,
    onSuccess: (result) => {
      setNotice(result.detail)
      void client.invalidateQueries({ queryKey: ['group-reports'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  return (
    <Panel title='报告记录'>
      {notice && (
        <p role='status' className='rounded-lg bg-muted p-3 text-sm'>
          {notice}
        </p>
      )}
      {jobs.length === 0 ? (
        <p className='text-sm text-muted-foreground'>
          还没有报告。可以先用上面的“生成预览”看看效果。
        </p>
      ) : (
        <ul className='divide-y'>
          {jobs.map((job) => {
            const done = ['ready', 'accepted', 'unknown'].includes(job.status)
            return (
              <li
                key={job.id}
                className='flex flex-wrap items-center gap-x-4 gap-y-2 py-3'
              >
                <Badge
                  variant={
                    job.status === 'failed'
                      ? 'destructive'
                      : job.status === 'ready'
                        ? 'default'
                        : 'secondary'
                  }
                >
                  {states[job.status] ?? job.status}
                </Badge>
                <div className='min-w-0 flex-1'>
                  <p className='text-sm'>
                    {formatTime(job.start)} 至 {formatTime(job.end)}
                  </p>
                  {job.detail && (
                    <p className='truncate text-xs text-muted-foreground'>
                      {job.detail}
                    </p>
                  )}
                </div>
                {done && (
                  <div className='flex gap-2'>
                    <Button
                      size='sm'
                      variant='outline'
                      onClick={() => setPreview(job)}
                    >
                      预览
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button size='sm' variant='outline'>
                          下载 <ChevronDown />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align='end'>
                        {(
                          [
                            ['png', '图片'],
                            ['pdf', 'PDF'],
                            ['txt', '文字'],
                          ] as const
                        ).map(([type, label]) => (
                          <DropdownMenuItem key={type} asChild>
                            <a href={reportApi.file(job.id, type, true)}>
                              {label}
                            </a>
                          </DropdownMenuItem>
                        ))}
                      </DropdownMenuContent>
                    </DropdownMenu>
                    {job.status === 'ready' && (
                      <Button
                        size='sm'
                        disabled={send.isPending}
                        onClick={() => send.mutate(job.id)}
                      >
                        发到群里
                      </Button>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      <Dialog
        open={!!preview}
        onOpenChange={(open) => !open && setPreview(null)}
      >
        <DialogContent className='max-h-[90vh] overflow-y-auto sm:max-w-3xl'>
          <DialogHeader>
            <DialogTitle>报告预览</DialogTitle>
          </DialogHeader>
          {preview && (
            <img
              className='mx-auto max-w-full rounded-lg border'
              src={reportApi.file(preview.id, 'png')}
              alt={`${preview.name} 群日报预览`}
            />
          )}
        </DialogContent>
      </Dialog>
    </Panel>
  )
}

function ReportSettings({
  open,
  onOpenChange,
  account,
  room,
  name,
  existing,
  prompts,
  profiles,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  account: string
  room: string
  name: string
  existing?: ReportRule
  prompts: ReportStatus['prompts']
  profiles: ModelProfile[]
}) {
  const client = useQueryClient()
  const models = textProfiles(profiles)
  const [config, setConfig] = useState<ReportConfig>(
    existing?.config ?? {
      enabled: false,
      ai_profile_id: models[0]?.id ?? '',
      schedule_time: '01:00',
      window_hours: 24,
      output_format: 'image',
      skin: 'md3',
      theme: 'auto',
      manual_enabled: false,
      admin_ids: [],
      max_messages: 2000,
      min_messages: 10,
      max_topics: 5,
      max_quotes: 3,
      max_titles: 6,
      prompt_topics: prompts.topics,
      prompt_titles: prompts.titles,
      prompt_quotes: prompts.quotes,
    }
  )
  const update = <K extends keyof ReportConfig>(
    field: K,
    value: ReportConfig[K]
  ) => setConfig((current) => ({ ...current, [field]: value }))
  const [memberSearch, setMemberSearch] = useState('')
  const members = useQuery({
    queryKey: ['report-members', account, room],
    queryFn: () => reportApi.members(account, room),
    enabled: open && config.manual_enabled,
  })
  const save = useMutation({
    mutationFn: () =>
      reportApi.save({
        account,
        room,
        revision: existing?.revision ?? 0,
        config,
      }),
    onSuccess: () => {
      onOpenChange(false)
      void client.invalidateQueries({ queryKey: ['group-reports'] })
    },
  })
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className='w-full gap-0 sm:max-w-xl'>
        <SheetHeader className='border-b'>
          <SheetTitle>群日报设置</SheetTitle>
          <SheetDescription>{name}</SheetDescription>
        </SheetHeader>
        <form
          id='report-settings'
          className='flex-1 space-y-8 overflow-y-auto p-4'
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          <FieldGroup title='定时生成'>
            <div className='flex items-center gap-3'>
              <Switch
                id='report-enabled'
                checked={config.enabled}
                onCheckedChange={(value) => update('enabled', value)}
              />
              <Label htmlFor='report-enabled'>每天定时生成并发到群里</Label>
            </div>
            <div className='grid gap-4 sm:grid-cols-2'>
              <div className='space-y-2'>
                <Label htmlFor='report-time'>每天几点生成</Label>
                <Input
                  id='report-time'
                  type='time'
                  required
                  value={config.schedule_time}
                  onChange={(e) => update('schedule_time', e.target.value)}
                />
              </div>
              <div className='space-y-2'>
                <Label htmlFor='report-hours'>统计之前多少小时</Label>
                <Input
                  id='report-hours'
                  type='number'
                  required
                  min={1}
                  max={168}
                  value={config.window_hours}
                  onChange={(e) =>
                    update('window_hours', Number(e.target.value))
                  }
                />
              </div>
              <div className='space-y-2 sm:col-span-2'>
                <Label htmlFor='report-model'>AI 模型</Label>
                <select
                  id='report-model'
                  required
                  className={selectStyle}
                  value={config.ai_profile_id}
                  onChange={(e) => update('ai_profile_id', e.target.value)}
                >
                  <option value=''>选择模型配置</option>
                  {models.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.config.name} · {profile.config.model}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <p className='text-xs text-muted-foreground'>
              所选时段的群消息会提交给这个模型。错过定时后恢复运行，会按原定区间补发最近一期。
            </p>
          </FieldGroup>

          <FieldGroup title='报告样式'>
            <div className='grid gap-4 sm:grid-cols-3'>
              <div className='space-y-2'>
                <Label htmlFor='report-format'>发送格式</Label>
                <select
                  id='report-format'
                  className={selectStyle}
                  value={config.output_format}
                  onChange={(e) =>
                    update(
                      'output_format',
                      e.target.value as ReportConfig['output_format']
                    )
                  }
                >
                  {Object.entries(formats).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <div className='space-y-2'>
                <Label htmlFor='report-skin'>皮肤</Label>
                <select
                  id='report-skin'
                  className={selectStyle}
                  value={config.skin}
                  onChange={(e) =>
                    update('skin', e.target.value as ReportConfig['skin'])
                  }
                >
                  {Object.entries(skins).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <div className='space-y-2'>
                <Label htmlFor='report-theme'>配色</Label>
                <select
                  id='report-theme'
                  className={selectStyle}
                  value={config.theme}
                  onChange={(e) =>
                    update('theme', e.target.value as ReportConfig['theme'])
                  }
                >
                  <option value='auto'>按时间自动</option>
                  <option value='light'>亮色</option>
                  <option value='dark'>暗色</option>
                </select>
              </div>
            </div>
          </FieldGroup>

          <FieldGroup
            title='群指令'
            description='让指定成员在群里发“群日报”“群日报 最近3小时”“群日报 昨天”或“群日报 2026-11-05 01:00 至 2026-11-06 01:00”触发报告。每群冷却 5 分钟。'
          >
            <div className='flex items-center gap-3'>
              <Switch
                id='report-command'
                checked={config.manual_enabled}
                onCheckedChange={(value) => update('manual_enabled', value)}
              />
              <Label htmlFor='report-command'>允许管理员用群指令生成</Label>
            </div>
            {config.manual_enabled && (
              <div className='space-y-3 rounded-lg border p-3'>
                <Input
                  aria-label='搜索群成员'
                  placeholder='搜索群成员'
                  value={memberSearch}
                  onChange={(e) => setMemberSearch(e.target.value)}
                />
                <ErrorText error={members.error} />
                {members.isFetching && (
                  <p className='text-sm text-muted-foreground'>
                    正在核验群成员…
                  </p>
                )}
                <div className='grid max-h-56 gap-2 overflow-auto sm:grid-cols-2'>
                  {members.data?.items
                    .filter(
                      (member) =>
                        member.name.includes(memberSearch) ||
                        member.id.includes(memberSearch)
                    )
                    .map((member) => (
                      <label
                        key={member.id}
                        className='flex items-center gap-2 text-sm'
                      >
                        <input
                          type='checkbox'
                          checked={config.admin_ids.includes(member.id)}
                          onChange={(e) =>
                            update(
                              'admin_ids',
                              e.target.checked
                                ? [...config.admin_ids, member.id]
                                : config.admin_ids.filter(
                                    (id) => id !== member.id
                                  )
                            )
                          }
                        />
                        <span className='truncate'>{member.name}</span>
                      </label>
                    ))}
                </div>
                <p className='text-xs text-muted-foreground'>
                  已选择 {config.admin_ids.length} 人
                </p>
              </div>
            )}
          </FieldGroup>

          <Collapsible>
            <CollapsibleTrigger className='flex w-full items-center justify-between rounded-lg border px-3 py-2 text-sm font-medium [&[data-state=open]>svg]:rotate-180'>
              高级：分析范围与提示词 <ChevronDown className='size-4' />
            </CollapsibleTrigger>
            <CollapsibleContent className='space-y-4 pt-4'>
              <div className='grid gap-4 sm:grid-cols-3'>
                {(
                  [
                    ['max_messages', 'AI 分析消息上限', 100, 20000],
                    ['min_messages', '最少消息数', 1, 1000],
                    ['max_topics', '话题上限', 1, 15],
                    ['max_quotes', '金句上限', 0, 10],
                    ['max_titles', '称号上限', 0, 15],
                  ] as const
                ).map(([field, label, min, max]) => (
                  <div className='space-y-2' key={field}>
                    <Label htmlFor={`report-${field}`}>{label}</Label>
                    <Input
                      id={`report-${field}`}
                      type='number'
                      min={min}
                      max={max}
                      required
                      value={config[field]}
                      onChange={(e) => update(field, Number(e.target.value))}
                    />
                  </div>
                ))}
              </div>
              <p className='text-xs text-muted-foreground'>
                基础统计覆盖整个时段；消息超过上限时，AI 输入按时间均匀取样。
              </p>
              {(
                [
                  ['prompt_topics', '话题提示词', 'topics'],
                  ['prompt_titles', '称号提示词', 'titles'],
                  ['prompt_quotes', '金句提示词', 'quotes'],
                ] as const
              ).map(([field, label, source]) => (
                <div key={field} className='space-y-2'>
                  <div className='flex items-center justify-between gap-2'>
                    <Label htmlFor={field}>{label}</Label>
                    <Button
                      type='button'
                      variant='ghost'
                      size='sm'
                      onClick={() => update(field, prompts[source])}
                    >
                      恢复默认
                    </Button>
                  </div>
                  <Textarea
                    id={field}
                    className='min-h-40 font-mono text-xs'
                    required
                    value={config[field]}
                    onChange={(e) => update(field, e.target.value)}
                  />
                </div>
              ))}
            </CollapsibleContent>
          </Collapsible>
        </form>
        <SheetFooter className='border-t'>
          <ErrorText error={save.error} />
          <div className='flex gap-2'>
            <Button
              type='submit'
              form='report-settings'
              disabled={
                save.isPending ||
                !config.ai_profile_id ||
                (config.manual_enabled && !config.admin_ids.length)
              }
            >
              {save.isPending ? '保存中…' : '保存'}
            </Button>
            <Button
              type='button'
              variant='outline'
              onClick={() => onOpenChange(false)}
            >
              取消
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
