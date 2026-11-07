import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
import { Textarea } from '@/components/ui/textarea'
import type { ModelProfile } from '@/features/ai/api'
import {
  modeName,
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
          {modeName(config.search_mode)} 检索 · 读取前 {config.context_minutes}{' '}
          分钟内最多 {config.context_messages} 条消息
        </p>
        <p className='text-sm text-muted-foreground'>
          /ask 开头的消息一定会回应
          {config.mention_trigger === false
            ? ''
            : '；有人 @ 你或引用你的回答追问时，查到可靠答案才会回答'}
          {config.reply_style === 'plain' ? '' : '，回答会引用提问的那条消息'}。
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
                      {run.delivery_status &&
                        ` · ${deliveryText(run.delivery_status, run.delivery_detail)}`}
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
      {config.faq_enabled !== false && (
        <FaqPanel account={account} room={room} />
      )}
      <RunDetailSheet id={runId} onClose={() => setRunId('')} />
      {sheet}
    </div>
  )
}

const deliveries: Record<string, string> = {
  accepted: '已发送',
  dispatching: '发送中',
  unknown: '发送待核对',
  failed: '发送失败',
  cancelled: '配置已变，未发送',
}
function deliveryText(status: string, detail?: string) {
  if (status === 'skipped') return detail || '未发送'
  return deliveries[status] ?? status
}

function FaqPanel({ account, room }: { account: string; room: string }) {
  const client = useQueryClient()
  const faq = useQuery({
    queryKey: ['reply-faq', account, room],
    queryFn: () => replyApi.faq(account, room),
    refetchInterval: 30_000,
  })
  const remove = useMutation({
    mutationFn: (id: string) => replyApi.deleteFaq(account, id),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ['reply-faq', account, room] }),
  })
  const items = faq.data?.items ?? []
  return (
    <Panel
      title='常见问题库'
      description='核验通过的回答会存在这里。之后有人问同样的问题，会直接复用、秒回，不再重新搜索。答错的可以删掉。'
    >
      <ErrorText error={faq.error ?? remove.error} />
      {items.length === 0 ? (
        <p className='text-sm text-muted-foreground'>
          还没有收录的问题。群里有问题被成功回答后会自动加入。
        </p>
      ) : (
        <ul className='divide-y'>
          {items.map((item) => (
            <li key={item.id} className='flex items-start gap-3 py-3'>
              <div className='min-w-0 flex-1 space-y-1'>
                <p className='text-sm font-medium'>{item.question}</p>
                <p className='line-clamp-2 text-sm text-muted-foreground'>
                  {item.answer}
                </p>
                <p className='text-xs text-muted-foreground'>
                  {formatTime(item.created_at)} 收录 · 复用 {item.hits} 次
                  {item.time_sensitive && ' · 时效性问题，1 天后过期'}
                </p>
              </div>
              <Button
                size='sm'
                variant='ghost'
                className='text-destructive'
                disabled={remove.isPending}
                onClick={() => remove.mutate(item.id)}
              >
                删除
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
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
      search_mode: 'deepseek',
      context_messages: 24,
      context_minutes: 15,
      cooldown_seconds: 60,
      confidence_threshold: 0.9,
      max_reply_chars: 1200,
      mention_trigger: true,
      mention_names: [],
      reply_style: 'quote',
      fallback_profile_ids: [],
      fast_stages: true,
      stale_check: true,
      vision_profile_id: null,
      glossary: '',
      source_style: 'one_link',
      faq_enabled: true,
      faq_ttl_days: 7,
      strictness: 'balanced',
    }
  )
  const [names, setNames] = useState((config.mention_names ?? []).join('，'))
  const fallbacks = config.fallback_profile_ids ?? []
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
            <div className='space-y-2'>
              <Label htmlFor='reply-strictness'>回答严格程度</Label>
              <select
                id='reply-strictness'
                className={selectStyle}
                value={config.strictness ?? 'balanced'}
                onChange={(event) =>
                  update(
                    'strictness',
                    event.target.value as ReplyConfig['strictness']
                  )
                }
              >
                <option value='balanced'>
                  平衡：攻略站、社区资料也算数，引用原文对得上就直接回答
                </option>
                <option value='strict'>
                  严格：只认官方和专业资料站原文，每次再单独核验一遍
                </option>
                <option value='loose'>
                  宽松：资料大致支持就回答，注明来源类型
                </option>
              </select>
              <p className='text-xs text-muted-foreground'>
                “DeepSeek
                直接搜索”最快：只用搜索结果自带的摘录回答，不再逐个打开网页。但像技能数值表这种在页面深处的内容，摘录里经常没有。
              </p>
            </div>
            <p className='text-xs text-muted-foreground'>
              开启真实回复后 /ask
              始终回应，缺少依据时会说明无法确认或请求补充；普通提问要通过来源和答案核验才会回答。
            </p>
          </FieldGroup>

          <FieldGroup
            title='怎么触发和回复'
            description='/ask 开头的消息总会得到回应。这个号是你本人的微信，大家 @ 你不一定是在提问，所以 @ 你或引用你的回答追问时，只有查到可靠答案才会回答，否则保持安静。'
          >
            <div className='flex items-center gap-3'>
              <Switch
                id='reply-mention'
                checked={config.mention_trigger ?? true}
                onCheckedChange={(value) => update('mention_trigger', value)}
              />
              <Label htmlFor='reply-mention'>
                @ 我或引用我的回答时，跳过闲聊过滤直接查
              </Label>
            </div>
            <div className='space-y-2'>
              <Label htmlFor='reply-names'>群里还会怎么称呼我（可留空）</Label>
              <Input
                id='reply-names'
                value={names}
                placeholder='已自动使用微信名；改了群昵称时填在这里，多个用逗号分隔'
                onChange={(event) => {
                  setNames(event.target.value)
                  update(
                    'mention_names',
                    event.target.value
                      .split(/[,，、\s]+/)
                      .map((name) => name.trim())
                      .filter(Boolean)
                      .slice(0, 10)
                  )
                }}
              />
            </div>
            <div className='grid gap-4 sm:grid-cols-2'>
              <div className='space-y-2'>
                <Label htmlFor='reply-style'>回答方式</Label>
                <select
                  id='reply-style'
                  className={selectStyle}
                  value={config.reply_style ?? 'quote'}
                  onChange={(event) =>
                    update(
                      'reply_style',
                      event.target.value as ReplyConfig['reply_style']
                    )
                  }
                >
                  <option value='quote'>引用提问的那条消息</option>
                  <option value='plain'>直接发送</option>
                </select>
              </div>
              <div className='space-y-2'>
                <Label htmlFor='reply-sources'>来源写法</Label>
                <select
                  id='reply-sources'
                  className={selectStyle}
                  value={config.source_style ?? 'one_link'}
                  onChange={(event) =>
                    update(
                      'source_style',
                      event.target.value as ReplyConfig['source_style']
                    )
                  }
                >
                  <option value='one_link'>附 1 条链接</option>
                  <option value='names'>只写来源名称，不附链接</option>
                  <option value='links'>附最多 3 条链接</option>
                </select>
              </div>
            </div>
          </FieldGroup>

          <FieldGroup
            title='速度与可靠性'
            description='群里聊得快，回答慢了就没用了。这些选项让回答更快，也更不容易打扰大家。'
          >
            <div className='flex items-center gap-3'>
              <Switch
                id='reply-fast'
                checked={config.fast_stages ?? true}
                onCheckedChange={(value) => update('fast_stages', value)}
              />
              <Label htmlFor='reply-fast'>
                问题解析和答案核验用低推理强度，加快回答
              </Label>
            </div>
            <div className='flex items-center gap-3'>
              <Switch
                id='reply-stale'
                checked={config.stale_check ?? true}
                onCheckedChange={(value) => update('stale_check', value)}
              />
              <Label htmlFor='reply-stale'>
                主动回答发出前，先看看是不是已经有人答了或话题已过
              </Label>
            </div>
            <div className='space-y-2'>
              <Label>备用模型（回答模型出错时按顺序换用）</Label>
              {fallbacks.length > 0 && (
                <div className='flex flex-wrap gap-2'>
                  {fallbacks.map((id, index) => (
                    <span
                      key={id}
                      className='flex items-center gap-1 rounded-full border py-0.5 ps-3 pe-1 text-sm'
                    >
                      {index + 1}. {profileName(profiles, id)}
                      <Button
                        type='button'
                        size='icon'
                        variant='ghost'
                        className='size-6'
                        aria-label={`移除备用模型 ${profileName(profiles, id)}`}
                        onClick={() =>
                          update(
                            'fallback_profile_ids',
                            fallbacks.filter((value) => value !== id)
                          )
                        }
                      >
                        <X />
                      </Button>
                    </span>
                  ))}
                </div>
              )}
              <select
                aria-label='添加备用模型'
                className={selectStyle}
                value=''
                disabled={fallbacks.length >= 5}
                onChange={(event) => {
                  if (event.target.value)
                    update('fallback_profile_ids', [
                      ...fallbacks,
                      event.target.value,
                    ])
                }}
              >
                <option value=''>添加备用模型</option>
                {models
                  .filter(
                    (profile) =>
                      profile.id !== config.ai_profile_id &&
                      !fallbacks.includes(profile.id)
                  )
                  .map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.config.name}
                    </option>
                  ))}
              </select>
            </div>
            <div className='space-y-2'>
              <Label htmlFor='reply-vision'>看截图的模型</Label>
              <select
                id='reply-vision'
                className={selectStyle}
                value={config.vision_profile_id ?? ''}
                onChange={(event) =>
                  update('vision_profile_id', event.target.value || null)
                }
              >
                <option value=''>不看截图</option>
                {models.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.config.name}
                  </option>
                ))}
              </select>
              <p className='text-xs text-muted-foreground'>
                选一个能识图的模型（如 GPT
                系列）。问题附近的截图会先被转成文字描述，再用来理解和检索。DeepSeek
                不能看图。
              </p>
            </div>
          </FieldGroup>

          <FieldGroup
            title='本群黑话'
            description='每行一条，例如“摸金 = 加页手记”“zz9 = 某战队选手”。只用来理解群友在说什么，不会当作答案的依据。'
          >
            <Textarea
              aria-label='本群黑话'
              className='min-h-28 text-sm'
              maxLength={4000}
              value={config.glossary ?? ''}
              placeholder={'摸金 = 加页手记\n屠夫 = 监管者'}
              onChange={(event) => update('glossary', event.target.value)}
            />
          </FieldGroup>

          <FieldGroup title='常见问题库'>
            <div className='flex items-center gap-3'>
              <Switch
                id='reply-faq'
                checked={config.faq_enabled ?? true}
                onCheckedChange={(value) => update('faq_enabled', value)}
              />
              <Label htmlFor='reply-faq'>
                记住核验过的回答，同样的问题直接复用
              </Label>
            </div>
            <div className='max-w-48 space-y-2'>
              <Label htmlFor='reply-faq-days'>答案保留天数</Label>
              <Input
                id='reply-faq-days'
                type='number'
                min={1}
                max={90}
                value={config.faq_ttl_days ?? 7}
                onChange={(event) =>
                  update('faq_ttl_days', Number(event.target.value))
                }
              />
            </div>
            <p className='text-xs text-muted-foreground'>
              活动、价格这类时效性问题只保留 1 天。
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
