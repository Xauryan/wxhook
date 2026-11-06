import { useDeferredValue, useEffect, useRef, useState } from 'react'
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  ArrowLeft,
  Box,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  ImageIcon,
  LayoutGrid,
  LoaderCircle,
  Menu,
  Play,
  PlusCircle,
  RefreshCw,
  Search,
  Tag,
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
import { localAsset, type LiveStatus } from './api'
import { Notice } from './chat-ui'
import { favoritesApi, type Favorite } from './favorites-api'
import './favorites.css'
import { externalLink } from './message-content'
import { FilePreview, ImagePreview } from './message-media'
import './message-media.css'
import { WechatText } from './wechat-text'

function sizeLabel(value?: number | null) {
  if (value == null) return '未获取'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
function favoriteTime(timestamp: number, full = false) {
  if (!timestamp || !Number.isFinite(timestamp)) return '时间未知'
  const date = new Date(timestamp * 1000)
  if (full) return date.toLocaleString('zh-CN', { hour12: false })
  const age = Date.now() - date.getTime()
  return age >= 0 && age < 7 * 86400000
    ? date.toLocaleDateString('zh-CN', { weekday: 'long' })
    : date.toLocaleDateString('zh-CN')
}

function FavoriteThumbnail({ item }: { item: Favorite }) {
  const [index, setIndex] = useState(0)
  const urls = [item.thumbnail_url, item.thumbnail_fallback_url].filter(
    (url): url is string => !!url
  )
  const url = localAsset(urls[index])
  return url ? (
    <span className='favorite-thumbnail'>
      <img
        src={url}
        alt={`${item.type_label}收藏预览`}
        loading='lazy'
        onError={() => setIndex(index + 1)}
      />
      {item.category === 'video' && (
        <Play className='favorite-video-indicator' size={28} />
      )}
    </span>
  ) : (
    <span className='favorite-placeholder'>
      {['image', 'video'].includes(item.category) ? (
        <ImageIcon size={36} />
      ) : (
        <FileText size={36} />
      )}
      <small>{index ? '预览暂不可用' : item.type_label}</small>
    </span>
  )
}

function FavoriteMedia({ account, item }: { account: string; item: Favorite }) {
  const [group, setGroup] = useState(0)
  const [failed, setFailed] = useState(false)
  const attachment = item.attachments.find((value) => value.group === group)
  const media = useQuery({
    queryKey: ['wechat-favorite-media', account, item.id, item.revision, group],
    queryFn: ({ signal }) =>
      favoritesApi.media(account, item.id, group, signal),
    enabled: !!attachment,
    retry: false,
    staleTime: Infinity,
  })
  const value = media.data?.items[media.data.preferred]
  const url = localAsset(value?.url)
  const images =
    media.data?.items.filter((value) => value.kind === 'image') || []
  const playable = !!url && value?.state === 'available' && !failed
  return (
    <div className='favorite-media-viewer'>
      {item.attachments.length > 1 && (
        <label className='favorite-attachment-switch'>
          附件
          <select
            aria-label='切换收藏附件'
            value={group}
            onChange={(event) => {
              setGroup(Number(event.target.value))
              setFailed(false)
            }}
          >
            {item.attachments.map((value, index) => (
              <option key={value.group} value={value.group}>
                {index + 1}. {value.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {media.isPending && (
        <div className='favorite-loading'>
          <LoaderCircle className='spin' />
          <p>正在读取本地收藏附件…</p>
        </div>
      )}
      {media.error && (
        <Notice error={media.error} retry={() => void media.refetch()} />
      )}
      {!media.isPending &&
        !media.error &&
        attachment?.kind === 'image' &&
        images.some((image) => image.url) && (
          <ImagePreview
            key={`${group}:${media.dataUpdatedAt}`}
            items={images}
            label='收藏图片'
          />
        )}
      {playable && attachment?.kind === 'video' && (
        <video
          className='video-preview'
          controls
          autoPlay
          playsInline
          src={url}
          poster={localAsset(images.find((image) => image.url)?.url)}
          onError={() => setFailed(true)}
        />
      )}
      {playable && attachment?.kind === 'voice' && (
        <audio controls autoPlay src={url} onError={() => setFailed(true)} />
      )}
      {playable && attachment?.kind === 'file' && (
        <FilePreview item={value!} url={url!} filename={attachment.name} />
      )}
      {!media.isPending &&
        !media.error &&
        (!url || (attachment?.kind !== 'image' && !playable)) && (
          <div className='favorite-resource-error' role='status'>
            <p>
              {failed
                ? '浏览器无法播放此收藏附件'
                : value?.detail || '收藏附件尚未在本地找到'}
            </p>
            <Button
              variant='outline'
              size='sm'
              onClick={() => {
                setFailed(false)
                void media.refetch()
              }}
            >
              <RefreshCw size={14} />
              重新检查附件
            </Button>
          </div>
        )}
    </div>
  )
}

function FavoriteViewer({
  account,
  favorite,
  close,
}: {
  account: string
  favorite: Favorite
  close: () => void
}) {
  const detail = useQuery({
    queryKey: [
      'wechat-favorites',
      account,
      'detail',
      favorite.id,
      favorite.revision,
    ],
    queryFn: ({ signal }) => favoritesApi.detail(account, favorite.id, signal),
    retry: false,
    staleTime: 30_000,
  })
  const item = detail.data
  const url = externalLink(item?.url)
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close()
      }}
    >
      <DialogContent className='media-preview-dialog favorite-preview-dialog'>
        <DialogHeader>
          <DialogTitle>
            {item?.title || `${favorite.type_label}收藏`}
          </DialogTitle>
          <DialogDescription>
            {item?.source_name || favorite.source_name} · 更新于{' '}
            {favoriteTime(item?.updated_at || favorite.updated_at, true)}
          </DialogDescription>
        </DialogHeader>
        {detail.isPending && (
          <div className='favorite-loading'>
            <LoaderCircle className='spin' />
            <p>正在读取收藏…</p>
          </div>
        )}
        {detail.error && (
          <Notice error={detail.error} retry={() => void detail.refetch()} />
        )}
        {item && (
          <>
            {item.parse_error ? (
              <p role='status'>此收藏结构暂无法完整解析，原始数据未修改。</p>
            ) : (
              <>
                {item.content && (
                  <div className='favorite-text-content'>
                    <WechatText text={item.content} />
                  </div>
                )}
                {!item.content && item.description && (
                  <p className='favorite-text-content'>{item.description}</p>
                )}
                {url && (
                  <a
                    className='favorite-external-link'
                    href={url}
                    target='_blank'
                    rel='noopener noreferrer'
                  >
                    <ExternalLink size={15} />
                    打开收藏链接
                  </a>
                )}
                {item.attachments.length > 0 && (
                  <FavoriteMedia
                    key={`${item.id}:${item.revision}`}
                    account={account}
                    item={item}
                  />
                )}
                {!item.content &&
                  !item.description &&
                  !url &&
                  !item.attachments.length && (
                    <p className='favorite-empty-content'>
                      本地记录不含可预览正文。
                    </p>
                  )}
                {item.content_limited && (
                  <p role='status'>
                    内容过长，当前为有界预览；完整内容请在微信查看。
                  </p>
                )}
              </>
            )}
            {item.tags.length > 0 && (
              <div className='favorite-tag-pills'>
                {item.tags.map((tag) => (
                  <span key={tag.id}>
                    <Tag size={12} />
                    {tag.name}
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

export function FavoritesWorkspace({ live }: { live: LiveStatus }) {
  const account = live.account
  const client = useQueryClient()
  const [search, setSearch] = useState('')
  const q = useDeferredValue(search.trim())
  const [category, setCategory] = useState<'all' | 'media'>('all')
  const [tag, setTag] = useState('')
  const [tagsExpanded, setTagsExpanded] = useState(true)
  const [mobileMenu, setMobileMenu] = useState(false)
  const [selected, setSelected] = useState<Favorite | null>(null)
  const [thumbnailVersion, setThumbnailVersion] = useState(0)
  const scroll = useRef<HTMLDivElement>(null)
  const list = useInfiniteQuery({
    queryKey: ['wechat-favorites', account, 'list', q, category, tag],
    queryFn: ({ signal, pageParam }) =>
      favoritesApi.list(account, q, category, tag, pageParam, signal),
    initialPageParam: 0,
    enabled: !!account && live.bridge_connected,
    retry: false,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      !live.bridge_connected
        ? false
        : query.state.data?.pages[0]?.ready === false ||
            query.state.data?.pages[0]?.syncing
          ? 1500
          : 20_000,
    getNextPageParam: (last, pages) => {
      const count = pages.reduce((sum, page) => sum + page.items.length, 0)
      return last.items.length && count < last.total ? count : undefined
    },
  })
  const refresh = useMutation({
    mutationFn: () => favoritesApi.refresh(account),
    retry: false,
    onSuccess: () => {
      setThumbnailVersion((value) => value + 1)
      void client.invalidateQueries({ queryKey: ['wechat-favorites', account] })
      void client.invalidateQueries({
        queryKey: ['wechat-favorite-media', account],
      })
    },
  })
  const first = list.data?.pages[0]
  const items = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.items) || []).map((item) => [
        item.id,
        item,
      ])
    ).values(),
  ]
  const tagName = first?.tags.find((value) => value.id === tag)?.name
  const heading = tag
    ? tagName || '标签收藏'
    : category === 'media'
      ? '图片与视频'
      : '全部收藏'
  const choose = (next: 'all' | 'media', nextTag = '') => {
    setCategory(next)
    setTag(nextTag)
    setMobileMenu(false)
    setSelected(null)
  }
  useEffect(() => {
    scroll.current?.scrollTo({ top: 0 })
  }, [q, category, tag])
  return (
    <div
      className={`favorites-window ${mobileMenu ? 'favorites-menu-open' : ''}`}
    >
      <aside className='favorites-sidebar' aria-label='收藏分类'>
        <button
          className='favorites-mobile-back'
          aria-label='查看收藏结果'
          onClick={() => setMobileMenu(false)}
        >
          <ArrowLeft size={18} />
          查看收藏
        </button>
        <div className='favorites-search'>
          <div className='session-search'>
            <Search size={15} />
            <Input
              aria-label='搜索收藏'
              placeholder='搜索收藏'
              value={search}
              maxLength={200}
              onChange={(event) => {
                setSearch(event.target.value)
                setSelected(null)
              }}
            />
            {search && (
              <button aria-label='清空收藏搜索' onClick={() => setSearch('')}>
                <X size={14} />
              </button>
            )}
          </div>
        </div>
        <button
          className='favorite-new-note'
          disabled
          aria-describedby='favorite-write-boundary'
          title='当前桥接尚未接入微信笔记写入'
        >
          <PlusCircle size={18} />
          新建笔记
        </button>
        <p id='favorite-write-boundary' className='favorite-write-note'>
          新建笔记暂未接入，请在微信中完成
        </p>
        <nav className='favorite-categories'>
          <button
            aria-pressed={category === 'all' && !tag}
            onClick={() => choose('all')}
          >
            <LayoutGrid size={22} />
            <span>全部收藏</span>
            {first?.ready && <small>{first.counts.all}</small>}
          </button>
          <button
            aria-pressed={category === 'media' && !tag}
            onClick={() => choose('media')}
          >
            <ImageIcon size={22} />
            <span>图片与视频</span>
            {first?.ready && <small>{first.counts.media}</small>}
          </button>
        </nav>
        <div className='favorite-tags'>
          <button
            className='favorite-tags-toggle'
            aria-expanded={tagsExpanded}
            onClick={() => setTagsExpanded(!tagsExpanded)}
          >
            <Tag size={22} />
            <span>标签</span>
            {tagsExpanded ? (
              <ChevronDown size={17} />
            ) : (
              <ChevronRight size={17} />
            )}
          </button>
          {tagsExpanded && (
            <div className='favorite-tag-list'>
              {!first?.ready ? (
                <small>等待收藏数据</small>
              ) : !first.tags_ready ? (
                <small>标签来源尚未就绪</small>
              ) : !first.tags.length ? (
                <small>暂无标签</small>
              ) : (
                first.tags.map((value) => (
                  <button
                    key={value.id}
                    aria-pressed={tag === value.id}
                    aria-label={`筛选标签：${value.name}`}
                    onClick={() => choose('all', value.id)}
                  >
                    <span>{value.name}</span>
                    <small>
                      {first.binding_limited ? '部分' : value.count}
                    </small>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
        <footer className='favorite-capacity'>
          <span>
            收藏附件{' '}
            {first?.ready
              ? sizeLabel(first.recorded_attachment_bytes)
              : '未获取'}
          </span>
          <span>云端剩余容量未获取</span>
        </footer>
      </aside>
      <section className='favorites-main' aria-label='收藏列表'>
        <header className='favorites-heading'>
          <button
            className='favorites-menu-toggle'
            aria-label={mobileMenu ? '返回收藏列表' : '打开收藏分类'}
            onClick={() => setMobileMenu(!mobileMenu)}
          >
            {mobileMenu ? <ArrowLeft size={20} /> : <Menu size={20} />}
          </button>
          <h2>{heading}</h2>
          <span>{first?.ready ? `${first.total} 条` : '待读取'}</span>
          <button
            className='favorite-refresh'
            aria-label='刷新收藏'
            disabled={
              !account ||
              !live.bridge_connected ||
              refresh.isPending ||
              first?.syncing
            }
            onClick={() => refresh.mutate()}
          >
            <RefreshCw
              size={17}
              className={refresh.isPending || first?.syncing ? 'spin' : ''}
            />
          </button>
        </header>
        <div
          className='favorites-scroll'
          ref={scroll}
          aria-busy={list.isFetching || refresh.isPending}
        >
          {!live.bridge_connected && (
            <p className='favorite-status' role='status'>
              微信未连接，当前收藏无法重新核验。
            </p>
          )}
          {(list.error || refresh.error) && (
            <Notice
              error={(list.error || refresh.error)!}
              retry={() => void list.refetch()}
            />
          )}
          {first?.error && (
            <p className='favorite-status error' role='alert'>
              {first.error}
            </p>
          )}
          {first && (first.limited_count > 0 || first.binding_limited > 0) && (
            <p className='favorite-status' role='status'>
              部分收藏状态或标签关联暂未识别，当前仅展示已解析内容。
            </p>
          )}
          {live.bridge_connected &&
            !list.error &&
            !first?.error &&
            !first?.ready && (
              <div className='favorite-loading'>
                <LoaderCircle className='spin' />
                <p>正在核验当前账号的收藏…</p>
              </div>
            )}
          {first?.ready && !items.length && !list.error && (
            <div className='favorites-empty'>
              <Box size={64} strokeWidth={1} />
              <h3>
                {q || tag || category === 'media'
                  ? '没有匹配的收藏'
                  : '暂无收藏'}
              </h3>
              <p>
                {q || tag
                  ? '调整搜索条件或切换分类试试'
                  : '这里显示当前微信账号的真实收藏'}
              </p>
            </div>
          )}
          {items.map((item) => (
            <button
              key={item.id}
              className={`favorite-card favorite-kind-${item.category}`}
              aria-label={`查看收藏 ${item.id}：${item.title || item.type_label}`}
              onClick={() => setSelected(item)}
            >
              <div className='favorite-card-content'>
                <FavoriteThumbnail
                  key={`${item.revision}:${thumbnailVersion}`}
                  item={item}
                />
                <div className='favorite-card-copy'>
                  {item.title && (
                    <strong>
                      <WechatText text={item.title} />
                    </strong>
                  )}
                  {item.content && item.content !== item.title && (
                    <p>
                      <WechatText text={item.content} />
                    </p>
                  )}
                  {!item.title &&
                    !['image', 'video'].includes(item.category) && (
                      <strong>
                        {item.attachments[0]?.name || item.type_label}
                      </strong>
                    )}
                  {item.parse_error && <small>结构暂无法完整解析</small>}
                  {item.attachments.length > 1 && (
                    <small>{item.attachments.length} 项附件</small>
                  )}
                </div>
              </div>
              <div className='favorite-card-meta'>
                <span>{item.source_name}</span>
                <div className='favorite-card-tags'>
                  {item.tags.map((value) => (
                    <small key={value.id}>{value.name}</small>
                  ))}
                </div>
                <time title={favoriteTime(item.updated_at, true)}>
                  {favoriteTime(item.updated_at)}
                </time>
              </div>
            </button>
          ))}
          {list.hasNextPage && (
            <Button
              className='favorite-load-more'
              variant='ghost'
              disabled={list.isFetching}
              onClick={() => void list.fetchNextPage()}
            >
              {list.isFetchingNextPage ? '读取中…' : '加载更多收藏'}
            </Button>
          )}
          {first?.ready && items.length > 0 && !list.hasNextPage && (
            <div className='favorites-end' aria-label='已显示全部结果'>
              <span />
              <i />
              <span />
            </div>
          )}
        </div>
      </section>
      {selected && (
        <FavoriteViewer
          key={`${account}:${selected.id}`}
          account={account}
          favorite={items.find((item) => item.id === selected.id) || selected}
          close={() => setSelected(null)}
        />
      )}
    </div>
  )
}
