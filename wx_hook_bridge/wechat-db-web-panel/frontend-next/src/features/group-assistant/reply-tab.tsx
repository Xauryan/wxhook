import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, ChevronDown, Settings2, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
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
import type { ModelProfile } from '@/features/ai/api'
import {
  modeNames,
  replyApi,
  type ReplyConfig,
  type ReplyRule,
  type ReplyStatus,
  type SearchMode,
} from '@/features/group-replies/api'
import { PipelineSettings } from '@/features/group-replies/pipeline-settings'
import { RunDetailSheet } from './run-detail'
import { EmptyState, ErrorText, FieldGroup, Panel } from './shared'
import {
  formatTime,
  profileName,
  selectStyle,
  textProfiles,
  reasonText,
} from './utils'

type Props = {
  account: string
  room: string
  rule?: ReplyRule
  recent: ReplyStatus['recent']
  prompts: ReplyStatus['prompts']
  profiles: ModelProfile[]
}

export function ReplyTab({
  account,
  room,
  rule,
  recent,
  prompts,
  profiles,
}: Props) {
  const client = useQueryClient()
  const [settings, setSettings] = useState(false)
  const [runId, setRunId] = useState('')
  const toggle = useMutation({
    mutationFn: (patch: Partial<ReplyConfig>) =>
      replyApi.save({
        account,
        room,
        revision: rule!.revision,
        config: { ...rule!.config, ...patch },
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['group-replies'] }),
  })
  const sheet = (
    <ReplySettings
      key={`${room}:${rule?.revision ?? 0}`}
      open={settings}
      onOpenChange={setSettings}
      account={account}
      room={room}
      existing={rule}
      prompts={prompts}
      profiles={profiles}
    />
  )
  if (!rule)
    return (
      <>
        <EmptyState
          title='这个群还没开启群问答'
          description='AI 会识别群里的资料类问题，检索并核对来源后回答。群友发 /ask 开头的消息一定会得到回应。'
          action={<Button onClick={() => setSettings(true)}>开启群问答</Button>}
        />
        {sheet}
      </>
    )
  const config = rule.config
  const noDecision = !config.decision_profile_ids?.length
  return (
    <div className='space-y-4'>
      <Panel
        action={
          <Button variant='outline' onClick={() => setSettings(true)}>
            <Settings2 /> 设置
          </Button>
        }
        title={
          <span className='flex flex-wrap items-center gap-x-6 gap-y-3'>
            <span className='flex items-center gap-3'>
              <Switch
                id='reply-enabled-inline'
                checked={config.enabled}
                disabled={toggle.isPending}
                onCheckedChange={(enabled) => toggle.mutate({ enabled })}
              />
              <Label htmlFor='reply-enabled-inline' className='text-base'>
                在群里真实回复
              </Label>
            </span>
            <span className='flex items-center gap-3'>
              <Switch
                id='reply-proactive-inline'
                checked={config.proactive}
                disabled={toggle.isPending}
                onCheckedChange={(proactive) => toggle.mutate({ proactive })}
              />
              <Label htmlFor='reply-proactive-inline' className='text-base'>
                主动回答普通提问
              </Label>
            </span>
          </span>
        }
      >
        <p className='text-sm text-muted-foreground'>
          {profileName(profiles, config.ai_profile_id)} 回答 ·{' '}
          {modeNames[config.search_mode]} 检索 · 读取前 {config.context_minutes}{' '}
          分钟内最多 {config.context_messages} 条消息
        </p>
        {!config.enabled && (
          <p className='text-sm text-muted-foreground'>
            关闭真实回复时只记录、不发送，可以在“回放测试”里先验证效果。
          </p>
        )}
        {config.proactive && noDecision && (
          <p className='rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm'>
            还没设置消息过滤模型，目前只会回应 /ask
            开头的消息。在设置里添加后才能主动回答普通提问。
          </p>
        )}
        <ErrorText error={toggle.error} />
      </Panel>

      <Panel
        title='最近处理'
        description='群里新消息的处理结果，点开可查看模型读到的前文和引用资料。'
      >
        {recent.length === 0 ? (
          <p className='text-sm text-muted-foreground'>
            {config.enabled
              ? '暂时没有新消息被处理。'
              : '开启真实回复后，这里会记录新消息的处理结果。'}
          </p>
        ) : (
          <ul className='divide-y'>
            {recent.map((run) => (
              <li key={run.id}>
                <button
                  className='flex w-full items-center gap-3 py-3 text-left hover:bg-muted/50'
                  onClick={() => setRunId(run.id)}
                >
                  <Badge
                    variant={run.answer ? 'default' : 'secondary'}
                    className='w-16'
                  >
                    {run.answer ? '已回答' : '未回答'}
                  </Badge>
                  <span className='min-w-0 flex-1'>
                    <span className='block truncate text-sm'>{run.input}</span>
                    <span className='text-xs text-muted-foreground'>
                      {reasonText(run.reason)}
                      {run.delivery_status && ` · ${run.delivery_status}`}
                    </span>
                  </span>
                  <span className='shrink-0 text-xs text-muted-foreground'>
                    {formatTime(run.created_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <RunDetailSheet id={runId} onClose={() => setRunId('')} />
      {sheet}
    </div>
  )
}

function ReplySettings({
  open,
  onOpenChange,
  account,
  room,
  existing,
  prompts,
  profiles,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  account: string
  room: string
  existing?: ReplyRule
  prompts: ReplyStatus['prompts']
  profiles: ModelProfile[]
}) {
  const client = useQueryClient()
  const models = textProfiles(profiles)
  const [config, setConfig] = useState<ReplyConfig>(
    existing?.config ?? {
      enabled: false,
      proactive: true,
      ai_profile_id: models[0]?.id ?? '',
      decision_profile_ids: [],
      decision_skip_threshold: 0.9,
      search_mode: 'hybrid',
      context_messages: 24,
      context_minutes: 15,
      cooldown_seconds: 60,
      confidence_threshold: 0.9,
      max_reply_chars: 1200,
    }
  )
  const update = <K extends keyof ReplyConfig>(key: K, value: ReplyConfig[K]) =>
    setConfig((previous) => ({ ...previous, [key]: value }))
  const decisions = config.decision_profile_ids ?? []
  const move = (index: number, offset: number) => {
    const next = [...decisions]
    ;[next[index], next[index + offset]] = [next[index + offset], next[index]]
    update('decision_profile_ids', next)
  }
  const save = useMutation({
    mutationFn: () =>
      replyApi.save({
        account,
        room,
        revision: existing?.revision ?? 0,
        config,
      }),
    onSuccess: () => {
      onOpenChange(false)
      void client.invalidateQueries({ queryKey: ['group-replies'] })
    },
  })
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className='w-full gap-0 sm:max-w-2xl'>
        <SheetHeader className='border-b'>
          <SheetTitle>群问答设置</SheetTitle>
          <SheetDescription>
            常用设置在上面，阶段模型、检索参数和提示词收在“高级”里。
          </SheetDescription>
        </SheetHeader>
        <form
          id='reply-settings'
          className='flex-1 space-y-8 overflow-y-auto p-4'
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          <FieldGroup title='基本'>
            <div className='flex items-center gap-3'>
              <Switch
                id='reply-enabled'
                checked={config.enabled}
                onCheckedChange={(value) => update('enabled', value)}
              />
              <Label htmlFor='reply-enabled'>在群里真实回复</Label>
            </div>
            <div className='flex items-center gap-3'>
              <Switch
                id='reply-proactive'
                checked={config.proactive}
                onCheckedChange={(value) => update('proactive', value)}
              />
              <Label htmlFor='reply-proactive'>
                普通提问有明确依据时主动回答
              </Label>
            </div>
            <div className='grid gap-4 sm:grid-cols-2'>
              <div className='space-y-2'>
                <Label htmlFor='reply-model'>回答模型</Label>
                <select
                  id='reply-model'
                  className={selectStyle}
                  value={config.ai_profile_id}
                  onChange={(event) =>
                    update('ai_profile_id', event.target.value)
                  }
                  required
                >
                  <option value=''>选择模型</option>
                  {profiles
                    .filter((profile) => !profile.decision_only)
                    .map((profile) => (
                      <option
                        key={profile.id}
                        value={profile.id}
                        disabled={!profile.config.enabled}
                      >
                        {profile.config.name}
                      </option>
                    ))}
                </select>
              </div>
              <div className='space-y-2'>
                <Label htmlFor='reply-mode'>资料检索方式</Label>
                <select
                  id='reply-mode'
                  className={selectStyle}
                  value={config.search_mode}
                  onChange={(event) =>
                    update('search_mode', event.target.value as SearchMode)
                  }
                >
                  {Object.entries(modeNames).map(([value, name]) => (
                    <option value={value} key={value}>
                      {name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <p className='text-xs text-muted-foreground'>
              开启真实回复后 /ask
              始终回应，缺少依据时会说明无法确认或请求补充；普通提问要通过来源和答案核验才会回答。
            </p>
          </FieldGroup>

          <FieldGroup
            title='消息过滤'
            description='先用过滤模型判断普通消息是不是求助，过滤掉闲聊。按顺序尝试，前一个连接故障时自动换下一个。/ask 消息不经过这一步。'
          >
            {decisions.length > 0 && (
              <ol className='space-y-2'>
                {decisions.map((id, index) => (
                  <li
                    key={id}
                    className='flex items-center gap-2 rounded-lg border px-3 py-2 text-sm'
                  >
                    <span className='w-5 text-muted-foreground'>
                      {index + 1}
                    </span>
                    <span className='mr-auto truncate'>
                      {profileName(profiles, id)}
                    </span>
                    <Button
                      type='button'
                      size='icon'
                      variant='ghost'
                      aria-label='上移'
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                    >
                      <ArrowUp />
                    </Button>
                    <Button
                      type='button'
                      size='icon'
                      variant='ghost'
                      aria-label='下移'
                      disabled={index === decisions.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      <ArrowDown />
                    </Button>
                    <Button
                      type='button'
                      size='icon'
                      variant='ghost'
                      aria-label='移除'
                      onClick={() =>
                        update(
                          'decision_profile_ids',
                          decisions.filter((value) => value !== id)
                        )
                      }
                    >
                      <X />
                    </Button>
                  </li>
                ))}
              </ol>
            )}
            <div className='grid gap-4 sm:grid-cols-2'>
              <div className='space-y-2'>
                <Label htmlFor='reply-decision-add'>添加过滤模型</Label>
                <select
                  id='reply-decision-add'
                  className={selectStyle}
                  value=''
                  disabled={decisions.length >= 10}
                  onChange={(event) => {
                    if (event.target.value)
                      update('decision_profile_ids', [
                        ...decisions,
                        event.target.value,
                      ])
                  }}
                >
                  <option value=''>从 AI 接入中选择</option>
                  {profiles
                    .filter(
                      (profile) =>
                        profile.config.enabled &&
                        !decisions.includes(profile.id)
                    )
                    .map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.config.name}
                        {profile.decision_only ? '' : '（文本分类）'}
                      </option>
                    ))}
                </select>
              </div>
              <div className='space-y-2'>
                <Label htmlFor='reply-decision-threshold'>
                  判定为闲聊的概率阈值
                </Label>
                <Input
                  id='reply-decision-threshold'
                  type='number'
                  min={0.5}
                  max={1}
                  step={0.01}
                  value={config.decision_skip_threshold ?? 0.9}
                  onChange={(event) =>
                    update(
                      'decision_skip_threshold',
                      Number(event.target.value)
                    )
                  }
                />
              </div>
            </div>
          </FieldGroup>

          <Collapsible>
            <CollapsibleTrigger className='flex w-full items-center justify-between rounded-lg border px-3 py-2 text-sm font-medium [&[data-state=open]>svg]:rotate-180'>
              高级：阶段模型、检索参数与提示词{' '}
              <ChevronDown className='size-4' />
            </CollapsibleTrigger>
            <CollapsibleContent className='space-y-4 pt-4'>
              <div className='grid gap-4 sm:grid-cols-3'>
                {(
                  [
                    ['planning_profile_id', '问题解析模型'],
                    ['review_profile_id', '答案核验模型'],
                    ['search_profile_id', 'GPT 搜索模型'],
                  ] as const
                ).map(([key, label]) => (
                  <div key={key} className='space-y-2'>
                    <Label htmlFor={`reply-${key}`}>{label}</Label>
                    <select
                      id={`reply-${key}`}
                      className={selectStyle}
                      value={config[key] ?? ''}
                      onChange={(event) =>
                        update(key, event.target.value || null)
                      }
                    >
                      <option value=''>跟随回答模型</option>
                      {models.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profile.config.name}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
              <PipelineSettings
                config={config}
                prompts={prompts}
                onChange={setConfig}
              />
            </CollapsibleContent>
          </Collapsible>
        </form>
        <SheetFooter className='border-t'>
          <ErrorText error={save.error} />
          <div className='flex gap-2'>
            <Button
              type='submit'
              form='reply-settings'
              disabled={save.isPending || !config.ai_profile_id}
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
