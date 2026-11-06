import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  CalendarDays,
  ChevronDown,
  Download,
  FileText,
  Image as ImageIcon,
  Search,
  X,
} from 'lucide-react'
import type { ShellContext } from '../components/AppShell'
import { TrendChart } from '../components/charts'
import {
  Avatar,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  ProfileBanner,
  Pill,
  SectionLink,
} from '../components/ui'
import {
  api,
  assetUrl,
  conversationExportUrl,
  type ConversationEpisode,
} from '../lib/api'
import {
  formatClock,
  formatDateTime,
  formatNumber,
  safeText,
} from '../lib/format'
import { useOutletContext, useSearchParams } from '../router'
import { useAnalysisQuery as useQuery } from '../use-analysis-query'

type TimelineRow =
  | { type: 'date'; date: string; count: number }
  | { type: 'episode'; episode: ConversationEpisode }

export function ConversationsPage() {
  const { profile, profileResponse } = useOutletContext<ShellContext>()
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState(searchParams.get('q') ?? '')
  const [selectedEpisode, setSelectedEpisode] = useState<ConversationEpisode>()
  const [jumpingMessageId, setJumpingMessageId] = useState<string>()
  const [referenceJumpError, setReferenceJumpError] = useState<string>()
  const [exportOpen, setExportOpen] = useState(false)
  const parentRef = useRef<HTMLDivElement>(null)
  const drawerTrigger = useRef<HTMLButtonElement | null>(null)
  const drawerClose = useRef<HTMLButtonElement>(null)
  const range = searchParams.get('range')
  const derivedRange = useMemo(() => {
    if (range !== '30' && range !== '90') return {}
    const end = new Date()
    const start = new Date(end)
    start.setDate(start.getDate() - Number(range) + 1)
    const key = (date: Date) =>
      `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    return { start_date: key(start), end_date: key(end) }
  }, [range])
  const filters = {
    q: searchParams.get('q') ?? undefined,
    date: searchParams.get('date') ?? undefined,
    message: searchParams.get('message') ?? undefined,
    session: searchParams.get('session') ?? undefined,
    category: searchParams.get('category') ?? undefined,
    start_date: searchParams.get('start_date') ?? derivedRange.start_date,
    end_date: searchParams.get('end_date') ?? derivedRange.end_date,
    hour: searchParams.get('hour') ?? undefined,
    order: searchParams.get('order') ?? 'desc',
  }
  const query = useQuery({
    queryKey: ['conversations', filters],
    queryFn: ({ signal }) =>
      api.conversations({ ...filters, limit: 250, offset: 0 }, signal),
  })
  const detailQuery = useQuery({
    queryKey: ['conversation-detail', selectedEpisode?.id],
    queryFn: ({ signal }) => api.conversation(selectedEpisode!.id, signal),
    enabled: selectedEpisode !== undefined,
  })
  const items = useMemo(() => query.data?.items ?? [], [query.data?.items])
  const rows = useMemo<TimelineRow[]>(() => {
    const result: TimelineRow[] = []
    let currentDate = ''
    items.forEach((episode) => {
      if (episode.date !== currentDate) {
        currentDate = episode.date
        result.push({
          type: 'date',
          date: currentDate,
          count: items.filter((item) => item.date === currentDate).length,
        })
      }
      result.push({ type: 'episode', episode })
    })
    return result
  }, [items])
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (index) => (rows[index]?.type === 'date' ? 46 : 72),
    overscan: 8,
  })

  function closeDrawer() {
    setSelectedEpisode(undefined)
    requestAnimationFrame(() => drawerTrigger.current?.focus())
  }

  async function jumpToReferencedMessage(messageId: string) {
    setJumpingMessageId(messageId)
    setReferenceJumpError(undefined)
    try {
      const detail = await api.message(messageId)
      const original = detail.referenced_message
      if (!original?.id) throw new Error('原消息未在当前快照中找到')
      const next = new URLSearchParams(searchParams)
      next.set('message', original.id)
      if (original.session_username)
        next.set('session', original.session_username)
      if (original.create_time)
        next.set('date', original.create_time.slice(0, 10))
      setSearchParams(next)
      closeDrawer()
      requestAnimationFrame(() => parentRef.current?.scrollTo({ top: 0 }))
    } catch (error) {
      setReferenceJumpError(
        error instanceof Error ? error.message : '无法定位原消息'
      )
    } finally {
      setJumpingMessageId(undefined)
    }
  }

  useEffect(() => {
    if (!selectedEpisode) return
    drawerClose.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeDrawer()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [selectedEpisode])

  function submit(event: FormEvent) {
    event.preventDefault()
    const next = new URLSearchParams(searchParams)
    if (search.trim()) next.set('q', search.trim())
    else next.delete('q')
    setSearchParams(next)
  }

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next)
  }

  if (query.isLoading) return <LoadingState label='正在生成会话时间线' />
  if (query.isError)
    return <ErrorState error={query.error} retry={() => query.refetch()} />

  const kpis = {
    ...(profileResponse?.stats ?? {}),
    ...(query.data?.kpis ?? {}),
  }
  const maxHourly = Math.max(
    1,
    ...(query.data?.hourly?.map((item) => item.count) ?? [1])
  )

  return (
    <div className='page conversations-page'>
      <ProfileBanner
        profile={profile}
        subtitle='本机资料 / 个人档案'
        tags={['联系人', '本地消息', '活跃用户']}
        metrics={[
          {
            label: '会话总数',
            value: formatNumber(kpis.sessions ?? query.data?.total),
            suffix: '次',
          },
          {
            label: '高峰时段',
            value: safeText(kpis.peak_period),
            note: '活跃最高',
          },
          {
            label: '平均每次会话轮数',
            value:
              typeof kpis.average_turns === 'number'
                ? kpis.average_turns.toFixed(1)
                : '—',
            suffix: '轮',
            note: '连续同发送者合并',
          },
          {
            label: '被引用消息',
            value: formatNumber(kpis.quoted_messages),
            suffix: '条',
            note: '被他人引用',
          },
        ]}
      />

      <div className='conversation-filters'>
        <form onSubmit={submit}>
          <Search size={17} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder='搜索会话内容'
          />
        </form>
        <label className='date-range'>
          <input
            type='date'
            value={filters.start_date ?? ''}
            onChange={(event) => setFilter('start_date', event.target.value)}
          />
          <span>~</span>
          <input
            type='date'
            value={filters.end_date ?? ''}
            onChange={(event) => setFilter('end_date', event.target.value)}
          />
          <CalendarDays size={15} />
        </label>
        <label className='filter-select'>
          会话范围
          <select
            value={filters.session ?? ''}
            onChange={(event) => setFilter('session', event.target.value)}
          >
            <option value=''>全部会话</option>
            {[
              ...new Map(
                items.map((item) => [item.session_username, item.session_name])
              ).entries(),
            ]
              .filter(([id]) => id)
              .map(([id, name]) => (
                <option key={id} value={id}>
                  {name || id}
                </option>
              ))}
          </select>
          <ChevronDown size={14} />
        </label>
        <div className='type-tabs'>
          {[
            ['', '全部'],
            ['text', '文本'],
            ['image', '图片'],
            ['file', '文件'],
            ['voice', '语音'],
            ['video', '视频'],
            ['link', '链接'],
          ].map(([value, label]) => (
            <button
              type='button'
              className={(filters.category ?? '') === value ? 'active' : ''}
              key={label}
              onClick={() => setFilter('category', value)}
            >
              {label}
            </button>
          ))}
        </div>
        <label className='filter-select compact'>
          <select
            value={filters.order}
            onChange={(event) => setFilter('order', event.target.value)}
          >
            <option value='desc'>按时间倒序</option>
            <option value='asc'>按时间正序</option>
          </select>
          <ChevronDown size={14} />
        </label>
        <div className='export-menu'>
          <button
            type='button'
            aria-label='导出当前筛选'
            onClick={() => setExportOpen((value) => !value)}
          >
            <Download size={17} />
          </button>
          {exportOpen && (
            <div>
              {(['csv', 'json', 'html'] as const).map((format) => (
                <button
                  type='button'
                  key={format}
                  onClick={() => {
                    window.location.assign(
                      conversationExportUrl(format, {
                        date: filters.date,
                        start_date: filters.start_date,
                        end_date: filters.end_date,
                        session: filters.session,
                        category: filters.category,
                        q: filters.q,
                        hour: filters.hour,
                        order: filters.order,
                      })
                    )
                    setExportOpen(false)
                  }}
                >
                  {format.toUpperCase()}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className='conversation-layout'>
        <Card className='timeline-card'>
          {rows.length ? (
            <div className='timeline-scroll' ref={parentRef}>
              <div
                style={{
                  height: virtualizer.getTotalSize(),
                  position: 'relative',
                }}
              >
                {virtualizer.getVirtualItems().map((virtualRow) => {
                  const row = rows[virtualRow.index]
                  return (
                    <div
                      key={virtualRow.key}
                      className={`virtual-row ${row.type}`}
                      style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        width: '100%',
                        height: virtualRow.size,
                        transform: `translateY(${virtualRow.start}px)`,
                      }}
                    >
                      {row.type === 'date' ? (
                        <div className='date-heading'>
                          <b>
                            {row.date}{' '}
                            {new Intl.DateTimeFormat('zh-CN', {
                              weekday: 'long',
                            }).format(new Date(`${row.date}T12:00:00`))}
                          </b>
                          <span>会话 {formatNumber(row.count)} 次</span>
                        </div>
                      ) : (
                        <button
                          type='button'
                          className='episode-row'
                          onClick={(event) => {
                            drawerTrigger.current = event.currentTarget
                            setSelectedEpisode(row.episode)
                          }}
                        >
                          <time>{formatClock(row.episode.start_time)}</time>
                          <i className='timeline-dot' />
                          <Avatar
                            src={row.episode.avatar_url}
                            name={
                              row.episode.session_name ||
                              row.episode.session_username
                            }
                            size={30}
                          />
                          <b>
                            {row.episode.session_name ||
                              row.episode.session_username ||
                              '未知会话'}
                          </b>
                          <span>
                            {safeText(row.episode.summary, '暂无可解码摘要')}
                          </span>
                          {row.episode.media?.length ? (
                            <div className='episode-media'>
                              {row.episode.media
                                .slice(0, 2)
                                .map((media) =>
                                  media.browser_supported !== false &&
                                  media.thumbnail_url ? (
                                    <img
                                      key={media.id}
                                      src={assetUrl(media.thumbnail_url)}
                                      alt='消息媒体缩略图'
                                    />
                                  ) : (
                                    <ImageIcon key={media.id} size={16} />
                                  )
                                )}
                            </div>
                          ) : null}
                          <Pill>{formatNumber(row.episode.turn_count)} 轮</Pill>
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          ) : (
            <EmptyState title='当前筛选没有会话记录' />
          )}
          <div className='timeline-note'>
            列表按会话间隔规则分组，媒体缺失时仅保留真实索引状态
          </div>
        </Card>

        <aside className='conversation-insights'>
          <Card
            title={
              <>
                消息活跃时段 <small>(按小时)</small>{' '}
                <span className='info-dot'>i</span>
              </>
            }
          >
            {query.data?.hourly?.length ? (
              <div className='hour-strip'>
                {Array.from({ length: 24 }, (_, hour) => {
                  const count =
                    query.data?.hourly?.find((item) => item.hour === hour)
                      ?.count ?? 0
                  return (
                    <button
                      type='button'
                      className={filters.hour === String(hour) ? 'active' : ''}
                      aria-label={`筛选 ${hour} 时，${count} 条消息`}
                      key={hour}
                      style={{ opacity: 0.12 + (count / maxHourly) * 0.88 }}
                      title={`${hour}:00 ${count} 条`}
                      onClick={() =>
                        setFilter(
                          'hour',
                          filters.hour === String(hour) ? '' : String(hour)
                        )
                      }
                    />
                  )
                })}
                <div>
                  {[0, 3, 6, 9, 12, 15, 18, 21, 23].map((hour) => (
                    <span key={hour}>{hour}</span>
                  ))}
                </div>
              </div>
            ) : (
              <EmptyState title='暂无小时聚合' />
            )}
          </Card>
          <Card
            title='会话频次趋势'
            action={<span className='select-chip'>近 30 天</span>}
          >
            <TrendChart
              data={query.data?.trend}
              height={185}
              label='近 30 天会话频次'
              onSelectDate={(date) => setFilter('date', date)}
            />
          </Card>
          <Card
            title='高频被引用的消息 TOP5'
            action={<SectionLink>查看全部</SectionLink>}
          >
            {query.data?.top_quoted?.length ? (
              <div className='quoted-list'>
                {query.data.top_quoted.slice(0, 5).map((item, index) => (
                  <button
                    type='button'
                    key={safeText(item.id, String(index))}
                    onClick={() => {
                      setFilter('message', safeText(item.id, ''))
                      parentRef.current?.scrollTo({
                        top: 0,
                        behavior: 'smooth',
                      })
                    }}
                  >
                    <em>{index + 1}</em>
                    <Avatar
                      src={safeText(item.avatar_url, '')}
                      name={safeText(
                        item.sender_name || item.session_name,
                        '消息发送者'
                      )}
                      size={25}
                    />
                    <b>
                      {safeText(
                        item.sender_name || item.session_name,
                        '未知发送者'
                      )}
                    </b>
                    <span>
                      {safeText(item.summary || item.content, '无可解码摘要')}
                    </span>
                    <small>被引用 {formatNumber(item.quote_count)} 次</small>
                    <time>{formatDateTime(item.create_time)}</time>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyState title='暂无可解析引用消息' />
            )}
          </Card>
        </aside>
      </div>

      {selectedEpisode && (
        <div className='drawer-scrim' onClick={closeDrawer}>
          <aside
            className='episode-drawer'
            role='dialog'
            aria-modal='true'
            aria-label='完整会话上下文'
            onClick={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <Avatar
                  src={selectedEpisode.avatar_url}
                  name={selectedEpisode.session_name}
                  size={38}
                />
                <span>
                  <b>
                    {selectedEpisode.session_name ||
                      selectedEpisode.session_username}
                  </b>
                  <small>
                    {selectedEpisode.date}{' '}
                    {formatClock(selectedEpisode.start_time)} -{' '}
                    {formatClock(selectedEpisode.end_time)}
                  </small>
                </span>
              </div>
              <button
                ref={drawerClose}
                type='button'
                onClick={closeDrawer}
                aria-label='关闭'
              >
                <X />
              </button>
            </header>
            <div className='drawer-body'>
              <h3>完整消息上下文</h3>
              <p>
                {safeText(
                  selectedEpisode.summary,
                  '该会话没有可解码文本摘要。'
                )}
              </p>
              <dl>
                <div>
                  <dt>消息数</dt>
                  <dd>{formatNumber(selectedEpisode.message_count)}</dd>
                </div>
                <div>
                  <dt>会话轮次</dt>
                  <dd>{formatNumber(selectedEpisode.turn_count)}</dd>
                </div>
                <div>
                  <dt>内容类型</dt>
                  <dd>{selectedEpisode.categories?.join('、') || '—'}</dd>
                </div>
              </dl>
              {referenceJumpError && (
                <div className='reference-error'>{referenceJumpError}</div>
              )}
              {detailQuery.isLoading ? (
                <LoadingState label='正在读取完整消息' />
              ) : detailQuery.isError ? (
                <ErrorState
                  error={detailQuery.error}
                  retry={() => detailQuery.refetch()}
                />
              ) : detailQuery.data?.messages.length ? (
                <div className='drawer-messages'>
                  {detailQuery.data.messages.map((message) => (
                    <article
                      key={message.id}
                      className={message.is_self ? 'self' : 'other'}
                    >
                      <Avatar
                        src={message.avatar_url}
                        name={message.sender_name}
                        size={28}
                      />
                      <div>
                        <header>
                          <b>
                            {message.sender_name ||
                              (message.is_self ? '我' : '未知发送者')}
                          </b>
                          <time>{formatDateTime(message.create_time)}</time>
                        </header>
                        <p>
                          {safeText(
                            message.content || message.summary,
                            message.decoded === false
                              ? `内容解码失败：${safeText(message.decode_error, '未知原因')}`
                              : '无文本内容'
                          )}
                        </p>
                        {message.reference && (
                          <blockquote>
                            <b>
                              {message.reference.display_name || '引用消息'}
                            </b>
                            <span>
                              {safeText(
                                message.reference.content,
                                '引用原文不可用'
                              )}
                            </span>
                            <small>
                              引用链：当前消息 → 原消息{' '}
                              {safeText(message.reference.server_id)}
                            </small>
                            <button
                              type='button'
                              disabled={jumpingMessageId === message.id}
                              onClick={() =>
                                jumpToReferencedMessage(message.id)
                              }
                            >
                              {jumpingMessageId === message.id
                                ? '定位中'
                                : '跳到原消息'}
                            </button>
                          </blockquote>
                        )}
                        {message.resources?.length ? (
                          <div className='message-resources'>
                            {message.resources.map((resource) =>
                              resource.browser_supported !== false &&
                              resource.thumbnail_url ? (
                                <img
                                  key={resource.id}
                                  src={assetUrl(resource.thumbnail_url)}
                                  alt={resource.file_name || '消息资源'}
                                />
                              ) : (
                                <span key={resource.id}>
                                  <FileText size={14} />
                                  {resource.file_name ||
                                    (resource.browser_supported === false
                                      ? 'DAT 图片不可预览'
                                      : '资源不可用')}
                                </span>
                              )
                            )}
                          </div>
                        ) : null}
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <EmptyState title='该会话没有消息上下文' />
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
