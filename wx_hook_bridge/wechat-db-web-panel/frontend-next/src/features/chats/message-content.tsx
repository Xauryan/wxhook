import { useState } from 'react'
import { ExternalLink, ImageIcon, MapPin, UserRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { Article, Message, MessageRecord } from './api'
import { MediaContent } from './message-media'
import { WechatText } from './wechat-text'

export function externalLink(value?: string) {
  try {
    const url = new URL(value || '')
    return ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : undefined
  } catch {
    return undefined
  }
}

function ArticleCover({ url, title }: { url?: string; title: string }) {
  const [failed, setFailed] = useState(false)
  const src = externalLink(url)
  return src && !failed ? (
    <img
      className='article-cover'
      src={src}
      alt={title}
      loading='lazy'
      referrerPolicy='no-referrer'
      onError={() => setFailed(true)}
    />
  ) : (
    <span className='article-cover article-cover-empty' aria-label='暂无封面'>
      <ImageIcon size={24} />
    </span>
  )
}

function ArticleLink({ article, lead }: { article: Article; lead: boolean }) {
  const url = externalLink(article.url)
  const content = (
    <>
      <ArticleCover
        key={article.cover_url}
        url={article.cover_url}
        title={article.title}
      />
      <span className='article-copy'>
        <strong>
          <WechatText text={article.title} />
        </strong>
        {!lead &&
          article.description &&
          article.description !== article.title && (
            <small>{article.description}</small>
          )}
      </span>
    </>
  )
  const className = `article-entry ${lead ? 'article-lead' : 'article-following'}`
  return url ? (
    <a
      className={className}
      href={url}
      target='_blank'
      rel='noopener noreferrer'
      aria-label={`打开文章：${article.title}`}
    >
      {content}
    </a>
  ) : (
    <div className={className}>
      {content}
      <small className='article-unavailable'>链接不可用</small>
    </div>
  )
}

function MessageBody({
  account,
  message,
}: {
  account: string
  message: Message
}) {
  const kind = message.category
  if (message.decode_error)
    return <p role='status'>消息正文解码失败，未使用摘要代替。</p>
  if (message.parse_error)
    return <p role='status'>消息结构无法解析，原始数据已保留。</p>
  if (['image', 'sticker', 'file', 'voice', 'video'].includes(kind || ''))
    return (
      <MediaContent
        key={`${account}:${message.id}:${message.revision}`}
        account={account}
        message={message}
      />
    )
  if (kind === 'text' || kind === 'quote')
    return (
      <>
        <p className='message-text'>
          <WechatText text={message.content} />
        </p>
      </>
    )
  if (kind === 'public_articles')
    return (
      <section className='public-articles' aria-label='公众号图文'>
        {message.articles?.length ? (
          message.articles.map((article, index) => (
            <ArticleLink
              key={`${index}:${article.url}`}
              article={article}
              lead={index === 0}
            />
          ))
        ) : (
          <p className='article-empty'>公众号消息暂不包含可读取的图文内容。</p>
        )}
        {message.source_name && <footer>{message.source_name}</footer>}
      </section>
    )
  if (kind === 'link') {
    const url = externalLink(message.url)
    const content = (
      <>
        <strong>
          <WechatText text={message.title || '链接'} />
        </strong>
        <div className='link-card-detail'>
          <p>{message.description}</p>
          <ArticleCover
            key={message.cover_url}
            url={message.cover_url}
            title={message.title || '链接封面'}
          />
        </div>
        <small>
          <ExternalLink size={13} />
          {message.source_name || '网页链接'}
        </small>
      </>
    )
    return url ? (
      <a
        className='link-card'
        href={url}
        target='_blank'
        rel='noopener noreferrer'
        aria-label={`打开链接：${message.title || '链接'}`}
      >
        {content}
      </a>
    ) : (
      <div className='link-card'>
        {content}
        <small>链接不可用</small>
      </div>
    )
  }
  if (kind === 'chat_history' || kind === 'note')
    return (
      <details className='structured-message record-message'>
        <summary>
          {message.title || (kind === 'note' ? '笔记' : '合并聊天记录')}
        </summary>
        {message.records?.length ? (
          <RecordList
            account={account}
            message={message}
            records={message.records}
          />
        ) : (
          <p>原消息未包含完整记录内容。</p>
        )}
        {message.records_truncated && (
          <small>较长记录已折叠，请在微信中查看完整内容。</small>
        )}
      </details>
    )
  if (kind === 'contact_card')
    return (
      <div className='structured-message'>
        <UserRound size={24} />
        <strong>{message.card?.name || '联系人名片'}</strong>
        <p>{message.card?.username}</p>
        <small>{message.card?.description}</small>
      </div>
    )
  if (kind === 'location')
    return (
      <div className='structured-message'>
        <MapPin size={24} />
        <strong>{message.location?.poiname || '位置'}</strong>
        <p>{message.location?.label}</p>
        <small>
          {message.location?.x}, {message.location?.y}
        </small>
      </div>
    )
  if (kind === 'announcement')
    return (
      <section
        className='structured-message announcement-card'
        aria-label='群公告'
      >
        <strong>群公告</strong>
        <p className='message-text'>
          <WechatText text={message.content || '原消息未包含公告正文'} />
        </p>
      </section>
    )
  if (kind === 'call')
    return (
      <section className='structured-message call-card' aria-label='通话记录'>
        <strong>通话记录</strong>
        <p>
          <WechatText
            text={
              message.call?.text || message.content || '原消息未包含通话详情'
            }
          />
        </p>
        {!!message.call?.duration && (
          <small>时长 {message.call.duration} 秒</small>
        )}
      </section>
    )
  if (kind === 'transfer' || kind === 'redpacket')
    return (
      <section
        className={'structured-message payment-card payment-' + kind}
        aria-label={kind === 'redpacket' ? '红包记录' : '转账记录'}
      >
        <strong>{kind === 'redpacket' ? '微信红包' : '微信转账'}</strong>
        {message.payment?.amount && <b>{message.payment.amount}</b>}
        <p>
          <WechatText text={message.payment?.remark || message.title} />
        </p>
        {(message.payment?.status || message.description) && (
          <small>
            <WechatText text={message.payment?.status || message.description} />
          </small>
        )}
      </section>
    )
  if (
    [
      'mini_program',
      'game',
      'finder_video',
      'finder_live',
      'shared_video',
      'music',
    ].includes(kind || '')
  )
    return <SharedCard message={message} />
  if (kind === 'system' || kind === 'pat')
    return (
      <p className='message-text'>
        <WechatText text={message.content || message.title || '系统通知'} />
      </p>
    )
  const label =
    message.type_label ||
    (
      {
        transfer: '转账',
        redpacket: '红包',
        call: '通话记录',
        status: '状态消息',
        app: '应用消息',
        friend_request: '好友请求',
        file_notice: '文件状态',
      } as Record<string, string>
    )[kind || ''] ||
    '特殊消息'
  return (
    <div className='structured-message'>
      <strong>{label}</strong>
      {message.title && (
        <p>
          <WechatText text={message.title} />
        </p>
      )}
      {message.content && message.content !== message.title && (
        <p className='message-text'>
          <WechatText text={message.content} />
        </p>
      )}
      {message.description && (
        <small>
          <WechatText text={message.description} />
        </small>
      )}
      {!message.title && !message.content && !message.description && (
        <small>原消息没有可展示的正文。</small>
      )}
    </div>
  )
}

function SharedCard({ message }: { message: Message }) {
  const label =
    message.type_label ||
    (
      {
        mini_program: '小程序',
        game: '小游戏',
        finder_video: '视频号',
        finder_live: '视频号直播',
        shared_video: '视频分享',
        music: '音乐分享',
      } as Record<string, string>
    )[message.category || ''] ||
    '分享'
  const url = externalLink(message.url)
  const content = (
    <>
      <strong>
        <WechatText text={message.title || label} />
      </strong>
      <div className='link-card-detail'>
        <p>
          <WechatText text={message.description} />
        </p>
        <ArticleCover
          key={message.cover_url}
          url={message.cover_url}
          title={message.title || label}
        />
      </div>
      <small>
        {label}
        {message.source_name ? ' · ' + message.source_name : ''}
      </small>
      {!url && <small>请在微信中打开</small>}
    </>
  )
  return url ? (
    <a
      className='link-card rich-share-card'
      href={url}
      target='_blank'
      rel='noopener noreferrer'
      aria-label={'打开分享：' + (message.title || label)}
    >
      {content}
    </a>
  ) : (
    <div className='link-card rich-share-card'>{content}</div>
  )
}

function RecordList({
  account,
  message,
  records,
  prefix = '',
  depth = 0,
}: {
  account: string
  message: Message
  records: MessageRecord[]
  prefix?: string
  depth?: number
}) {
  return (
    <div className='record-list'>
      {records.slice(0, 100).map((record, index) => {
        const path = prefix ? prefix + '.' + index : String(index)
        const url = externalLink(record.url)
        return (
          <blockquote key={path}>
            <strong>{record.sender || '原消息'}</strong>
            {record.kind &&
            ['image', 'sticker', 'voice', 'video', 'file'].includes(
              record.kind
            ) ? (
              <MediaContent
                account={account}
                recordPath={path}
                message={{
                  ...message,
                  category: record.kind,
                  file_name: record.file_name,
                  file_size: record.file_size,
                  duration: record.duration,
                  reference: null,
                }}
              />
            ) : (
              <span>
                <WechatText text={record.content} />
              </span>
            )}
            {url && (
              <a href={url} target='_blank' rel='noopener noreferrer'>
                打开记录链接
              </a>
            )}
            {!!record.children?.length && depth < 2 && (
              <details>
                <summary>{record.title || '嵌套聊天记录'}</summary>
                <RecordList
                  account={account}
                  message={message}
                  records={record.children}
                  prefix={path}
                  depth={depth + 1}
                />
              </details>
            )}
            {record.truncated && <small>更深层的记录请在微信中查看。</small>}
          </blockquote>
        )
      })}
    </div>
  )
}

export function MessageContent({
  account,
  message,
  locate,
}: {
  account: string
  message: Message
  locate: (server: string) => void
}) {
  const reference = message.reference
  const refUrl = externalLink(reference?.url)
  return (
    <>
      <MessageBody account={account} message={message} />
      {reference && (
        <blockquote className='message-reference'>
          <strong>{reference.display_name || '引用消息'}</strong>
          <span>
            <WechatText text={reference.content || '原消息待定位'} />
          </span>
          {refUrl && (
            <a href={refUrl} target='_blank' rel='noopener noreferrer'>
              打开引用链接
            </a>
          )}
          {reference.server_id && (
            <Button
              size='sm'
              variant='ghost'
              onClick={() => locate(reference.server_id!)}
            >
              定位原消息
            </Button>
          )}
        </blockquote>
      )}
    </>
  )
}
