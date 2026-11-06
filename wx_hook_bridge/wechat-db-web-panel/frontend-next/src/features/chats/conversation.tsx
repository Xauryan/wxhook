import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  ArrowDown,
  ArrowLeft,
  Ellipsis,
  LoaderCircle,
  Search,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import type { ChatTarget, LiveStatus, Message, Profile, Session } from './api'
import { Notice } from './chat-ui'
import { Composer } from './composer'
import {
  captureAnchor,
  isNearBottom,
  restoreAnchor,
  type ScrollAnchor,
} from './message-flow'
import './message-media.css'
import { MessageRow } from './message-row'
import { PendingSendRow } from './pending-send-row'
import { useMessageFlow } from './use-message-flow'
import { usePendingSends } from './use-pending-sends'

const EMPTY: Message[] = []
export function Conversation({
  session,
  profile,
  live,
  onBack,
  initialTarget,
}: {
  session: Session
  profile?: Profile['profile']
  live: LiveStatus
  onBack: () => void
  initialTarget?: ChatTarget
}) {
  const flow = useMessageFlow(live.account, session.username)
  const messages = flow.data?.messages ?? EMPTY
  const outgoing = usePendingSends(
    live.account,
    session.username,
    messages,
    flow.data?.revision ?? 0
  )
  const current = flow.data?.session ?? session
  const [searchOpen, setSearchOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [infoOpen, setInfoOpen] = useState(false)
  const [unread, setUnread] = useState(0)
  const [target, setTarget] = useState<string | undefined>()
  const [jumpError, setJumpError] = useState<Error | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)
  const anchor = useRef<ScrollAnchor | null>(null)
  const prependAnchor = useRef<ScrollAnchor | null>(null)
  const restoring = useRef(false)
  const upwardIntent = useRef(false)
  const previous = useRef<Message[]>(EMPTY)
  const initialized = useRef(false)
  const placed = useRef<string | undefined>(undefined)
  const matching = useMemo(
    () =>
      messages.filter(
        (message) =>
          !search ||
          `${message.sender_name || ''} ${message.content || ''} ${message.title || ''} ${message.file_name || ''} ${message.reference?.content || ''}`
            .toLocaleLowerCase()
            .includes(search.toLocaleLowerCase())
      ),
    [messages, search]
  )

  useLayoutEffect(() => {
    const element = scroller.current
    if (!element || (!messages.length && !outgoing.messages.length)) return
    const previousIds = new Set(previous.current.map((item) => item.id))
    const lastTime =
      previous.current[previous.current.length - 1]?.order_time ?? Infinity
    const added = messages.filter(
      (item) => !previousIds.has(item.id) && item.order_time >= lastTime
    ).length
    if (target && placed.current !== target) {
      const node = element.querySelector<HTMLElement>(
        `[data-message-id="${target}"]`
      )
      node?.scrollIntoView({ block: 'center' })
      nearBottom.current = false
      placed.current = target
      initialized.current = true
      anchor.current = captureAnchor(element)
    } else if (!initialized.current || (nearBottom.current && !search)) {
      element.scrollTop = element.scrollHeight
      initialized.current = true
      setUnread(0)
    } else {
      restoreAnchor(element, prependAnchor.current ?? anchor.current)
      if (added) setUnread((value) => value + added)
    }
    previous.current = messages
  }, [messages, outgoing.messages, search, target])

  useEffect(() => {
    let frame = 0
    const observer = new ResizeObserver(() => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        const element = scroller.current
        if (!element || !initialized.current) return
        if (nearBottom.current) element.scrollTop = element.scrollHeight
        else restoreAnchor(element, prependAnchor.current ?? anchor.current)
      })
    })
    if (content.current) observer.observe(content.current)
    return () => {
      observer.disconnect()
      cancelAnimationFrame(frame)
    }
  }, [])

  const older = async () => {
    if (restoring.current || flow.loadingOlder) return
    if (scroller.current)
      prependAnchor.current = captureAnchor(scroller.current)
    restoring.current = true
    upwardIntent.current = false
    nearBottom.current = false
    await flow.loadOlder()
    // Hold the same element through layout and lazy-media resize notifications.
    // Programmatic scroll events must not replace it with the prepended page.
    requestAnimationFrame(() => {
      if (scroller.current)
        restoreAnchor(scroller.current, prependAnchor.current)
      requestAnimationFrame(() => {
        if (scroller.current)
          restoreAnchor(scroller.current, prependAnchor.current)
        anchor.current = prependAnchor.current
        prependAnchor.current = null
        restoring.current = false
      })
    })
  }
  const latest = async () => {
    try {
      setJumpError(null)
      await flow.latest()
      nearBottom.current = true
      setTarget(undefined)
      placed.current = undefined
      setUnread(0)
      requestAnimationFrame(() => {
        if (scroller.current)
          scroller.current.scrollTop = scroller.current.scrollHeight
      })
    } catch (error) {
      setJumpError(
        error instanceof Error ? error : new Error('最新消息读取失败')
      )
    }
  }
  const locateMessage = flow.locate
  const locate = useCallback(
    (server?: string, messageId?: string) => {
      void locateMessage(server, messageId).then((found) => {
        if (found) {
          placed.current = undefined
          setTarget(found)
        }
      })
    },
    [locateMessage]
  )
  useEffect(() => {
    if (initialTarget) locate(initialTarget.server, initialTarget.messageId)
  }, [initialTarget, locate])
  return (
    <section
      className='conversation'
      aria-label={`${current.display_name}的聊天记录`}
    >
      <header className='conversation-heading'>
        <Button
          className='mobile-back'
          variant='ghost'
          size='icon'
          aria-label='返回会话列表'
          onClick={onBack}
        >
          <ArrowLeft />
        </Button>
        <div className='conversation-title'>
          <h2>{current.display_name}</h2>
        </div>
        <div className='conversation-actions'>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type='button'
                className='conversation-more'
                aria-label='聊天更多操作'
                title='更多'
              >
                <Ellipsis />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align='end'>
              <DropdownMenuItem
                onSelect={() => {
                  setSearchOpen(true)
                  setSearch('')
                }}
              >
                搜索当前已加载消息
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setInfoOpen(true)}>
                查看会话信息
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void latest()}>
                回到最新
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>
      {searchOpen && (
        <div className='message-search'>
          <Search size={16} />
          <Input
            autoFocus
            aria-label='搜索已加载消息'
            placeholder='搜索已加载消息的完整正文'
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <span>{matching.length} 条</span>
          <Button
            variant='ghost'
            size='icon'
            aria-label='关闭消息搜索'
            onClick={() => {
              setSearchOpen(false)
              setSearch('')
            }}
          >
            <X />
          </Button>
        </div>
      )}
      {flow.isError && flow.error && (
        <Notice error={flow.error} retry={() => void flow.refetch()} />
      )}
      {flow.pageError && (
        <Notice error={flow.pageError} retry={() => void older()} />
      )}
      {jumpError && <Notice error={jumpError} retry={() => void latest()} />}
      <div
        className='message-scroll'
        tabIndex={0}
        aria-label='聊天消息历史'
        onWheel={(event) => {
          if (event.deltaY < 0) upwardIntent.current = true
        }}
        onTouchMove={() => {
          upwardIntent.current = true
        }}
        onKeyDown={(event) => {
          if (['Home', 'PageUp', 'ArrowUp'].includes(event.key))
            upwardIntent.current = true
        }}
        ref={scroller}
        aria-busy={flow.isPending || flow.loadingOlder}
        onScroll={() => {
          const element = scroller.current
          if (!element || restoring.current) return
          nearBottom.current = isNearBottom(element)
          anchor.current = captureAnchor(element)
          if (nearBottom.current) setUnread(0)
          if (
            element.scrollTop < 50 &&
            upwardIntent.current &&
            !search &&
            flow.data?.has_more &&
            !flow.loadingOlder &&
            !flow.isFetching &&
            initialized.current &&
            !flow.pageError
          )
            void older()
        }}
      >
        <div ref={content}>
          {live.history_error && (
            <div className='history-note' role='status'>
              {live.history_error}
            </div>
          )}
          {flow.isPending ? (
            <div className='chat-state'>
              <LoaderCircle className='spin' />
              <p>读取聊天记录</p>
            </div>
          ) : (
            <>
              {flow.data?.has_more && (
                <div className='history-limit'>
                  <Button
                    variant='ghost'
                    size='sm'
                    disabled={flow.loadingOlder}
                    onClick={() => void older()}
                  >
                    {flow.loadingOlder ? '正在加载历史…' : '加载更早的消息'}
                  </Button>
                </div>
              )}
              {!matching.length && !outgoing.messages.length && (
                <div className='chat-state'>
                  <p>
                    {search
                      ? '已加载记录中没有匹配消息'
                      : '当前没有可显示的消息'}
                  </p>
                </div>
              )}
              {matching.map((message, index) => {
                const previousMessage = matching[index - 1]
                const separator =
                  !previousMessage ||
                  message.order_time - previousMessage.order_time > 300
                return (
                  <MessageRow
                    key={message.id}
                    account={live.account}
                    message={message}
                    separator={separator}
                    group={current.is_group}
                    selfName={profile?.display_name || '我'}
                    highlighted={message.id === target}
                    locate={locate}
                  />
                )
              })}
            </>
          )}
          {outgoing.messages.map((message) => (
            <PendingSendRow
              key={message.request}
              message={message}
              selfName={
                profile?.display_name || live.profile.display_name || '我'
              }
              avatarUrl={profile?.avatar_url || live.profile.avatar_url}
            />
          ))}
        </div>
      </div>
      {(unread > 0 || flow.data?.detached) && (
        <div className='new-message-notice'>
          <Button variant='secondary' size='sm' onClick={() => void latest()}>
            <ArrowDown size={14} />
            <span aria-live='polite'>
              {unread ? `${unread} 条新消息 · ` : ''}回到最新
            </span>
          </Button>
        </div>
      )}
      <Composer
        account={live.account}
        username={session.username}
        enabled={
          live.bridge_connected &&
          live.send_enabled === true &&
          current.can_send !== false
        }
        confirmedRequests={outgoing.confirmedRequests}
        onSearch={() => setSearchOpen(true)}
        onSubmit={(request, text) => {
          nearBottom.current = true
          setSearch('')
          setSearchOpen(false)
          setTarget(undefined)
          placed.current = undefined
          outgoing.submit(request, text)
          if (flow.data?.detached) void latest()
        }}
        onReceipt={(receipt) =>
          outgoing.update(receipt.request_id, receipt.status)
        }
        onUnknown={(request) => outgoing.update(request, 'unknown')}
      />
      <Sheet open={infoOpen} onOpenChange={setInfoOpen}>
        <SheetContent className='conversation-info'>
          <SheetHeader>
            <SheetTitle>会话信息</SheetTitle>
            <SheetDescription>
              当前登录账号的本地历史与实时消息
            </SheetDescription>
          </SheetHeader>
          <dl>
            <dt>微信标识</dt>
            <dd>{current.username}</dd>
            <dt>消息总数</dt>
            <dd>{current.message_count} 条</dd>
            <dt>当前记录</dt>
            <dd>
              已加载 {messages.length} 条 · 本页查询{' '}
              {flow.data?.elapsed_ms ?? 0} ms
            </dd>
            <dt>发送状态</dt>
            <dd>
              {current.can_send
                ? '发送受理不等于发送成功，以微信回显为准'
                : '仅历史会话，不在当前已核验发送名册'}
            </dd>
            <dt>数据来源</dt>
            <dd>
              {live.history_error ||
                (live.history_syncing
                  ? '后台同步历史，现有消息可正常阅读'
                  : '历史同步就绪')}
            </dd>
            <dt>来源校验</dt>
            <dd>活动微信进程核验、源数据库及已提交 WAL；不使用旧账号快照。</dd>
            <dt>媒体</dt>
            <dd>
              只读本机已有资源，不自动下载附件。文件以附件下载，不执行文件。
            </dd>
          </dl>
        </SheetContent>
      </Sheet>
    </section>
  )
}
