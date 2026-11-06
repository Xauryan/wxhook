import { type ReactNode, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Gift,
  LoaderCircle,
  RefreshCw,
  Settings2,
  ShieldCheck,
  UserRoundCheck,
  UsersRound,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  integrationsApi,
  type FriendConfig,
  type GroupConfig,
  type IntegrationStatus,
} from './api'
import { FriendForm, GroupForm, HongbaoDelayForm } from './config-forms'

const statusKey = ['app-integrations'] as const
type Feature = 'friends' | 'groups'
const labels: Record<string, string> = {
  friends: '好友自动通过',
  groups: '群聊自动拉人',
  reply: '通过后自动回复',
  runtime: '执行器',
}
const states: Record<string, string> = {
  dispatching: '提交中',
  pending: '待确认',
  accepted: '已受理',
  failed: '失败',
  unknown: '结果未知',
  skipped: '已跳过',
  blocked: '已拦截',
}

export function IntegrationsPanel() {
  const status = useQuery({
    queryKey: statusKey,
    queryFn: ({ signal }) => integrationsApi.status(signal),
    refetchInterval: 10000,
    retry: false,
  })
  return (
    <div className='space-y-6'>
      <div className='flex flex-wrap items-start justify-between gap-3'>
        <div>
          <h1 className='text-2xl font-bold tracking-tight'>自动化</h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            管理微信功能开关与配置，让日常操作按规则执行。
          </p>
        </div>
        <Button
          variant='outline'
          disabled={status.isFetching}
          onClick={() => void status.refetch()}
        >
          <RefreshCw
            className={status.isFetching ? 'motion-safe:animate-spin' : ''}
          />
          重新读取
        </Button>
      </div>
      {status.isError && (
        <div
          role='alert'
          className='rounded-lg border border-destructive/40 p-4 text-sm text-destructive'
        >
          读取失败：{status.error.message}。暂时禁止修改，请恢复连接后重新读取。
        </div>
      )}
      {status.isPending && (
        <div
          role='status'
          className='flex items-center gap-2 rounded-lg border p-6 text-sm text-muted-foreground'
        >
          <LoaderCircle className='size-4 motion-safe:animate-spin' />
          正在核验当前微信账号和原生开关…
        </div>
      )}
      {status.data && (
        <AccountIntegrations
          key={status.data.account}
          data={status.data}
          offline={status.isError}
        />
      )}
    </div>
  )
}

function FeatureCard({
  title,
  description,
  icon,
  enabled,
  disabled,
  onToggle,
  children,
  onConfigure,
  status,
}: {
  title: string
  description: string
  icon: ReactNode
  enabled: boolean | null
  disabled: boolean
  onToggle: (enabled: boolean) => void
  children: ReactNode
  onConfigure?: () => void
  status?: string
}) {
  return (
    <article className='flex min-w-0 flex-col rounded-xl border bg-card p-5'>
      <div className='mb-6 flex items-center justify-between gap-3'>
        <div className='flex size-11 items-center justify-center rounded-lg bg-muted'>
          {icon}
        </div>
        <div className='flex items-center gap-3'>
          <Badge variant={enabled ? 'default' : 'secondary'}>
            {status ||
              (enabled === null ? '状态未知' : enabled ? '已开启' : '已关闭')}
          </Badge>
          <Switch
            aria-label={`${title}开关`}
            checked={enabled === true}
            disabled={disabled || enabled === null}
            onCheckedChange={onToggle}
          />
        </div>
      </div>
      <h2 className='font-semibold'>{title}</h2>
      <p className='mt-1 text-sm leading-6 text-muted-foreground'>
        {description}
      </p>
      <div className='my-5 flex-1 space-y-2 border-t pt-4 text-sm leading-6'>
        {children}
      </div>
      {onConfigure && (
        <Button
          variant='outline'
          className='w-full'
          disabled={disabled}
          onClick={onConfigure}
        >
          <Settings2 className='size-4' />
          配置{title}
        </Button>
      )}
    </article>
  )
}

function AccountIntegrations({
  data,
  offline,
}: {
  data: IntegrationStatus
  offline: boolean
}) {
  const client = useQueryClient()
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [editing, setEditing] = useState<Feature | null>(null)
  const [confirmHongbao, setConfirmHongbao] = useState(false)
  const [editingDelay, setEditingDelay] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const groups = useQuery({
    queryKey: ['integration-groups', data.account],
    queryFn: ({ signal }) => integrationsApi.groups(data.account, signal),
    enabled: editing === 'groups',
    retry: false,
    staleTime: 0,
  })
  const save = useMutation({
    mutationFn: ({
      feature,
      revision,
      config,
    }: {
      feature: Feature
      revision: number
      config: FriendConfig | GroupConfig
    }) => integrationsApi.save(feature, data.account, revision, config),
    onMutate: async () => {
      await client.cancelQueries({ queryKey: statusKey })
      setError('')
      setNotice('')
    },
    onSuccess: (result, { feature }) => {
      client.setQueryData<IntegrationStatus>(statusKey, (current) =>
        current?.account === data.account
          ? {
              ...current,
              [feature]: result,
              ...(feature === 'friends'
                ? { native_friends: { available: true, enabled: false } }
                : {}),
            }
          : current
      )
      setNotice('配置已保存，仅处理保存后的新事件。')
      setEditing(null)
      void client.invalidateQueries({ queryKey: statusKey })
    },
    onError: (failure: Error) => setError(failure.message),
  })
  const hongbao = useMutation({
    mutationFn: (enabled: boolean) =>
      integrationsApi.hongbao(
        data.account,
        enabled,
        data.hongbao.enabled === true
      ),
    onMutate: async () => {
      await client.cancelQueries({ queryKey: statusKey })
      setError('')
      setNotice('')
    },
    onSuccess: (result) => {
      client.setQueryData<IntegrationStatus>(statusKey, (current) =>
        current?.account === data.account
          ? { ...current, hongbao: result }
          : current
      )
      setConfirmHongbao(false)
      setNotice(
        result.available
          ? '已写入并回读原生控制文件，不代表红包已领取。'
          : '控制文件已提交，但当前原生状态无法确认，请重新读取。'
      )
      void client.invalidateQueries({ queryKey: statusKey })
    },
    onError: (failure: Error) => {
      setError(failure.message)
      void client.invalidateQueries({ queryKey: statusKey })
    },
  })
  const delay = useMutation({
    mutationFn: ({ value, expected }: { value: string; expected: string }) =>
      integrationsApi.hongbaoDelay(data.account, value, expected),
    onMutate: async () => {
      await client.cancelQueries({ queryKey: statusKey })
      setError('')
      setNotice('')
    },
    onSuccess: (result) => {
      client.setQueryData<IntegrationStatus>(statusKey, (current) =>
        current?.account === data.account
          ? { ...current, hongbao: result }
          : current
      )
      setEditingDelay(false)
      setNotice(
        result.available
          ? `领取延迟已保存为 ${result.delay_ms} ms。`
          : '延迟已提交，当前状态需重新读取。'
      )
      void client.invalidateQueries({ queryKey: statusKey })
    },
    onError: (failure: Error) => {
      setError(failure.message)
      void client.invalidateQueries({ queryKey: statusKey })
    },
  })
  const busy = save.isPending || hongbao.isPending || delay.isPending
  const disabled = busy || offline
  const automationDisabled =
    disabled || !data.runtime.running || !data.runtime.available
  const friendEnabled = data.native_friends.available
    ? data.friends.config.enabled || data.native_friends.enabled === true
    : null
  const friendStatus = data.native_friends.enabled
    ? data.friends.config.enabled
      ? '控制冲突'
      : '原生已开启'
    : undefined
  const active =
    Number(friendEnabled === true) +
    Number(data.groups.config.enabled) +
    Number(data.hongbao.enabled === true)
  const visible = (name: string, enabled: boolean | null) =>
    name.includes(search.trim()) &&
    (filter === 'all' ||
      (filter === 'enabled' ? enabled === true : enabled === false))
  const toggle = (feature: Feature, enabled: boolean) => {
    if (feature === 'groups' && enabled && !data.groups.config.room) {
      setError('')
      setEditing('groups')
      return
    }
    save.mutate({
      feature,
      revision: data[feature].revision,
      config: { ...data[feature].config, enabled },
    })
  }
  const roomName =
    groups.data?.account === data.account
      ? groups.data.items.find(
          (group) => group.username === data.groups.config.room
        )?.name
      : undefined
  const allHidden =
    !visible(labels.friends, friendEnabled) &&
    !visible(labels.groups, data.groups.config.enabled) &&
    !visible('红包秒抢', data.hongbao.enabled)
  return (
    <>
      <section
        className='flex flex-wrap items-center justify-between gap-4 rounded-xl border bg-muted/35 px-5 py-4'
        aria-label='当前微信账号'
      >
        <div className='min-w-0'>
          <p className='flex items-center gap-2 text-sm font-medium'>
            <ShieldCheck className='size-4' />
            {data.profile.display_name}{' '}
            <span className='font-normal text-muted-foreground'>当前账号</span>
          </p>
          <p className='mt-1 text-xs break-all text-muted-foreground'>
            {data.account}
          </p>
        </div>
        <p className='text-sm text-muted-foreground'>
          <span className='font-semibold text-foreground'>{active}</span> / 3
          项开启
        </p>
      </section>
      <p className='text-sm text-muted-foreground'>
        新的好友与群聊规则默认关闭，按账号保存；已有原生开关如实展示，不在启动时擅自修改。
      </p>
      {(!data.runtime.running || data.runtime.error) && (
        <p role='alert' className='rounded-lg border p-3 text-sm'>
          {data.runtime.error || '后台执行器未运行，好友与群聊功能暂不可用。'}
        </p>
      )}
      {notice && (
        <p role='status' className='rounded-lg border p-3 text-sm'>
          {notice}
        </p>
      )}
      {error && !editing && !confirmHongbao && !editingDelay && (
        <p role='alert' className='text-sm text-destructive'>
          {error}
        </p>
      )}
      <div className='flex flex-wrap gap-3'>
        <Input
          aria-label='搜索应用'
          placeholder='搜索应用…'
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className='w-full sm:w-64'
        />
        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger aria-label='按开关状态筛选' className='w-36'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>全部应用</SelectItem>
            <SelectItem value='enabled'>已开启</SelectItem>
            <SelectItem value='disabled'>已关闭</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className='grid items-stretch gap-4 md:grid-cols-2 xl:grid-cols-3'>
        {visible(labels.friends, friendEnabled) && (
          <FeatureCard
            title={labels.friends}
            description='自动处理新好友申请，通过后可发送一条自定义回复。'
            icon={<UserRoundCheck className='size-5' />}
            enabled={friendEnabled}
            status={friendStatus}
            disabled={automationDisabled}
            onToggle={(value) => toggle('friends', value)}
            onConfigure={() => {
              setError('')
              setEditing('friends')
            }}
          >
            <p>
              通过后回复：
              <span className='font-medium'>
                {data.friends.config.reply_enabled ? '已配置' : '未开启'}
              </span>
            </p>
            {data.native_friends.enabled && (
              <p className='text-xs text-muted-foreground'>
                当前原生自动通过已开启。保存本页配置后将关闭旧模式，由账号级规则接管。
              </p>
            )}
            <p className='line-clamp-2 break-all text-muted-foreground'>
              {data.friends.config.reply_enabled
                ? data.friends.config.reply_text
                : '可单独配置欢迎语，不补发历史申请。'}
            </p>
          </FeatureCard>
        )}
        {visible(labels.groups, data.groups.config.enabled) && (
          <FeatureCard
            title={labels.groups}
            description='好友私聊发送指定关键词后，自动加入你选择的群聊。'
            icon={<UsersRound className='size-5' />}
            enabled={data.groups.config.enabled}
            disabled={automationDisabled}
            onToggle={(value) => toggle('groups', value)}
            onConfigure={() => {
              setError('')
              setEditing('groups')
            }}
          >
            <p>
              私聊关键词：
              <span className='font-medium'>{data.groups.config.keyword}</span>
            </p>
            <p className='break-all text-muted-foreground'>
              {roomName || data.groups.config.room || '尚未选择目标群聊'}
            </p>
            <p className='text-xs text-muted-foreground'>
              执行前重新核验管理权限，已在群内不重复拉入。
            </p>
          </FeatureCard>
        )}
        {visible('红包秒抢', data.hongbao.enabled) && (
          <FeatureCard
            title='红包秒抢'
            description='使用现有原生冷抢链路，收到红包后自动尝试领取。'
            icon={<Gift className='size-5' />}
            enabled={data.hongbao.enabled}
            disabled={disabled || !data.hongbao.available}
            onToggle={(value) => {
              if (value) {
                setError('')
                setConfirmHongbao(true)
              } else hongbao.mutate(false)
            }}
            status={data.hongbao.receive_only ? '仅接收，未打开' : undefined}
            onConfigure={() => {
              setError('')
              setEditingDelay(true)
            }}
          >
            <p>
              执行范围：<span className='font-medium'>当前微信进程</span>
            </p>
            <p className='text-muted-foreground'>
              领取延迟：
              {data.hongbao.delay_ms === null
                ? '未知'
                : `${data.hongbao.delay_ms} ms`}
            </p>
            <p className='text-xs text-muted-foreground'>
              {data.hongbao.detail}
            </p>
          </FeatureCard>
        )}
      </div>
      {allHidden && (
        <p className='rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground'>
          没有匹配的应用，请调整搜索或筛选条件。
        </p>
      )}
      <section className='rounded-xl border p-5'>
        <h2 className='font-semibold'>最近执行记录</h2>
        <p className='mt-1 text-xs text-muted-foreground'>
          仅展示当前账号的实际处理记录。“已受理”不等于已发送或已入群；结果未知不自动重试。
        </p>
        {!data.actions.length ? (
          <p className='py-8 text-center text-sm text-muted-foreground'>
            暂无执行记录，启用后将从新的申请和私聊消息开始处理。
          </p>
        ) : (
          <ul className='mt-4 divide-y'>
            {data.actions.map((action) => (
              <li
                key={action.action_id}
                className='flex flex-wrap gap-2 py-3 text-sm'
              >
                <span className='font-medium'>
                  {labels[action.feature] || action.feature}
                </span>
                <Badge variant='outline'>
                  {states[action.state] || action.state}
                </Badge>
                <time className='ms-auto text-xs text-muted-foreground'>
                  {new Date(action.created_at * 1000).toLocaleString('zh-CN')}
                </time>
                <p className='w-full break-words text-muted-foreground'>
                  {action.detail}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
      <Dialog
        open={editing !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setEditing(null)
        }}
      >
        <DialogContent className='max-h-[85dvh] overflow-y-auto'>
          <DialogHeader>
            <DialogTitle>
              {editing === 'friends'
                ? '好友通过与回复配置'
                : '群聊自动拉人配置'}
            </DialogTitle>
            <DialogDescription>
              仅对当前账号生效。保存前的历史消息不会触发自动操作。
            </DialogDescription>
          </DialogHeader>
          {editing === 'friends' && (
            <FriendForm
              config={{
                ...data.friends.config,
                enabled: friendEnabled === true,
              }}
              revision={data.friends.revision}
              disabled={automationDisabled}
              error={error}
              onCancel={() => setEditing(null)}
              onSave={(config, revision) =>
                save.mutate({ feature: 'friends', config, revision })
              }
            />
          )}
          {editing === 'groups' && (
            <GroupForm
              config={data.groups.config}
              revision={data.groups.revision}
              disabled={automationDisabled}
              error={error}
              groups={
                groups.data?.account === data.account ? groups.data.items : []
              }
              loading={groups.isFetching}
              groupsError={groups.error?.message}
              refresh={() => void groups.refetch()}
              onCancel={() => setEditing(null)}
              onSave={(config, revision) =>
                save.mutate({ feature: 'groups', config, revision })
              }
            />
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={editingDelay}
        onOpenChange={(open) => {
          if (!busy) setEditingDelay(open)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>红包领取延迟</DialogTitle>
            <DialogDescription>
              在发起领取请求前等待，对当前微信进程生效。保存后自动加载，适用于后续新领取任务。
            </DialogDescription>
          </DialogHeader>
          {editingDelay && data.hongbao.delay_ms !== null && (
            <HongbaoDelayForm
              initialDelay={data.hongbao.delay_ms}
              disabled={disabled || !data.hongbao.available}
              error={error}
              onCancel={() => setEditingDelay(false)}
              onSave={(value, expected) => delay.mutate({ value, expected })}
            />
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={confirmHongbao}
        onOpenChange={(open) => {
          if (!busy) setConfirmHongbao(open)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>开启红包秒抢</DialogTitle>
            <DialogDescription>
              将对当前微信进程收到的红包自动尝试领取。该开关不按账号隔离，切换微信账号后仍可能保持开启。保留现有延迟与快车道配置。
            </DialogDescription>
          </DialogHeader>
          {error && (
            <p role='alert' className='text-sm text-destructive'>
              {error}
            </p>
          )}
          <DialogFooter>
            <Button
              variant='outline'
              disabled={busy}
              onClick={() => setConfirmHongbao(false)}
            >
              取消
            </Button>
            <Button disabled={disabled} onClick={() => hongbao.mutate(true)}>
              {busy ? '正在保存…' : '确认开启'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
