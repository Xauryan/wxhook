import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getRouteApi, Link } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ThemeSwitch } from '@/components/theme-switch'
import { aiApi } from '@/features/ai/api'
import { replyApi, type ReplyRule } from '@/features/group-replies/api'
import { reportApi, type ReportRule } from '@/features/group-reports/api'
import { ReplayTab } from './replay-tab'
import { ReplyTab } from './reply-tab'
import { ReportTab } from './report-tab'
import { ErrorText } from './shared'

const route = getRouteApi('/_authenticated/group-assistant/')
type Tab = 'reports' | 'replies' | 'replays'
type GroupEntry = {
  room: string
  name: string
  report?: ReportRule
  reply?: ReplyRule
}

export function GroupAssistantPage() {
  const search = route.useSearch()
  const navigate = route.useNavigate()
  const reports = useQuery({
    queryKey: ['group-reports'],
    queryFn: reportApi.status,
    refetchInterval: 5000,
  })
  const replies = useQuery({
    queryKey: ['group-replies'],
    queryFn: replyApi.status,
    refetchInterval: 3000,
  })
  const profiles = useQuery({
    queryKey: ['ai-profiles'],
    queryFn: aiApi.status,
  })
  const [added, setAdded] = useState<{ id: string; name: string }[]>([])
  const [picking, setPicking] = useState(false)
  const account = replies.data?.account || reports.data?.account || ''

  const entries = useMemo(() => {
    const map = new Map<string, GroupEntry>()
    const entry = (room: string, name: string) => {
      const value = map.get(room) ?? { room, name }
      map.set(room, value)
      return value
    }
    for (const rule of reports.data?.rules ?? [])
      entry(rule.room, rule.name).report = rule
    for (const rule of replies.data?.rules ?? [])
      entry(rule.room, rule.name).reply = rule
    for (const group of added) entry(group.id, group.name)
    return [...map.values()]
  }, [reports.data, replies.data, added])

  const selected =
    entries.find((item) => item.room === search.room) ?? entries[0]
  const tab: Tab = search.tab ?? 'reports'
  const select = (room: string, next: Tab = tab) =>
    void navigate({ search: { room, tab: next }, replace: true })

  return (
    <>
      <Header>
        <ThemeSwitch />
      </Header>
      <Main className='space-y-6'>
        <div className='flex flex-wrap items-end justify-between gap-3'>
          <div>
            <h1 className='text-2xl font-bold'>AI 群助手</h1>
            <p className='mt-2 text-sm text-muted-foreground'>
              按群管理 AI 日报和资料问答。先在左侧选群，再在右侧开启或调整功能。
            </p>
          </div>
          {replies.data?.profile.display_name && (
            <span className='text-sm text-muted-foreground'>
              当前账号 · {replies.data.profile.display_name}
            </span>
          )}
        </div>
        <ErrorText error={reports.error ?? replies.error ?? profiles.error} />
        <ErrorText error={reports.data?.error ?? replies.data?.error} />
        {profiles.data &&
          !profiles.data.profiles.some(
            (profile) => profile.config.enabled && !profile.decision_only
          ) && (
            <p className='rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm'>
              还没有可用的文本模型。先到{' '}
              <Link className='underline' to='/ai'>
                AI 接入
              </Link>{' '}
              添加一个模型配置。
            </p>
          )}
        {!account ? (
          <p className='text-sm text-muted-foreground'>正在连接当前微信账号…</p>
        ) : (
          <div className='grid gap-6 lg:grid-cols-[260px_minmax(0,1fr)]'>
            <aside className='space-y-3'>
              <Button
                className='w-full'
                variant='outline'
                onClick={() => setPicking(true)}
              >
                <Plus /> 添加群
              </Button>
              {entries.length === 0 && (
                <p className='text-sm text-muted-foreground'>
                  还没有群开启 AI 功能。点上面的按钮选一个群开始。
                </p>
              )}
              <nav className='space-y-1' aria-label='群列表'>
                {entries.map((item) => (
                  <button
                    key={item.room}
                    onClick={() => select(item.room)}
                    aria-current={item.room === selected?.room}
                    className={cn(
                      'w-full space-y-1.5 rounded-lg border px-3 py-2.5 text-left transition-colors',
                      item.room === selected?.room
                        ? 'border-primary bg-primary/5'
                        : 'border-transparent hover:bg-muted'
                    )}
                  >
                    <span className='block truncate font-medium'>
                      {item.name}
                    </span>
                    <span className='flex flex-wrap gap-1'>
                      <FeatureBadge
                        label='日报'
                        configured={!!item.report}
                        active={!!item.report?.config.enabled}
                        activeText={item.report?.config.schedule_time}
                      />
                      <FeatureBadge
                        label='问答'
                        configured={!!item.reply}
                        active={!!item.reply?.config.enabled}
                        activeText='发送中'
                      />
                    </span>
                  </button>
                ))}
              </nav>
            </aside>
            {selected && profiles.data && reports.data && replies.data ? (
              <section className='min-w-0 space-y-4'>
                <h2 className='truncate text-xl font-semibold'>
                  {selected.name}
                </h2>
                <Tabs
                  value={tab}
                  onValueChange={(value) => select(selected.room, value as Tab)}
                  className='gap-4'
                >
                  <TabsList>
                    <TabsTrigger value='reports'>群日报</TabsTrigger>
                    <TabsTrigger value='replies'>群问答</TabsTrigger>
                    <TabsTrigger value='replays'>回放测试</TabsTrigger>
                  </TabsList>
                  <TabsContent value='reports'>
                    <ReportTab
                      key={selected.room}
                      account={account}
                      room={selected.room}
                      name={selected.name}
                      rule={selected.report}
                      jobs={reports.data.jobs.filter(
                        (job) => job.room === selected.room
                      )}
                      prompts={reports.data.prompts}
                      profiles={profiles.data.profiles}
                    />
                  </TabsContent>
                  <TabsContent value='replies'>
                    <ReplyTab
                      key={selected.room}
                      account={account}
                      room={selected.room}
                      rule={selected.reply}
                      recent={replies.data.recent.filter(
                        (run) => run.room === selected.room
                      )}
                      prompts={replies.data.prompts}
                      profiles={profiles.data.profiles}
                    />
                  </TabsContent>
                  <TabsContent value='replays'>
                    <ReplayTab
                      key={selected.room}
                      account={account}
                      rule={selected.reply}
                      batches={replies.data.batches.filter(
                        (batch) => batch.room === selected.room
                      )}
                      onSetup={() => select(selected.room, 'replies')}
                    />
                  </TabsContent>
                </Tabs>
              </section>
            ) : (
              selected && (
                <p className='text-sm text-muted-foreground'>加载中…</p>
              )
            )}
          </div>
        )}
      </Main>
      {account && (
        <GroupPicker
          open={picking}
          account={account}
          configured={new Set(entries.map((item) => item.room))}
          onOpenChange={setPicking}
          onPick={(group) => {
            setAdded((previous) =>
              previous.some((item) => item.id === group.id)
                ? previous
                : [...previous, group]
            )
            setPicking(false)
            select(group.id)
          }}
        />
      )}
    </>
  )
}

function FeatureBadge({
  label,
  configured,
  active,
  activeText,
}: {
  label: string
  configured: boolean
  active: boolean
  activeText?: string
}) {
  if (!configured)
    return (
      <Badge variant='outline' className='text-muted-foreground'>
        {label} 未开启
      </Badge>
    )
  return (
    <Badge variant={active ? 'default' : 'secondary'}>
      {label} · {active ? activeText : '已暂停'}
    </Badge>
  )
}

function GroupPicker({
  open,
  account,
  configured,
  onOpenChange,
  onPick,
}: {
  open: boolean
  account: string
  configured: Set<string>
  onOpenChange: (open: boolean) => void
  onPick: (group: { id: string; name: string }) => void
}) {
  const groups = useQuery({
    queryKey: ['report-groups', account],
    queryFn: () => reportApi.groups(account),
    enabled: open,
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='p-0 sm:max-w-md'>
        <DialogHeader className='px-5 pt-5'>
          <DialogTitle>添加群</DialogTitle>
          <DialogDescription>
            选择要开启 AI 功能的微信群，之后可以分别开启群日报和群问答。
          </DialogDescription>
        </DialogHeader>
        <Command className='border-t'>
          <CommandInput placeholder='搜索群名' />
          <CommandList className='max-h-80'>
            {groups.isLoading && (
              <p className='p-4 text-sm text-muted-foreground'>
                正在读取通讯录…
              </p>
            )}
            <ErrorText error={groups.error} />
            <CommandEmpty>没有匹配的群</CommandEmpty>
            <CommandGroup>
              {groups.data?.items.map((group) => (
                <CommandItem
                  key={group.id}
                  value={`${group.name} ${group.id}`}
                  onSelect={() => onPick(group)}
                >
                  <span className='truncate'>{group.name}</span>
                  {configured.has(group.id) && (
                    <span className='ms-auto text-xs text-muted-foreground'>
                      已添加
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  )
}
