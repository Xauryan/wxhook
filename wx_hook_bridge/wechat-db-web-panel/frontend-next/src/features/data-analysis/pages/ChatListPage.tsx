import {
  type FormEvent,
  type ReactNode,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Link } from '@tanstack/react-router'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  ArrowLeft,
  CalendarDays,
  ChevronDown,
  ContactRound,
  ExternalLink,
  File,
  FileText,
  Image as ImageIcon,
  Info,
  Link2,
  Menu,
  Mic,
  MoreHorizontal,
  Search,
  Star,
  Video,
  X,
} from 'lucide-react'
import { CalendarHeatmap } from '../components/charts'
import { Avatar, EmptyState, ErrorState, LoadingState } from '../components/ui'
import {
  api,
  assetUrl,
  externalUrl,
  type ChatSessionDetailResponse,
  type ChatSessionItem,
  type ConversationMessage,
  type MediaItem,
} from '../lib/api'
import {
  formatClock,
  formatDateTime,
  formatNumber,
  safeText,
} from '../lib/format'
import { useAnalysisQuery as useQuery } from '../use-analysis-query'

type SessionScope = 'all' | 'groups' | 'contacts'
type DetailTab = 'profile' | 'stats' | 'files' | 'records'

const categoryLabels: Record<string, string> = {
  text: '文本',
  image: '图片',
  file: '文件',
  voice: '语音',
  video: '视频',
  link: '链接',
  quote: '引用',
  sticker: '表情',
  system: '系统消息',
  app: '应用消息',
  transfer: '转账',
  chat_history: '聊天记录',
  contact_card: '联系人名片',
  location: '位置',
  call: '通话',
  other: '其他',
}

function categoryLabel(value?: string): string {
  return categoryLabels[value || 'other'] || value || '其他'
}

function formatListTime(value?: string): string {
  if (!value) return ''
  const input = new Date(value)
  if (Number.isNaN(input.getTime())) return ''
  const now = new Date()
  if (input.toDateString() === now.toDateString()) return formatClock(value)
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (input.toDateString() === yesterday.toDateString()) return '昨天'
  if (input.getFullYear() === now.getFullYear())
    return `${input.getMonth() + 1}/${input.getDate()}`
  return `${input.getFullYear()}/${input.getMonth() + 1}/${input.getDate()}`
}

function formatDayHeading(value: string): string {
  const day = new Date(`${value}T12:00:00`)
  if (Number.isNaN(day.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(day)
}

function formatFileSize(value?: number): string {
  if (!value || value < 0) return '大小未知'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024)
    return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`
}

function messageText(message: ConversationMessage): string {
  return safeText(message.summary || message.content, '该消息暂时无法解析')
}

function cleanStructuredText(value?: string): string {
  const text = safeText(value, '原消息未在当前快照中解析')
  if (!/[<>]/.test(text)) return text
  return (
    text
      .replace(/<[^>]+>/g, ' ')
      .replace(
        /&(lt|gt|amp|quot|apos);/g,
        (_, entity: string) =>
          ({ lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" })[entity] || ' '
      )
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 220) || '结构化消息'
  )
}

function firstAvailableResource(
  message: ConversationMessage
): MediaItem | undefined {
  return message.resources?.find(
    (item) => item.resource_available !== false && item.url
  )
}

function linkHost(value?: string): string {
  if (!value) return '外部链接'
  try {
    return new URL(value).hostname || '外部链接'
  } catch {
    return '外部链接'
  }
}

function MessageBody({ message }: { message: ConversationMessage }) {
  const resource = firstAvailableResource(message)
  const resourceUrl = assetUrl(resource?.url)
  const summary = messageText(message)

  if (message.category === 'image' || message.category === 'sticker') {
    return resourceUrl && resource?.browser_supported !== false ? (
      <a
        className='chat-image-message'
        href={resourceUrl}
        target='_blank'
        rel='noreferrer'
      >
        <img
          src={resourceUrl}
          alt={summary === '[图片]' ? '聊天图片' : summary}
        />
      </a>
    ) : (
      <div className='chat-asset-placeholder'>
        <ImageIcon size={19} />
        <span>{summary}</span>
      </div>
    )
  }

  if (message.category === 'video') {
    return (
      <a
        className='chat-video-message'
        href={resourceUrl}
        target='_blank'
        rel='noreferrer'
      >
        <span>
          <Video size={22} />
        </span>
        <div>
          <b>{safeText(message.file_name, '视频消息')}</b>
          <small>{resourceUrl ? '打开本地视频' : '本地资源不可用'}</small>
        </div>
      </a>
    )
  }

  if (message.category === 'file' || message.category === 'chat_history') {
    const fileName = safeText(
      message.file_name || resource?.file_name || message.title,
      summary
    )
    return (
      <a
        className='chat-file-message'
        href={resourceUrl}
        target='_blank'
        rel='noreferrer'
      >
        <div>
          <b>{fileName}</b>
          <small>
            {formatFileSize(message.file_size || resource?.file_size)}
          </small>
        </div>
        <span>
          <FileText size={24} />
        </span>
      </a>
    )
  }

  if (message.category === 'link' && message.url) {
    return (
      <a
        className='chat-link-message'
        href={externalUrl(message.url)}
        target='_blank'
        rel='noreferrer'
      >
        <div>
          <b>{safeText(message.title, summary)}</b>
          <span>{safeText(message.description, linkHost(message.url))}</span>
          <small>{linkHost(message.url)}</small>
        </div>
        <ExternalLink size={18} />
      </a>
    )
  }

  if (message.category === 'voice') {
    return (
      <div className='chat-voice-message'>
        <Mic size={18} />
        <i>
          <span />
          <span />
          <span />
          <span />
          <span />
        </i>
        <small>语音消息</small>
      </div>
    )
  }

  return (
    <>
      <p>{summary}</p>
      {message.reference && (
        <blockquote>
          <b>{safeText(message.reference.display_name, '引用消息')}</b>
          <span>{cleanStructuredText(message.reference.content)}</span>
        </blockquote>
      )}
    </>
  )
}

function DetailMetric({
  label,
  value,
}: {
  label: string
  value: number | string
}) {
  return (
    <div className='chat-detail-metric'>
      <span>{label}</span>
      <b>{typeof value === 'number' ? formatNumber(value) : value}</b>
    </div>
  )
}

function SessionProfile({
  detail,
  selectedDate,
  onSelectDate,
  onSelectYear,
}: {
  detail: ChatSessionDetailResponse
  selectedDate?: string
  onSelectDate: (value: string) => void
  onSelectYear: (value: number) => void
}) {
  const session = detail.session
  const day = detail.selected_day
  const activePeriod =
    day.first_time && day.last_time
      ? `${formatClock(day.first_time)} - ${formatClock(day.last_time)}`
      : '当日无消息'

  return (
    <div className='chat-profile-view'>
      <section className='chat-contact-card'>
        <Avatar
          src={session.avatar_url}
          name={session.display_name}
          size={76}
        />
        <div>
          <h2>
            {session.display_name} <ContactRound size={16} />
          </h2>
          <dl>
            <div>
              <dt>微信号</dt>
              <dd>{session.username}</dd>
            </div>
            <div>
              <dt>备注</dt>
              <dd>{safeText(session.remark, '-')}</dd>
            </div>
            <div>
              <dt>首次互动</dt>
              <dd>{formatDateTime(session.first_interaction, true)}</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className='chat-profile-section chat-tag-section'>
        <header>
          <h3>标签</h3>
        </header>
        <div>
          {(session.tags || []).map((tag, index) => (
            <span key={tag} className={`tag-${index % 4}`}>
              {tag}
            </span>
          ))}
        </div>
      </section>

      <section className='chat-profile-section chat-profile-heatmap'>
        <header>
          <h3>
            <CalendarDays size={15} /> {detail.year} 年会话活跃
          </h3>
          <label>
            <select
              aria-label='选择热力图年份'
              value={detail.year}
              onChange={(event) => onSelectYear(Number(event.target.value))}
            >
              {detail.available_years.map((year) => (
                <option key={year} value={year}>
                  {year}年
                </option>
              ))}
            </select>
            <ChevronDown size={13} />
          </label>
        </header>
        <CalendarHeatmap
          data={detail.heatmap}
          year={detail.year}
          selected={selectedDate || day.date}
          onSelect={(point) => onSelectDate(point.date)}
          dense
        />
        <div className='chat-heat-legend'>
          <span>少</span>
          {[0, 1, 2, 3, 4, 5].map((level) => (
            <i key={level} className={`l${level}`} />
          ))}
          <span>多</span>
        </div>
        <div className='chat-overall-metrics'>
          <DetailMetric label='消息总数' value={detail.stats.messages} />
          <DetailMetric label='有效内容' value={detail.stats.quality} />
          <DetailMetric label='被引用' value={detail.stats.quoted} />
          <DetailMetric label='活跃天数' value={detail.stats.active_days} />
        </div>
      </section>

      <section className='chat-profile-section chat-selected-day'>
        <header>
          <div>
            <h3>{day.date ? formatDayHeading(day.date) : '未选择日期'}</h3>
            <p>活跃时段 {activePeriod}</p>
          </div>
          <button
            type='button'
            onClick={() => day.date && onSelectDate(day.date)}
            disabled={!day.date}
          >
            当前日期
          </button>
        </header>
        <div className='chat-day-metrics'>
          <DetailMetric label='消息' value={day.messages} />
          <DetailMetric label='我方发送' value={day.sent} />
          <DetailMetric
            label={session.is_group ? '群内发送' : '对方发送'}
            value={day.received}
          />
          <DetailMetric label='有效内容' value={day.quality} />
          <DetailMetric label='被引用' value={day.quoted} />
        </div>
        <p className='chat-day-summary'>
          {day.messages
            ? `${formatNumber(day.messages)} 条消息，${formatNumber(day.speakers)} 位发言者`
            : '该日期没有聊天记录'}
        </p>
      </section>

      <section className='chat-profile-section chat-note-section'>
        <header>
          <h3>备注</h3>
        </header>
        <textarea
          readOnly
          aria-label='只读备注'
          placeholder='源数据库为只读，未写入本地备注'
        />
      </section>
    </div>
  )
}

function SessionStats({ detail }: { detail: ChatSessionDetailResponse }) {
  const max = Math.max(1, ...detail.categories.map((item) => item.count))
  const session = detail.session
  return (
    <div className='chat-secondary-view'>
      <section className='chat-secondary-hero'>
        <h2>会话统计</h2>
        <p>统计范围为当前本地数据库快照中的全部真实消息。</p>
        <div>
          <DetailMetric label='我方发送' value={session.sent_count || 0} />
          <DetailMetric
            label={session.is_group ? '群内消息' : '对方发送'}
            value={session.received_count || 0}
          />
          <DetailMetric
            label='互动强度'
            value={`${(session.relationship_strength || 0).toFixed(1)}`}
          />
          <DetailMetric
            label='双向比例'
            value={`${Math.round((session.bidirectional_ratio || 0) * 100)}%`}
          />
        </div>
      </section>
      <section className='chat-category-stats'>
        <h3>消息类型</h3>
        {detail.categories.map((item) => (
          <div key={item.category}>
            <span>{categoryLabel(item.category)}</span>
            <i>
              <b
                style={{ width: `${Math.max(3, (item.count / max) * 100)}%` }}
              />
            </i>
            <em>{formatNumber(item.count)}</em>
          </div>
        ))}
      </section>
    </div>
  )
}

function SessionFiles({ detail }: { detail: ChatSessionDetailResponse }) {
  if (!detail.files.length)
    return <EmptyState title='该会话没有可展示的文件或媒体记录' />
  return (
    <div className='chat-file-view'>
      <header>
        <h2>文件与媒体</h2>
        <p>展示当前会话中最新的真实资源索引。</p>
      </header>
      <div>
        {detail.files.map((message) => {
          const resource = firstAvailableResource(message)
          const url = assetUrl(resource?.url)
          const content = (
            <>
              <span className={`chat-file-kind kind-${message.category}`}>
                {message.category === 'image' ? (
                  <ImageIcon size={19} />
                ) : message.category === 'video' ? (
                  <Video size={19} />
                ) : message.category === 'link' ? (
                  <Link2 size={19} />
                ) : (
                  <File size={19} />
                )}
              </span>
              <p>
                <b>
                  {safeText(
                    message.file_name || message.title,
                    messageText(message)
                  )}
                </b>
                <small>
                  {categoryLabel(message.category)} ·{' '}
                  {formatDateTime(message.create_time)}
                </small>
              </p>
              <em>{url ? <ExternalLink size={15} /> : '缺失'}</em>
            </>
          )
          return url || externalUrl(message.url) ? (
            <a
              key={message.id}
              href={url || externalUrl(message.url)}
              target='_blank'
              rel='noreferrer'
            >
              {content}
            </a>
          ) : (
            <div key={message.id}>{content}</div>
          )
        })}
      </div>
    </div>
  )
}

function SessionRecords({ detail }: { detail: ChatSessionDetailResponse }) {
  return (
    <div className='chat-record-view'>
      <header>
        <h2>消息记录</h2>
        <p>当前载入最近 {formatNumber(detail.messages.length)} 条消息。</p>
      </header>
      <div>
        {[...detail.messages].reverse().map((message) => (
          <article key={message.id}>
            <Avatar
              src={message.avatar_url}
              name={message.sender_name || detail.session.display_name}
              size={34}
            />
            <p>
              <b>
                {message.is_self
                  ? '我'
                  : safeText(message.sender_name, detail.session.display_name)}
              </b>
              <span>{messageText(message)}</span>
            </p>
            <time>{formatDateTime(message.create_time)}</time>
          </article>
        ))}
      </div>
    </div>
  )
}

function DetailPanel({
  content,
  tab,
  onTab,
  open,
  onClose,
}: {
  content: ReactNode
  tab: DetailTab
  onTab: (tab: DetailTab) => void
  open: boolean
  onClose: () => void
}) {
  const tabs: Array<[DetailTab, string]> = [
    ['profile', '资料'],
    ['stats', '统计'],
    ['files', '文件'],
    ['records', '记录'],
  ]
  return (
    <>
      <button
        className={`chat-detail-backdrop${open ? 'open' : ''}`}
        type='button'
        aria-label='关闭资料面板'
        onClick={onClose}
      />
      <aside className={`chat-detail-panel${open ? 'open' : ''}`}>
        <div className='chat-detail-tabs' role='tablist' aria-label='会话详情'>
          {tabs.map(([value, label]) => (
            <button
              role='tab'
              aria-selected={tab === value}
              className={tab === value ? 'active' : ''}
              type='button'
              key={value}
              onClick={() => onTab(value)}
            >
              {label}
            </button>
          ))}
          <button
            className='chat-detail-close'
            type='button'
            aria-label='关闭资料面板'
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>
        <div className='chat-detail-scroll'>{content}</div>
      </aside>
    </>
  )
}

export function ChatListPage() {
  const [scope, setScope] = useState<SessionScope>('all')
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search.trim())
  const [selectedUsername, setSelectedUsername] = useState<string>()
  const [selectedYear, setSelectedYear] = useState<number>()
  const [selectedDate, setSelectedDate] = useState<string>()
  const [detailTab, setDetailTab] = useState<DetailTab>('profile')
  const [mobileChatOpen, setMobileChatOpen] = useState(false)
  const [mobileDetailsOpen, setMobileDetailsOpen] = useState(false)
  const sessionListRef = useRef<HTMLDivElement>(null)
  const messageScrollRef = useRef<HTMLDivElement>(null)
  const messageContentRef = useRef<HTMLDivElement>(null)

  const sessionsQuery = useQuery({
    queryKey: ['chat-list', scope, deferredSearch],
    queryFn: ({ signal }) =>
      api.chatList(
        { scope, q: deferredSearch || undefined, limit: 160 },
        signal
      ),
  })
  const detailQuery = useQuery({
    queryKey: ['chat-session', selectedUsername, selectedYear, selectedDate],
    queryFn: ({ signal }) =>
      api.chatSession(
        selectedUsername!,
        { limit: 180, year: selectedYear, date: selectedDate },
        signal
      ),
    enabled: Boolean(selectedUsername),
  })

  const sessions = useMemo(
    () => sessionsQuery.data?.items || [],
    [sessionsQuery.data?.items]
  )
  const selectedSession = sessions.find(
    (item) => item.username === selectedUsername
  )
  const detail = detailQuery.data
  const sessionVirtualizer = useVirtualizer({
    count: sessions.length,
    getScrollElement: () => sessionListRef.current,
    estimateSize: () => 74,
    overscan: 8,
  })

  useEffect(() => {
    if (!sessions.length) return
    if (
      !selectedUsername ||
      !sessions.some((item) => item.username === selectedUsername)
    ) {
      setSelectedUsername(sessions[0].username)
      setSelectedYear(undefined)
      setSelectedDate(undefined)
    }
  }, [sessions, selectedUsername])

  useEffect(() => {
    const node = messageScrollRef.current
    const content = messageContentRef.current
    if (!node || !content || !detail?.messages.length) return

    let followingLatest = true
    let frame = 0
    const scrollToLatest = () => {
      frame = 0
      if (followingLatest) node.scrollTop = node.scrollHeight
    }
    const scheduleScroll = () => {
      if (frame) cancelAnimationFrame(frame)
      frame = requestAnimationFrame(scrollToLatest)
    }
    const stopFollowing = () => {
      followingLatest = false
    }
    const resizeObserver =
      typeof ResizeObserver === 'undefined'
        ? undefined
        : new ResizeObserver(scheduleScroll)

    scheduleScroll()
    resizeObserver?.observe(content)
    node.addEventListener('wheel', stopFollowing, { passive: true })
    node.addEventListener('touchstart', stopFollowing, { passive: true })
    node.addEventListener('pointerdown', stopFollowing, { passive: true })
    const timeout = window.setTimeout(() => resizeObserver?.disconnect(), 1800)

    return () => {
      followingLatest = false
      if (frame) cancelAnimationFrame(frame)
      window.clearTimeout(timeout)
      resizeObserver?.disconnect()
      node.removeEventListener('wheel', stopFollowing)
      node.removeEventListener('touchstart', stopFollowing)
      node.removeEventListener('pointerdown', stopFollowing)
    }
  }, [selectedUsername, detail?.messages, detail?.messages.length])

  useEffect(() => {
    sessionListRef.current?.scrollTo({ top: 0 })
  }, [scope, deferredSearch])

  const messageRows = useMemo(() => {
    let previousDate = ''
    return (detail?.messages || []).map((message) => {
      const date = message.create_time?.slice(0, 10) || '未知日期'
      const showDate = date !== previousDate
      previousDate = date
      return { message, date, showDate }
    })
  }, [detail?.messages])

  function submitSearch(event: FormEvent) {
    event.preventDefault()
  }

  function chooseSession(item: ChatSessionItem) {
    setSelectedUsername(item.username)
    setSelectedYear(undefined)
    setSelectedDate(undefined)
    setDetailTab('profile')
    setMobileChatOpen(true)
  }

  const detailContent = detailQuery.isLoading ? (
    <LoadingState label='正在读取会话资料' />
  ) : detailQuery.isError ? (
    <ErrorState error={detailQuery.error} retry={() => detailQuery.refetch()} />
  ) : detail ? (
    detailTab === 'profile' ? (
      <SessionProfile
        detail={detail}
        selectedDate={selectedDate}
        onSelectDate={setSelectedDate}
        onSelectYear={(year) => {
          setSelectedYear(year)
          setSelectedDate(undefined)
        }}
      />
    ) : detailTab === 'stats' ? (
      <SessionStats detail={detail} />
    ) : detailTab === 'files' ? (
      <SessionFiles detail={detail} />
    ) : (
      <SessionRecords detail={detail} />
    )
  ) : (
    <EmptyState title='请选择一个会话' />
  )

  return (
    <div className='chat-list-app'>
      <div
        className={`chat-list-workspace${mobileChatOpen ? 'mobile-chat-open' : ''}`}
      >
        <section className='chat-session-panel'>
          <header>
            <h1>会话统计</h1>
            <button type='button' aria-label='会话统计菜单'>
              <Menu size={19} />
            </button>
          </header>
          <form className='chat-session-search' onSubmit={submitSearch}>
            <Search size={16} />
            <input
              aria-label='搜索会话或联系人'
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder='搜索会话或联系人'
            />
          </form>
          <div
            className='chat-session-tabs'
            role='tablist'
            aria-label='会话类型'
          >
            {(
              [
                ['all', '全部', sessionsQuery.data?.counts.all],
                ['groups', '群聊', sessionsQuery.data?.counts.groups],
                ['contacts', '联系人', sessionsQuery.data?.counts.contacts],
              ] as Array<[SessionScope, string, number | undefined]>
            ).map(([value, label, count]) => (
              <button
                key={value}
                type='button'
                role='tab'
                aria-selected={scope === value}
                className={scope === value ? 'active' : ''}
                onClick={() => setScope(value)}
              >
                {label} <span>{formatNumber(count)}</span>
              </button>
            ))}
          </div>
          <div
            className='chat-session-list'
            ref={sessionListRef}
            role='listbox'
            aria-label='真实会话统计'
          >
            {sessionsQuery.isLoading ? (
              <LoadingState label='正在读取会话统计' />
            ) : sessionsQuery.isError ? (
              <ErrorState
                error={sessionsQuery.error}
                retry={() => sessionsQuery.refetch()}
              />
            ) : sessions.length ? (
              <div
                className='chat-session-virtual'
                style={{
                  height: sessionVirtualizer.getTotalSize(),
                  position: 'relative',
                }}
              >
                {sessionVirtualizer.getVirtualItems().map((virtualRow) => {
                  const item = sessions[virtualRow.index]
                  return (
                    <button
                      key={item.username}
                      type='button'
                      role='option'
                      aria-selected={selectedUsername === item.username}
                      className={`chat-session-option${selectedUsername === item.username ? 'active' : ''}`}
                      style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        transform: `translateY(${virtualRow.start}px)`,
                      }}
                      onClick={() => chooseSession(item)}
                    >
                      <Avatar
                        src={item.avatar_url}
                        name={item.display_name}
                        size={42}
                      />
                      <span className='chat-session-copy'>
                        <b>{item.display_name}</b>
                        <small>
                          {item.latest_is_self ? '我：' : ''}
                          {safeText(
                            item.summary,
                            `[${categoryLabel(item.latest_category)}]`
                          )}
                        </small>
                      </span>
                      <time>{formatListTime(item.last_active)}</time>
                    </button>
                  )
                })}
              </div>
            ) : (
              <EmptyState
                title='没有匹配的真实会话'
                detail='请调整关键词或会话类型筛选。'
              />
            )}
          </div>
        </section>

        <section className='chat-conversation-panel' aria-label='当前会话'>
          <header className='chat-conversation-header'>
            <button
              className='chat-mobile-back'
              type='button'
              aria-label='返回会话统计'
              onClick={() => setMobileChatOpen(false)}
            >
              <ArrowLeft size={20} />
            </button>
            {selectedSession || detail?.session ? (
              <Avatar
                src={(detail?.session || selectedSession)?.avatar_url}
                name={(detail?.session || selectedSession)?.display_name}
                size={49}
              />
            ) : null}
            <div>
              <h2>
                {safeText(
                  detail?.session.display_name || selectedSession?.display_name,
                  '请选择会话'
                )}{' '}
                <Star size={17} />
              </h2>
              <p>
                {detail?.session.is_group
                  ? `${formatNumber(detail.session.message_count)} 条群聊消息`
                  : `微信号：${safeText(detail?.session.username || selectedSession?.username, '-')}`}
              </p>
            </div>
            <button
              type='button'
              aria-label='打开会话资料'
              onClick={() => setMobileDetailsOpen(true)}
            >
              <Info size={18} />
            </button>
            <button type='button' aria-label='更多会话操作'>
              <MoreHorizontal size={21} />
            </button>
          </header>

          <div className='chat-message-scroll' ref={messageScrollRef}>
            <div className='chat-message-content' ref={messageContentRef}>
              {detailQuery.isLoading ? (
                <LoadingState label='正在读取真实消息' />
              ) : detailQuery.isError ? (
                <ErrorState
                  error={detailQuery.error}
                  retry={() => detailQuery.refetch()}
                />
              ) : messageRows.length ? (
                messageRows.map(({ message, date, showDate }) => (
                  <div className='chat-message-row-wrap' key={message.id}>
                    {showDate && (
                      <div className='chat-date-divider'>
                        <span>{formatDayHeading(date)}</span>
                      </div>
                    )}
                    {message.category === 'system' ? (
                      <div className='chat-system-message'>
                        {messageText(message)}
                      </div>
                    ) : (
                      <article
                        className={`chat-message-row${message.is_self ? 'self' : ''}`}
                      >
                        <Avatar
                          src={message.avatar_url}
                          name={
                            message.sender_name || detail?.session.display_name
                          }
                          size={35}
                        />
                        <div className='chat-message-stack'>
                          {!message.is_self && detail?.session.is_group && (
                            <b className='chat-sender-name'>
                              {safeText(
                                message.sender_name,
                                detail.session.display_name
                              )}
                            </b>
                          )}
                          <div
                            className={`chat-message-bubble category-${message.category || 'other'}`}
                          >
                            <MessageBody message={message} />
                          </div>
                          <time>{formatClock(message.create_time)}</time>
                        </div>
                      </article>
                    )}
                  </div>
                ))
              ) : (
                <EmptyState title='该会话没有可展示的消息' />
              )}
            </div>
          </div>

          <footer className='p-4'>
            <Link
              to='/chats'
              className='inline-flex rounded-md border bg-background px-4 py-2 text-sm'
            >
              打开微信聊天
            </Link>
          </footer>
        </section>

        <DetailPanel
          content={detailContent}
          tab={detailTab}
          onTab={setDetailTab}
          open={mobileDetailsOpen}
          onClose={() => setMobileDetailsOpen(false)}
        />
      </div>
    </div>
  )
}
