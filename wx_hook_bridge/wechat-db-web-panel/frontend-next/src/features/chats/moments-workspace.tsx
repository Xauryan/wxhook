import { memo, useDeferredValue, useEffect, useRef, useState } from 'react'
import {
  Aperture,
  Bell,
  Camera,
  ChevronLeft,
  ChevronRight,
  Heart,
  ImageOff,
  LoaderCircle,
  MapPin,
  MessageCircle,
  MoreHorizontal,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { localAsset, type LiveStatus } from './api'
import { ContactAvatar, Notice } from './chat-ui'
import { MomentsApiError, type Moment, type MomentMedia } from './moments-api'
import './moments.css'
import { useMomentsFeed } from './use-moments-feed'
import { WechatText } from './wechat-text'

function momentDate(timestamp: number) {
  const date = new Date(timestamp * 1000)
  return timestamp > 0 && Number.isFinite(date.getTime()) ? date : undefined
}

function momentTime(timestamp: number) {
  const date = momentDate(timestamp)
  if (!date) return '时间未知'
  const elapsed = (Date.now() - timestamp * 1000) / 1000
  if (elapsed >= 0 && elapsed < 60) return '刚刚'
  if (elapsed >= 60 && elapsed < 3600)
    return `${Math.floor(elapsed / 60)}分钟前`
  if (elapsed >= 3600 && elapsed < 86400)
    return `${Math.floor(elapsed / 3600)}小时前`
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    ...(new Date(timestamp * 1000).getFullYear() !== new Date().getFullYear()
      ? { year: 'numeric' as const }
      : {}),
  })
}

function MomentImage({
  media,
  label,
  original = false,
}: {
  media: MomentMedia
  label: string
  original?: boolean
}) {
  const [failed, setFailed] = useState(false)
  const [nearViewport, setNearViewport] = useState(
    () => original || typeof IntersectionObserver === 'undefined'
  )
  const element = useRef<HTMLImageElement>(null)
  const url = localAsset(
    (original
      ? media.url || media.thumbnail_url
      : media.thumbnail_url || media.url) ?? undefined
  )
  useEffect(() => {
    if (nearViewport || !element.current) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setNearViewport(true)
          observer.disconnect()
        }
      },
      { rootMargin: '100px' }
    )
    observer.observe(element.current)
    return () => observer.disconnect()
  }, [nearViewport, url])
  return url && !failed ? (
    <img
      ref={element}
      src={nearViewport ? url : undefined}
      style={nearViewport ? undefined : { visibility: 'hidden' }}
      width={media.width && media.width > 0 ? media.width : undefined}
      height={media.height && media.height > 0 ? media.height : undefined}
      alt={label}
      loading={original ? 'eager' : 'lazy'}
      decoding='async'
      referrerPolicy='no-referrer'
      onError={() => setFailed(true)}
    />
  ) : (
    <span className='moment-media-unavailable'>
      <ImageOff size={24} />
      <span>
        {failed ? '图片读取失败' : media.detail || '图片未在本地缓存'}
      </span>
    </span>
  )
}

function MomentGallery({ items }: { items: MomentMedia[] }) {
  const [selected, setSelected] = useState<number | null>(null)
  const images = items.filter((media) => media.kind === 'image')
  const image = selected === null ? undefined : images[selected]
  return (
    <>
      <div
        className={`moment-media-grid ${items.length === 1 ? 'is-single' : ''} ${items.length === 4 ? 'is-four' : ''}`}
      >
        {items.map((media, index) =>
          media.kind === 'image' ? (
            <button
              key={media.id || index}
              type='button'
              className='moment-image'
              aria-label={`查看朋友圈图片 ${index + 1}`}
              onClick={() => setSelected(images.indexOf(media))}
            >
              <MomentImage
                key={`${media.url}:${media.thumbnail_url}`}
                media={media}
                label={`朋友圈图片 ${index + 1}`}
              />
            </button>
          ) : media.kind === 'video' && localAsset(media.url ?? undefined) ? (
            <MomentVideo
              key={`${media.id || index}:${media.url}`}
              media={media}
            />
          ) : (
            <div key={media.id || index} className='moment-media-unavailable'>
              <ImageOff size={24} />
              <span>{media.detail || '媒体未在本地缓存'}</span>
            </div>
          )
        )}
      </div>
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
      >
        <DialogContent className='moment-image-dialog'>
          <DialogHeader>
            <DialogTitle>朋友圈图片</DialogTitle>
            <DialogDescription>
              仅显示当前账号已缓存的本地图片
              {image && !image.url ? '，当前为缩略图' : ''}。
            </DialogDescription>
          </DialogHeader>
          {image && (
            <div className='moment-image-preview'>
              <MomentImage
                key={`${selected}:${image.url}:${image.thumbnail_url}`}
                media={image}
                label='朋友圈图片预览'
                original
              />
            </div>
          )}
          {images.length > 1 && (
            <div className='moment-image-controls'>
              <Button
                variant='outline'
                aria-label='上一张图片'
                disabled={selected === 0}
                onClick={() =>
                  setSelected((value) => Math.max(0, (value ?? 0) - 1))
                }
              >
                <ChevronLeft size={18} />
              </Button>
              <span>
                {(selected ?? 0) + 1} / {images.length}
              </span>
              <Button
                variant='outline'
                aria-label='下一张图片'
                disabled={selected === images.length - 1}
                onClick={() =>
                  setSelected((value) =>
                    Math.min(images.length - 1, (value ?? 0) + 1)
                  )
                }
              >
                <ChevronRight size={18} />
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

function MomentVideo({ media }: { media: MomentMedia }) {
  const [failed, setFailed] = useState(false)
  return failed ? (
    <div className='moment-media-unavailable'>
      <ImageOff />
      <span>本地视频无法播放</span>
    </div>
  ) : (
    <video
      controls
      playsInline
      preload='none'
      aria-label='朋友圈视频'
      src={localAsset(media.url ?? undefined)}
      poster={localAsset(media.thumbnail_url ?? undefined)}
      onError={() => setFailed(true)}
    />
  )
}

const MomentCard = memo(function MomentCard({ item }: { item: Moment }) {
  const [expanded, setExpanded] = useState(false)
  const date = momentDate(item.create_time)
  const long = item.content.length > 360 || item.content.split('\n').length > 8
  const name = item.display_name || item.username || '未知联系人'
  const hasInteractions =
    item.likes.length > 0 ||
    item.comments.length > 0 ||
    item.like_count > 0 ||
    item.comment_count > 0
  return (
    <article className='moment-post' aria-label={`${name}的朋友圈`}>
      <ContactAvatar name={name} url={item.avatar_url} />
      <div className='moment-post-main'>
        <h3 className='moment-author'>{name}</h3>
        {item.content && (
          <div
            className={`moment-text ${long && !expanded ? 'is-collapsed' : ''}`}
          >
            <WechatText text={item.content} />
          </div>
        )}
        {long && (
          <button
            className='moment-text-toggle'
            type='button'
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? '收起' : '全文'}
          </button>
        )}
        {item.media.length > 0 && <MomentGallery items={item.media} />}
        {!item.content && item.media.length === 0 && (
          <p className='moment-unsupported'>此动态的内容类型暂不支持展示</p>
        )}
        {item.location && (
          <p className='moment-location'>
            <MapPin size={13} />
            <span>{item.location}</span>
          </p>
        )}
        <div className='moment-post-footer'>
          <time
            dateTime={date?.toISOString()}
            title={date?.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}
          >
            {momentTime(item.create_time)}
          </time>
          <Popover>
            <PopoverTrigger asChild>
              <button
                type='button'
                className='moment-actions'
                aria-label='动态操作'
                title='动态操作'
              >
                <MoreHorizontal size={22} />
              </button>
            </PopoverTrigger>
            <PopoverContent
              className='moment-action-menu'
              side='top'
              align='end'
            >
              <div>
                <button type='button' disabled>
                  <Heart size={17} />
                  点赞（暂未接入）
                </button>
                <button type='button' disabled>
                  <MessageCircle size={17} />
                  评论（暂未接入）
                </button>
              </div>
              <p>当前仅浏览本地动态，不会向微信发送互动。</p>
            </PopoverContent>
          </Popover>
        </div>
        {hasInteractions && (
          <div className='moment-interactions'>
            {item.likes.length > 0 && (
              <p className='moment-likes'>
                <Heart size={15} aria-label='点赞' />
                <span>
                  {item.likes
                    .map((like) => like.display_name || like.username)
                    .join('、')}
                </span>
              </p>
            )}
            {item.comments.length > 0 && (
              <ul className='moment-comments'>
                {item.comments.map((comment, index) => (
                  <li key={`${comment.id}:${index}`}>
                    <strong>
                      {comment.display_name || comment.username}：
                    </strong>
                    <WechatText text={comment.content} />
                  </li>
                ))}
              </ul>
            )}
            {(item.like_count > item.likes.length ||
              item.comment_count > item.comments.length) && (
              <p className='moment-interaction-note'>
                本地记录：{item.like_count} 个赞 · {item.comment_count}{' '}
                条评论，明细可能不完整
              </p>
            )}
            {item.interactions_limited && (
              <p className='moment-interaction-note'>仅展示本地已缓存的互动</p>
            )}
          </div>
        )}
      </div>
    </article>
  )
})

export function MomentsWorkspace({ live }: { live: LiveStatus }) {
  const [search, setSearch] = useState('')
  const q = useDeferredValue(search.trim())
  const [scope, setScope] = useState<'all' | 'self'>('all')
  const [info, setInfo] = useState<'notifications' | 'publish' | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const { list, refresh, data, items } = useMomentsFeed(live, q, scope)
  const name =
    live.profile.display_name ||
    live.profile.nickname ||
    live.profile.username ||
    '等待微信登录'
  const busy = refresh.isPending || !!data?.syncing
  const lastSuccessfulRead = data?.ready
    ? momentDate(data.updated_at ?? 0)
    : undefined
  const changeScope = (value: 'all' | 'self') => {
    setScope(value)
    viewport.current?.scrollTo({ top: 0 })
  }
  return (
    <section
      className='moments-workspace'
      aria-label='朋友圈工作区'
      ref={viewport}
    >
      <div className='moments-cover'>
        <div className='moments-cover-toolbar'>
          <h2>朋友圈</h2>
          <div className='moments-toolbar-actions'>
            <button
              type='button'
              aria-label='刷新朋友圈'
              title='重新读取本地朋友圈'
              disabled={!live.account || !live.bridge_connected || busy}
              onClick={() => refresh.mutate()}
            >
              <RefreshCw
                className={busy || list.isFetching ? 'spin' : ''}
                size={22}
              />
            </button>
            <button
              type='button'
              aria-label='朋友圈消息'
              title='朋友圈消息'
              onClick={() => setInfo('notifications')}
            >
              <Bell size={22} />
            </button>
            <button
              type='button'
              aria-label='发表朋友圈'
              title='发表朋友圈（暂未接入）'
              onClick={() => setInfo('publish')}
            >
              <Camera size={23} />
            </button>
          </div>
        </div>
        <div className='moments-profile'>
          <strong>{name}</strong>
          <ContactAvatar name={name} url={live.profile.avatar_url} />
        </div>
      </div>
      <div className='moments-content'>
        <div className='moments-feed-toolbar'>
          <div className='moments-tabs' aria-label='朋友圈范围'>
            <button
              type='button'
              aria-pressed={scope === 'all'}
              onClick={() => changeScope('all')}
            >
              全部动态
            </button>
            <button
              type='button'
              aria-pressed={scope === 'self'}
              onClick={() => changeScope('self')}
            >
              我的朋友圈
            </button>
          </div>
          <div className='moments-search'>
            <Search size={15} />
            <Input
              aria-label='搜索朋友圈'
              placeholder='搜索动态'
              maxLength={200}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            {search && (
              <button
                type='button'
                aria-label='清空朋友圈搜索'
                onClick={() => setSearch('')}
              >
                <X size={15} />
              </button>
            )}
          </div>
        </div>
        <div className='moments-source' role='status'>
          <div className='flex flex-col gap-1'>
            <span>
              {data?.ready
                ? `本地已缓存 ${data.total} 条动态`
                : data?.syncing
                  ? '正在首次读取当前账号的本地朋友圈…'
                  : '当前账号的本地朋友圈'}
            </span>
            {lastSuccessfulRead && (
              <span>
                上次成功读取本地源库：
                <time dateTime={lastSuccessfulRead.toISOString()}>
                  {lastSuccessfulRead.toLocaleString('zh-CN', {
                    timeZone: 'Asia/Shanghai',
                    hour12: false,
                  })}
                </time>
              </span>
            )}
            {data?.ready && busy && !data.error && (
              <span>后台更新中，不影响浏览缓存</span>
            )}
          </div>
          <span>仅本地缓存，不代表完整朋友圈</span>
        </div>
        {!live.bridge_connected && (
          <p className='moments-status' role='alert'>
            微信未连接，暂不能核验当前账号
            {items.length ? '，以下保留上次已核验的缓存' : ''}。
          </p>
        )}
        {list.error && (
          <Notice
            error={list.error}
            retry={() => {
              if (
                list.error instanceof MomentsApiError &&
                list.error.status === 409
              )
                void list.refetch()
              else if (list.isFetchNextPageError) void list.fetchNextPage()
              else void list.refetch()
            }}
          />
        )}
        {refresh.error && (
          <Notice error={refresh.error} retry={() => refresh.mutate()} />
        )}
        {data?.error && data.ready && (
          <div className='moments-status' role={busy ? 'status' : 'alert'}>
            <div className='flex flex-wrap items-center gap-x-3 gap-y-1'>
              <span>
                {busy
                  ? '后台重试中，显示上次缓存'
                  : '后台同步失败，显示上次缓存'}
              </span>
              {!busy && (
                <Button
                  variant='link'
                  size='sm'
                  className='h-auto px-0 py-1'
                  disabled={!live.account || !live.bridge_connected}
                  onClick={() => refresh.mutate()}
                >
                  重试同步
                </Button>
              )}
            </div>
            <details>
              <summary className='cursor-pointer'>上次同步失败详情</summary>
              <p className='break-words'>{data.error}</p>
            </details>
          </div>
        )}
        {data?.error && !data.ready && (
          <Notice
            error={
              new Error(
                data.syncing
                  ? `正在重试首次读取；上次失败：${data.error}`
                  : data.error
              )
            }
            retry={() => refresh.mutate()}
          />
        )}
        {!!data?.limited_count && (
          <p className='moments-status' role='status'>
            另有 {data.limited_count} 条本地记录因解析或读取限制未展示。
          </p>
        )}
        {!!live.account && live.bridge_connected && list.isPending && (
          <div className='moments-empty' role='status'>
            <LoaderCircle className='spin' />
            <p>正在读取朋友圈…</p>
          </div>
        )}
        {!live.account && (
          <div className='moments-empty'>
            <Aperture size={36} />
            <h3>登录微信后查看朋友圈</h3>
            <p>只读取当前活动账号，不使用其他账号的快照。</p>
          </div>
        )}
        {data && !data.ready && !data.error && (
          <div className='moments-empty' role='status'>
            <LoaderCircle className='spin' />
            <p>正在准备当前账号的朋友圈缓存…</p>
          </div>
        )}
        {data?.ready &&
          !data.syncing &&
          !data.error &&
          !list.error &&
          items.length === 0 && (
            <div className='moments-empty'>
              <Aperture size={36} />
              <h3>
                {q
                  ? '没有匹配的动态'
                  : scope === 'self'
                    ? '暂无自己的本地动态'
                    : '暂无本地朋友圈动态'}
              </h3>
              <p>
                {q
                  ? '试试其他关键词，或清空搜索。'
                  : '可先在微信中浏览朋友圈，再回来刷新本地缓存。'}
              </p>
            </div>
          )}
        <div className='moments-feed'>
          {items.map((item) => (
            <MomentCard key={item.id} item={item} />
          ))}
        </div>
        {list.hasNextPage && (
          <div className='moments-pagination'>
            <Button
              variant='ghost'
              disabled={list.isFetchingNextPage || !live.bridge_connected}
              onClick={() => void list.fetchNextPage()}
            >
              {list.isFetchingNextPage && (
                <LoaderCircle className='spin' size={16} />
              )}
              加载更多动态
            </Button>
          </div>
        )}
        {data?.ready && items.length > 0 && !list.hasNextPage && (
          <p className='moments-end'>已显示当前筛选下的全部本地缓存</p>
        )}
      </div>
      <Dialog
        open={info !== null}
        onOpenChange={(open) => {
          if (!open) setInfo(null)
        }}
      >
        <DialogContent className='sm:max-w-md'>
          <DialogHeader>
            <DialogTitle>
              {info === 'publish' ? '发表朋友圈暂未接入' : '朋友圈消息暂未接入'}
            </DialogTitle>
            <DialogDescription>
              {info === 'publish'
                ? '当前仅支持浏览本地朋友圈，发布、点赞和评论尚未接入。请在微信客户端中完成这些操作。'
                : '当前未接入朋友圈通知及已读状态。动态下方仅展示已缓存的点赞和评论，不代表完整消息列表。'}
            </DialogDescription>
          </DialogHeader>
          <Button variant='outline' onClick={() => setInfo(null)}>
            知道了
          </Button>
        </DialogContent>
      </Dialog>
    </section>
  )
}
