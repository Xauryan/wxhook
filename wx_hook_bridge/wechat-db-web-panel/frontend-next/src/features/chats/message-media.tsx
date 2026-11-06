import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Download,
  ExternalLink,
  FileText,
  ImageIcon,
  Pause,
  Play,
  RefreshCw,
  Volume2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  chatApi,
  localAsset,
  type FilePreviewResult,
  type MediaItem,
  type Message,
} from './api'

function fileSize(size?: number) {
  if (size == null) return '大小未知'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
function downloadUrl(url: string) {
  const value = new URL(url)
  value.searchParams.set('download', 'true')
  return value.href
}
function available(item: MediaItem) {
  return ['local', 'available'].includes(item.state) && !!localAsset(item.url)
}
function quality(item?: MediaItem) {
  return item?.variant === 'original'
    ? '原图'
    : item?.variant === 'preview'
      ? '预览图'
      : '缩略图'
}

export function ImagePreview({
  items,
  label,
}: {
  items: MediaItem[]
  label: string
}) {
  const [index, setIndex] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [zoom, setZoom] = useState(false)
  const candidates = [...items].filter(available).sort((a, b) => {
    const rank: Record<string, number> = {
      original: 0,
      preview: 1,
      thumbnail: 2,
    }
    return (rank[a.variant] ?? 3) - (rank[b.variant] ?? 3)
  })
  const item = candidates[index]
  const url = localAsset(item?.url)
  return (
    <>
      {url ? (
        <div className={`image-preview-stage ${zoom ? 'is-zoomed' : ''}`}>
          <button
            type='button'
            aria-label={zoom ? '适应窗口' : '放大图片'}
            onClick={() => setZoom(!zoom)}
          >
            <img
              key={url}
              src={url}
              alt={`${label}${quality(item)}`}
              onLoad={() => setLoaded(true)}
              onError={() => {
                setLoaded(false)
                setIndex(index + 1)
              }}
            />
          </button>
        </div>
      ) : (
        <p role='status'>本地图片无法加载，请关闭后重新检查资源。</p>
      )}
      {url && (
        <div className='media-preview-actions'>
          <span role='status'>
            {loaded
              ? `${index ? '较清晰版本加载失败，已降级 · ' : ''}当前显示${quality(item)}`
              : `正在加载${quality(item)}…`}
            {item?.variant === 'thumbnail' && ' · 无可用原图'}
          </span>
          <a href={url} target='_blank' rel='noopener noreferrer'>
            新窗口查看
            <ExternalLink size={14} />
          </a>
          <a href={downloadUrl(url)} download>
            <Download size={14} />
            下载
          </a>
        </div>
      )}
    </>
  )
}

function VoiceBubble({
  url,
  duration,
  onError,
}: {
  url: string
  duration?: number
  onError: () => void
}) {
  const audio = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState('')
  const total = Math.ceil(duration || 0)
  async function toggle() {
    if (!audio.current) return
    setError('')
    if (playing) audio.current.pause()
    else {
      try {
        await audio.current.play()
      } catch {
        setError('播放未开始，请点击重试。')
      }
    }
  }
  return (
    <>
      <button
        type='button'
        className={`voice-bubble ${playing ? 'is-playing' : ''}`}
        style={{ width: `${Math.min(260, 112 + total * 2)}px` }}
        aria-label={playing ? '暂停语音' : '播放语音'}
        aria-pressed={playing}
        onClick={() => void toggle()}
      >
        {playing ? <Pause size={20} /> : <Volume2 size={22} />}
        <span>
          {total ? `${total}″` : '语音'}
          {playing && <small>{Math.floor(elapsed)} 秒</small>}
        </span>
        <span className='voice-waves' aria-hidden='true'>
          <i />
          <i />
          <i />
        </span>
      </button>
      <audio
        ref={audio}
        src={url}
        preload='none'
        onPlay={() => {
          document.querySelectorAll('audio').forEach((other) => {
            if (other !== audio.current) other.pause()
          })
          setPlaying(true)
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false)
          setElapsed(0)
        }}
        onTimeUpdate={() => setElapsed(audio.current?.currentTime || 0)}
        onError={onError}
      />
      {error && <small role='status'>{error}</small>}
    </>
  )
}

export function FilePreview({
  item,
  url,
  filename,
}: {
  item: MediaItem
  url: string
  filename: string
}) {
  const [failed, setFailed] = useState(false)
  const isImage = item.mime?.startsWith('image/')
  const isDocument =
    item.mime === 'application/pdf' || item.mime?.startsWith('text/plain')
  const isOffice = /\.(xlsx|docx|pptx)$/i.test(filename)
  const office = useQuery({
    queryKey: ['wechat-file-preview', url],
    enabled: isOffice,
    queryFn: async ({ signal }): Promise<FilePreviewResult> => {
      const endpoint = new URL(url)
      endpoint.pathname += '/preview'
      const response = await fetch(endpoint, { signal })
      if (!response.ok)
        throw new Error('此文件无法生成内容预览，请下载原文件查看。')
      return response.json() as Promise<FilePreviewResult>
    },
    retry: false,
    staleTime: Infinity,
  })
  return (
    <>
      {isOffice ? (
        <div className='office-preview'>
          {office.isPending && <p role='status'>正在读取文件内容…</p>}
          {office.error && <p role='status'>{office.error.message}</p>}
          {office.data && (
            <>
              <p className='office-preview-note'>{office.data.detail}</p>
              {office.data.sections.map((section, index) => (
                <section key={index}>
                  <h3>{section.title}</h3>
                  {section.rows ? (
                    <div className='office-table-scroll'>
                      <table>
                        <tbody>
                          {section.rows.map((row, rowIndex) => (
                            <tr key={rowIndex}>
                              <th scope='row'>{rowIndex + 1}</th>
                              {row.map((cell, column) => (
                                <td key={column}>{cell}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    section.paragraphs?.map((paragraph, line) => (
                      <p key={line}>{paragraph}</p>
                    ))
                  )}
                </section>
              ))}
              {office.data.truncated && (
                <p role='status'>
                  内容较多，当前最多预览 10 个工作表或章节、每节 200 行及 30
                  列，请下载查看完整文件。
                </p>
              )}
            </>
          )}
        </div>
      ) : isImage && !failed ? (
        <img
          className='file-image-preview'
          src={url}
          alt={filename}
          onError={() => setFailed(true)}
        />
      ) : isDocument && !failed ? (
        <iframe
          className='file-document-preview'
          title={`文件预览：${filename}`}
          src={url}
          sandbox=''
          onError={() => setFailed(true)}
        />
      ) : (
        <div className='file-preview-empty'>
          <FileText size={48} />
          <strong>{filename}</strong>
          <p>
            {failed
              ? '文件预览加载失败。'
              : '此格式不支持浏览器内预览，请下载后使用本机应用打开。'}
          </p>
        </div>
      )}
      <div className='media-preview-actions'>
        <span>{fileSize(item.size)}</span>
        {isDocument && (
          <a href={url} target='_blank' rel='noopener noreferrer'>
            新窗口打开
            <ExternalLink size={14} />
          </a>
        )}
        <a href={downloadUrl(url)} download={filename}>
          <Download size={14} />
          下载原文件
        </a>
      </div>
    </>
  )
}

export function MediaContent({
  account,
  message,
  recordPath,
}: {
  account: string
  message: Message
  recordPath?: string
}) {
  const root = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [failed, setFailed] = useState(false)
  const [imageIndex, setImageIndex] = useState(0)
  const [preview, setPreview] = useState(false)
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: '150px' }
    )
    if (root.current) observer.observe(root.current)
    return () => observer.disconnect()
  }, [])
  const media = useQuery({
    queryKey: [
      'wechat-media',
      account,
      message.id,
      message.revision,
      recordPath,
    ],
    queryFn: ({ signal, client, queryKey }) => {
      const previous = client.getQueryState(queryKey)
      return chatApi.media(
        account,
        message.id,
        signal,
        !!(previous?.dataUpdatedAt || previous?.errorUpdatedAt),
        recordPath
      )
    },
    enabled: visible,
    staleTime: (query) =>
      query.state.data?.items.some(
        (item) => item.kind === message.category && available(item)
      )
        ? Infinity
        : 15_000,
    gcTime: 30 * 60_000,
    refetchOnWindowFocus: (query) =>
      query.state.data?.items.some(
        (item) => item.kind === message.category && available(item)
      )
        ? false
        : 'always',
    refetchInterval: (query) =>
      query.state.status === 'success' &&
      query.state.dataUpdateCount < 4 &&
      query.state.data?.items.some((item) =>
        ['missing', 'not_downloaded'].includes(item.state)
      )
        ? 15_000
        : false,
    retry: false,
  })
  const kind = message.category || 'image'
  const items = media.data?.items || []
  const preferred = media.data?.preferred ?? 0
  const images = items.filter(
    (value) => ['image', 'sticker'].includes(value.kind) && available(value)
  )
  const bubbleImages = [...images].sort(
    (a, b) => Number(b.index === preferred) - Number(a.index === preferred)
  )
  const item = ['image', 'sticker'].includes(kind)
    ? bubbleImages[imageIndex]
    : items[preferred]
  const url = localAsset(item?.url)
  const poster = localAsset(
    items.find((value) => value.kind === 'image' && available(value))?.url
  )
  const playable =
    !!url && !!item && available(item) && !failed && item.kind === kind
  const filename = message.file_name || item?.name || '未命名文件'
  const label =
    (
      { voice: '语音', video: '视频', sticker: '表情', file: '文件' } as Record<
        string,
        string
      >
    )[kind] || '图片'
  const errorItem = items.find(
    (value) => value.kind === kind && !available(value)
  )
  return (
    <div ref={root} className={`chat-media media-${kind}`}>
      {kind === 'file' && (
        <button
          type='button'
          className='file-card'
          disabled={!playable}
          onClick={() => setPreview(true)}
          aria-label={`打开文件：${filename}`}
        >
          <span className='file-card-icon'>
            <FileText size={30} />
          </span>
          <span className='file-card-copy'>
            <strong>{filename}</strong>
            <small>{fileSize(message.file_size ?? item?.size)}</small>
            <small>{playable ? '点击打开' : '文件'}</small>
          </span>
        </button>
      )}
      {playable && ['image', 'sticker'].includes(kind) && (
        <button
          type='button'
          className='message-image'
          aria-label={`预览${label}`}
          onClick={() => setPreview(true)}
        >
          <img
            key={url}
            src={url}
            alt={label}
            loading='lazy'
            onError={() => setImageIndex(imageIndex + 1)}
          />
        </button>
      )}
      {playable && kind === 'voice' && (
        <VoiceBubble
          url={url!}
          duration={message.duration}
          onError={() => setFailed(true)}
        />
      )}
      {playable && kind === 'video' && (
        <button
          type='button'
          className='video-bubble'
          aria-label='播放视频'
          onClick={() => setPreview(true)}
        >
          {poster ? (
            <img
              src={poster}
              alt='视频封面'
              onError={(event) => {
                event.currentTarget.style.display = 'none'
              }}
            />
          ) : (
            <video
              preload='metadata'
              muted
              playsInline
              src={url}
              aria-hidden='true'
            />
          )}
          <span className='video-play'>
            <Play size={24} fill='currentColor' />
          </span>
          <small>{message.duration ? `${message.duration} 秒` : '视频'}</small>
        </button>
      )}
      {!playable && (
        <p className='media-state' role='status'>
          <ImageIcon size={16} />
          {!visible
            ? `${label} · 按需加载`
            : media.isPending || media.isFetching
              ? `正在读取本地${label}…`
              : failed || imageIndex > 0
                ? '浏览器无法解码此资源，请重新检查。'
                : media.error?.message ||
                  (errorItem?.state === 'not_downloaded'
                    ? `暂未找到本地${label}`
                    : errorItem?.detail) ||
                  '本地没有可用资源'}
        </p>
      )}
      {playable && kind === 'file' && (
        <a
          className='media-download'
          href={downloadUrl(url!)}
          download={filename}
        >
          <Download size={14} />
          下载原文件
        </a>
      )}
      {visible &&
        !playable &&
        ['missing', 'not_downloaded'].includes(errorItem?.state || '') && (
          <small className='media-help'>
            请先在电脑微信打开此{label}，再点击重新检查。
          </small>
        )}
      {visible && !playable && !media.isPending && (
        <Button
          variant='ghost'
          size='sm'
          disabled={media.isFetching}
          onClick={() => {
            setFailed(false)
            setImageIndex(0)
            void media.refetch()
          }}
        >
          <RefreshCw
            size={12}
            className={media.isFetching ? 'animate-spin' : ''}
          />
          {media.isFetching ? '正在重新检查…' : '重新检查本地资源'}
        </Button>
      )}
      <Dialog open={preview} onOpenChange={setPreview}>
        <DialogContent className='media-preview-dialog'>
          <DialogHeader>
            <DialogTitle>
              {kind === 'file' ? filename : `${label}预览`}
            </DialogTitle>
            <DialogDescription>
              当前账号的本地资源
              {kind === 'image' ? ' · 优先原图，点击图片放大' : ''}
            </DialogDescription>
          </DialogHeader>
          {preview && ['image', 'sticker'].includes(kind) && (
            <ImagePreview items={images} label={label} />
          )}
          {preview && kind === 'video' && url && !failed && (
            <video
              className='video-preview'
              controls
              autoPlay
              playsInline
              src={url}
              poster={poster}
              onError={() => setFailed(true)}
            />
          )}
          {preview && kind === 'video' && failed && (
            <p role='status'>视频无法播放，请关闭后重新检查本地资源。</p>
          )}
          {preview && kind === 'file' && item && url && (
            <FilePreview item={item} url={url} filename={filename} />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
