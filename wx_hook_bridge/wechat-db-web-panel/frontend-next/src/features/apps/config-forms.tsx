import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { type FriendConfig, type Group, type GroupConfig } from './api'

type FormProps<T> = {
  config: T
  revision: number
  disabled: boolean
  error: string
  onCancel: () => void
  onSave: (config: T, revision: number) => void
}

export function HongbaoDelayForm({
  initialDelay,
  disabled,
  error,
  onCancel,
  onSave,
}: {
  initialDelay: string
  disabled: boolean
  error: string
  onCancel: () => void
  onSave: (delay: string, expected: string) => void
}) {
  const [draft, setDraft] = useState(initialDelay)
  const [expected] = useState(initialDelay)
  const match = /^([0-9]+)(?:\s*-\s*([0-9]+))?$/.exec(draft.trim())
  const low = Number(match?.[1])
  const high = Number(match?.[2] ?? match?.[1])
  const valid = match !== null && low <= high && high <= 600000
  return (
    <form
      className='space-y-4'
      onSubmit={(event) => {
        event.preventDefault()
        if (valid && !disabled) onSave(draft.trim(), expected)
      }}
    >
      <div className='space-y-2'>
        <Label htmlFor='hongbao-delay'>领取延迟（毫秒）</Label>
        <Input
          id='hongbao-delay'
          value={draft}
          maxLength={32}
          disabled={disabled}
          placeholder='例如 300 或 300-800'
          aria-describedby='hongbao-delay-help'
          aria-invalid={!valid}
          onChange={(event) => setDraft(event.target.value)}
        />
        <p id='hongbao-delay-help' className='text-sm text-muted-foreground'>
          300 表示固定 300 毫秒，300-800 表示每次随机等待 300～800 毫秒。0
          为立即执行，上限 600000 毫秒。
        </p>
        {!valid && (
          <p role='alert' className='text-sm text-destructive'>
            请输入 0～600000 的整数，或最小值不大于最大值的范围。
          </p>
        )}
      </div>
      {error && (
        <p role='alert' className='text-sm text-destructive'>
          {error}
        </p>
      )}
      <DialogFooter>
        <Button
          type='button'
          variant='outline'
          disabled={disabled}
          onClick={onCancel}
        >
          取消
        </Button>
        <Button type='submit' disabled={disabled || !valid}>
          保存延迟
        </Button>
      </DialogFooter>
    </form>
  )
}

export function FriendForm({
  config,
  revision,
  disabled,
  error,
  onCancel,
  onSave,
}: FormProps<FriendConfig>) {
  const [draft, setDraft] = useState(config)
  const [baseRevision] = useState(revision)
  const invalid = draft.reply_enabled && !draft.reply_text.trim()
  return (
    <form
      className='space-y-5'
      onSubmit={(event) => {
        event.preventDefault()
        if (!invalid && !disabled) onSave(draft, baseRevision)
      }}
    >
      <div className='flex items-center justify-between gap-4'>
        <Label htmlFor='friend-enabled'>自动通过新好友申请</Label>
        <Switch
          id='friend-enabled'
          checked={draft.enabled}
          onCheckedChange={(enabled) => setDraft({ ...draft, enabled })}
          disabled={disabled}
        />
      </div>
      <div className='flex items-center justify-between gap-4'>
        <Label htmlFor='reply-enabled'>通过后自动回复</Label>
        <Switch
          id='reply-enabled'
          checked={draft.reply_enabled}
          onCheckedChange={(reply_enabled) =>
            setDraft({ ...draft, reply_enabled })
          }
          disabled={disabled}
        />
      </div>
      <div className='space-y-2'>
        <Label htmlFor='reply-text'>回复内容</Label>
        <Textarea
          id='reply-text'
          maxLength={2000}
          rows={5}
          placeholder='填写好友通过后发送的欢迎语'
          value={draft.reply_text}
          disabled={disabled}
          onChange={(event) =>
            setDraft({ ...draft, reply_text: event.target.value })
          }
          aria-describedby='reply-help'
        />
        <p id='reply-help' className='text-xs text-muted-foreground'>
          {draft.reply_text.length} / 2000
          字。仅在自动通过开启且好友关系确认后发送一次。
        </p>
        {invalid && (
          <p role='alert' className='text-sm text-destructive'>
            开启自动回复时请填写回复内容。
          </p>
        )}
      </div>
      {error && (
        <p role='alert' className='text-sm text-destructive'>
          {error}
        </p>
      )}
      <DialogFooter>
        <Button
          type='button'
          variant='outline'
          onClick={onCancel}
          disabled={disabled}
        >
          取消
        </Button>
        <Button type='submit' disabled={disabled || invalid}>
          保存配置
        </Button>
      </DialogFooter>
    </form>
  )
}

export function GroupForm({
  config,
  revision,
  disabled,
  error,
  groups,
  loading,
  groupsError,
  refresh,
  onCancel,
  onSave,
}: FormProps<GroupConfig> & {
  groups: Group[]
  loading: boolean
  groupsError?: string
  refresh: () => void
}) {
  const [draft, setDraft] = useState(config)
  const [baseRevision] = useState(revision)
  const selected = groups.find((group) => group.username === draft.room)
  const invalid =
    !draft.keyword.trim() ||
    (draft.enabled && (loading || !!groupsError || !selected?.can_manage))
  return (
    <form
      className='space-y-5'
      onSubmit={(event) => {
        event.preventDefault()
        if (!invalid && !disabled) onSave(draft, baseRevision)
      }}
    >
      <div className='flex items-center justify-between gap-4'>
        <Label htmlFor='group-enabled'>启用关键词自动拉人</Label>
        <Switch
          id='group-enabled'
          checked={draft.enabled}
          disabled={disabled}
          onCheckedChange={(enabled) => setDraft({ ...draft, enabled })}
        />
      </div>
      <div className='space-y-2'>
        <Label htmlFor='group-keyword'>私聊关键词</Label>
        <Input
          id='group-keyword'
          value={draft.keyword}
          maxLength={100}
          disabled={disabled}
          onChange={(event) =>
            setDraft({ ...draft, keyword: event.target.value })
          }
        />
        <p className='text-xs text-muted-foreground'>
          去除首尾空格后完整匹配；群消息、自己的消息和非好友不触发。
        </p>
      </div>
      <div className='space-y-3'>
        <div className='flex items-center justify-between'>
          <Label>目标群聊</Label>
          <Button
            type='button'
            variant='ghost'
            size='sm'
            disabled={loading || disabled}
            onClick={refresh}
          >
            <RefreshCw
              className={loading ? 'size-4 motion-safe:animate-spin' : 'size-4'}
            />
            核验权限
          </Button>
        </div>
        {loading && (
          <p role='status' className='text-sm text-muted-foreground'>
            正在读取当前账号群聊和管理权限…
          </p>
        )}
        {groupsError && (
          <p role='alert' className='text-sm text-destructive'>
            {groupsError}
          </p>
        )}
        {!loading && !groupsError && !groups.length && (
          <p className='text-sm text-muted-foreground'>
            当前账号没有可读取的群聊。
          </p>
        )}
        <div
          className='max-h-52 space-y-2 overflow-y-auto'
          role='radiogroup'
          aria-label='目标群聊'
        >
          {groups.map((group) => (
            <label
              key={group.username}
              className={`flex items-start gap-3 rounded-lg border p-3 text-sm ${!group.can_manage ? 'bg-muted/30 text-muted-foreground' : 'cursor-pointer'}`}
            >
              <input
                type='radio'
                name='target-group'
                value={group.username}
                checked={draft.room === group.username}
                disabled={
                  disabled || loading || !!groupsError || !group.can_manage
                }
                onChange={() => setDraft({ ...draft, room: group.username })}
                className='mt-1 size-4 accent-foreground'
              />
              <span className='min-w-0 flex-1'>
                <span className='block font-medium break-all'>
                  {group.name}
                </span>
                <span className='mt-1 block text-xs'>{group.reason}</span>
              </span>
              <Badge variant='outline'>
                {group.can_manage ? '群主' : '权限未确认'}
              </Badge>
            </label>
          ))}
        </div>
        <p className='text-xs leading-5 text-muted-foreground'>
          目前可从源库确认群主。未取得可靠管理员身份的群不可选，保存及每次执行前都会再次核验；不冒用普通群成员权限。
        </p>
      </div>
      {draft.enabled && !selected?.can_manage && (
        <p role='alert' className='text-sm text-destructive'>
          请选择已确认管理权限的目标群聊。
        </p>
      )}
      {error && (
        <p role='alert' className='text-sm text-destructive'>
          {error}
        </p>
      )}
      <DialogFooter>
        <Button
          type='button'
          variant='outline'
          onClick={onCancel}
          disabled={disabled}
        >
          取消
        </Button>
        <Button type='submit' disabled={disabled || invalid}>
          保存配置
        </Button>
      </DialogFooter>
    </form>
  )
}
