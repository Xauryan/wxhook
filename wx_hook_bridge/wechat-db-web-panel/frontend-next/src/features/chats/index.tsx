import { useDeferredValue, useRef, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import {
  LoaderCircle,
  MessagesSquare,
  Plus,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { ConfigDrawer } from '@/components/config-drawer'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search as GlobalSearch } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import {
  chatApi,
  cleanText,
  type Scope,
  type Session,
  type LiveStatus,
  type ChatTarget,
} from './api'
import { ChatNavigation } from './chat-navigation'
import { ContactAvatar, Notice } from './chat-ui'
import { ContactsWorkspace } from './contacts-workspace'
import { Conversation } from './conversation'
import { FavoritesWorkspace } from './favorites-workspace'
import { displayTime } from './message-flow'
import { MomentsWorkspace } from './moments-workspace'
import { RecallsWorkspace } from './recalls-workspace'
import { useAccountBoundary } from './use-account-boundary'
import { useLiveEvents } from './use-live-events'

export function Chats() {
  const status = useQuery({
    queryKey: ['wechat-live-status-v2'],
    queryFn: ({ signal }) => chatApi.status(signal),
    refetchInterval: 5000,
    retry: 1,
    refetchOnWindowFocus: false,
  })
  const live: LiveStatus = status.data ?? {
    account: '',
    profile: { username: '' },
    bridge_connected: false,
    receive_connected: false,
    newest_sequence: 0,
    relay_pending: 0,
    relay_dropped: 0,
  }
  const statusUnavailable =
    status.isError || status.errorUpdatedAt > status.dataUpdatedAt
  const statusError =
    status.error ?? (statusUnavailable ? new Error('本地接口尚未恢复。') : null)
  useAccountBoundary(live.account, live.bridge_connected && !statusUnavailable)
  return (
    <ChatWorkspace
      key={live.account}
      live={
        statusUnavailable
          ? { ...live, bridge_connected: false, receive_connected: false }
          : live
      }
      statusError={statusError}
      statusPending={status.isPending && !statusUnavailable}
      refreshStatus={() => void status.refetch()}
    />
  )
}

function ChatWorkspace({
  live,
  statusError,
  statusPending,
  refreshStatus,
}: {
  live: LiveStatus
  statusError: Error | null
  statusPending: boolean
  refreshStatus: () => void
}) {
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search.trim())
  const [scope, setScope] = useState<Scope>('all')
  const [view, setView] = useState<
    'chats' | 'contacts' | 'favorites' | 'moments' | 'recalls'
  >('chats')
  const [directorySelected, setDirectorySelected] = useState<string | null>(
    null
  )
  const [selected, setSelected] = useState<Session | null>(null)
  const [chatTarget, setChatTarget] = useState<ChatTarget | undefined>()
  const searchInput = useRef<HTMLInputElement>(null)
  const focusSearchOnClose = useRef(false)
  const streamConnected = useLiveEvents(live.account)
  const connectionLabel = statusError
    ? '本地服务未连接'
    : statusPending
      ? '正在连接本地服务'
      : !live.bridge_connected
        ? '微信未连接'
        : streamConnected && live.receive_connected
          ? '实时接收中'
          : '接收链路未就绪'
  const unknownAccountLabel = statusError
    ? '账号状态未知'
    : statusPending
      ? '正在读取账号'
      : '等待微信登录'
  const list = useInfiniteQuery({
    queryKey: ['wechat-roster-v2', live.account, 'list', deferredSearch, scope],
    queryFn: ({ signal, pageParam }) =>
      chatApi.list(live.account, deferredSearch, scope, pageParam, signal),
    enabled: !!live.account,
    staleTime: 30_000,
    gcTime: 30 * 60_000,
    refetchOnWindowFocus: false,
    placeholderData: (previous, query) =>
      query?.queryKey[1] === live.account ? previous : undefined,
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0)
      return last.items.length > 0 && loaded < last.total ? loaded : undefined
    },
  })
  const items = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.items) ?? []).map((session) => [
        session.username,
        session,
      ])
    ).values(),
  ]
  const count = list.data?.pages[0]?.total
  const account = live.profile
  const accountName =
    account.display_name || account.nickname || account.username
  const selectedCurrent =
    items.find((item) => item.username === selected?.username) ?? selected
  const refresh = () => {
    if (live.account) void list.refetch()
    refreshStatus()
  }
  const changeScope = (value: Scope) => {
    setScope(value)
    setSelected(null)
  }

  return (
    <>
      <Header className='shrink-0'>
        <GlobalSearch className='me-auto' />
        <ThemeSwitch />
        <ConfigDrawer />
        <div
          className='chat-admin-account'
          title={`${accountName || unknownAccountLabel} · ${connectionLabel}`}
        >
          <ShieldCheck size={14} />
          <span className='chat-admin-status'>{connectionLabel}</span>
          <span className='chat-admin-name'>
            {accountName || unknownAccountLabel}
          </span>
          <ContactAvatar name={accountName || '微'} url={account.avatar_url} />
        </div>
      </Header>
      <Main fixed fluid id='content' className='chat-workspace'>
        <h1 className='sr-only'>微信聊天</h1>
        <div className='chat-page'>
          <ChatNavigation
            live={live}
            connectionLabel={connectionLabel}
            view={view}
            onViewChange={setView}
            onRefresh={refresh}
            refreshing={list.isFetching}
          />
          {view === 'recalls' ? (
            <RecallsWorkspace
              live={live}
              openChat={(session, target) => {
                setSelected(session)
                setChatTarget(target)
                setScope('all')
                setSearch('')
                setView('chats')
              }}
            />
          ) : view === 'moments' ? (
            <MomentsWorkspace live={live} />
          ) : view === 'favorites' ? (
            <FavoritesWorkspace live={live} />
          ) : view === 'contacts' ? (
            <ContactsWorkspace
              live={live}
              selected={directorySelected}
              select={setDirectorySelected}
              openChat={(session) => {
                setSelected(session)
                setChatTarget(undefined)
                setScope('all')
                setSearch('')
                setView('chats')
              }}
            />
          ) : (
            <div className={`wechat-window ${selected ? 'has-selection' : ''}`}>
              <aside className='session-panel' aria-label='微信会话列表'>
                <div className='session-toolbar'>
                  <h2 className='sr-only'>微信会话</h2>
                  <div className='session-search'>
                    <Search size={13} />
                    <Input
                      ref={searchInput}
                      aria-label='搜索微信会话'
                      placeholder='搜索'
                      maxLength={200}
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                    />
                    {search && (
                      <button
                        aria-label='清空会话搜索'
                        onClick={() => setSearch('')}
                      >
                        <X size={15} />
                      </button>
                    )}
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type='button'
                        className='session-add'
                        aria-label='会话操作与筛选'
                        title='会话操作与筛选'
                      >
                        <Plus size={19} />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                      align='end'
                      onCloseAutoFocus={(event) => {
                        if (!focusSearchOnClose.current) return
                        event.preventDefault()
                        focusSearchOnClose.current = false
                        searchInput.current?.focus()
                      }}
                    >
                      <DropdownMenuItem
                        onSelect={() => {
                          focusSearchOnClose.current = true
                        }}
                      >
                        <Search />
                        查找联系人或群聊
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuLabel>
                        会话筛选
                        {count === undefined ? '' : ` · ${count} 个会话`}
                      </DropdownMenuLabel>
                      <DropdownMenuRadioGroup
                        value={scope}
                        onValueChange={(value) => changeScope(value as Scope)}
                      >
                        <DropdownMenuRadioItem value='all'>
                          全部
                        </DropdownMenuRadioItem>
                        <DropdownMenuRadioItem value='contacts'>
                          私聊
                        </DropdownMenuRadioItem>
                        <DropdownMenuRadioItem value='groups'>
                          群聊
                        </DropdownMenuRadioItem>
                      </DropdownMenuRadioGroup>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
                {scope !== 'all' && (
                  <div className='session-filter-note'>
                    <span>{scope === 'groups' ? '群聊' : '联系人'}</span>
                    <button
                      type='button'
                      onClick={() => changeScope('all')}
                      aria-label='显示全部会话'
                    >
                      <X size={13} />
                    </button>
                  </div>
                )}
                {statusError ? (
                  <Notice
                    error={
                      new Error(
                        `本地服务无法连接，请启动或重启本地服务后刷新连接。${statusError.message}`
                      )
                    }
                    retry={refreshStatus}
                  />
                ) : statusPending ? (
                  <p className='live-chat-notice' role='status'>
                    正在连接本地服务并读取微信状态…
                  </p>
                ) : null}
                {!statusError &&
                  !statusPending &&
                  (live.contacts_error ||
                    !live.bridge_connected ||
                    !live.receive_connected ||
                    live.relay_pending > 0 ||
                    live.relay_dropped > 0) && (
                    <p className='live-chat-notice' role='status'>
                      {live.contacts_error ||
                        `${connectionLabel}。积压 ${live.relay_pending} 条，累计丢弃 ${live.relay_dropped} 条。`}
                    </p>
                  )}
                <div className='session-scroll' aria-busy={list.isFetching}>
                  {list.isError && (
                    <Notice
                      error={list.error}
                      retry={() => void list.refetch()}
                    />
                  )}
                  {list.isPending && (
                    <div className='chat-state'>
                      {!statusError && (statusPending || !!live.account) && (
                        <LoaderCircle className='spin' />
                      )}
                      <p>
                        {live.account
                          ? '正在读取当前微信联系人'
                          : statusError
                            ? '请先恢复本地服务连接'
                            : statusPending
                              ? '正在读取连接状态'
                              : '请登录微信并启动桥接'}
                      </p>
                    </div>
                  )}
                  {!list.isPending && !list.isError && !items.length && (
                    <div className='chat-state'>
                      <Search size={30} />
                      <p>{search ? '没有找到匹配的会话' : '暂无会话记录'}</p>
                      <small>
                        {search
                          ? '试试其他名字或群聊名称'
                          : '本地数据就绪后，在这里查看聊天'}
                      </small>
                    </div>
                  )}
                  {items.map((session) => (
                    <button
                      key={session.username}
                      className={`session-item ${selected?.username === session.username ? 'selected' : ''}`}
                      aria-pressed={selected?.username === session.username}
                      onClick={() => {
                        setSelected(session)
                        setChatTarget(undefined)
                      }}
                    >
                      <ContactAvatar
                        name={session.display_name}
                        url={session.avatar_url}
                        group={session.is_group}
                      />
                      <span className='session-copy'>
                        <span className='session-line'>
                          <strong>{session.display_name}</strong>
                          <time>{displayTime(session.last_active)}</time>
                        </span>
                        <span className='session-preview'>
                          {cleanText(session.summary) || '暂无消息摘要'}
                        </span>
                      </span>
                    </button>
                  ))}
                  {list.hasNextPage && (
                    <Button
                      className='load-sessions'
                      variant='ghost'
                      disabled={list.isFetching}
                      onClick={() => void list.fetchNextPage()}
                    >
                      {list.isFetchingNextPage ? '读取中…' : '加载更多会话'}
                    </Button>
                  )}
                </div>
              </aside>
              {selectedCurrent ? (
                <Conversation
                  key={selectedCurrent.username}
                  session={selectedCurrent}
                  profile={account}
                  live={live}
                  initialTarget={chatTarget}
                  onBack={() => setSelected(null)}
                />
              ) : (
                <section className='conversation-empty'>
                  <div className='empty-chat-art'>
                    <MessagesSquare size={48} strokeWidth={1.2} />
                  </div>
                  <h2>从一段对话开始</h2>
                  <p>选择联系人或群聊，开始实时聊天</p>
                </section>
              )}
            </div>
          )}
        </div>
      </Main>
    </>
  )
}
