import { useDeferredValue, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import {
  ArrowUpRight,
  LoaderCircle,
  RefreshCw,
  Search,
  Undo2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { chatApi, type ChatTarget, type LiveStatus, type Session } from './api'
import { Notice } from './chat-ui'
import './recalls.css'
import { WechatText } from './wechat-text'

function timeLabel(value: number) {
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}

export function RecallsWorkspace({
  live,
  openChat,
}: {
  live: LiveStatus
  openChat: (session: Session, target?: ChatTarget) => void
}) {
  const [search, setSearch] = useState('')
  const q = useDeferredValue(search.trim())
  const list = useInfiniteQuery({
    queryKey: ['wechat-recalls', live.account, q],
    queryFn: ({ signal, pageParam }) =>
      chatApi.recalls(live.account, q, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: !!live.account,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    // Durable HTTP catch-up also works when the SSE connection reconnects.
    refetchInterval: 15_000,
  })
  const items = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.items) ?? []).map((item) => [
        item.id,
        item,
      ])
    ).values(),
  ]
  const total = list.data?.pages[0]?.total ?? 0
  return (
    <section className='recalls-workspace' aria-label='撤回消息记录'>
      <header className='recalls-heading'>
        <div>
          <h2>
            <Undo2 size={20} />
            撤回记录 <span>{total}</span>
          </h2>
          <p>自动保存收到的撤回通知及已捕获的原文，可跳转到对应聊天位置。</p>
        </div>
        <Button
          variant='ghost'
          size='icon'
          aria-label='刷新撤回记录'
          disabled={list.isFetching}
          onClick={() => void list.refetch()}
        >
          <RefreshCw size={17} className={list.isFetching ? 'spin' : ''} />
        </Button>
      </header>
      <div className='recalls-toolbar'>
        <Search size={16} />
        <Input
          aria-label='搜索撤回记录'
          placeholder='搜索会话、发送者或原文'
          value={search}
          maxLength={200}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {!live.receive_connected && (
        <p className='recalls-note' role='status'>
          接收链路未就绪，恢复连接后继续自动收集。已有记录保留。
        </p>
      )}
      <div className='recalls-scroll' aria-busy={list.isFetching}>
        {list.isError && (
          <Notice error={list.error} retry={() => void list.refetch()} />
        )}
        {list.isPending && (
          <div className='chat-state'>
            <LoaderCircle className='spin' />
            <p>{live.account ? '读取撤回记录' : '请登录微信并启动桥接'}</p>
          </div>
        )}
        {!list.isPending && !list.isError && !items.length && (
          <div className='chat-state'>
            <Undo2 size={32} />
            <p>{q ? '没有找到匹配的撤回记录' : '暂无撤回记录'}</p>
            <small>
              收到撤回通知后会自动出现在这里；未捕获的原文无法恢复。
            </small>
          </div>
        )}
        {items.map((item) => (
          <article className='recall-card' key={item.id}>
            <div className='recall-card-heading'>
              <div>
                <strong>{item.session.display_name}</strong>
                <span>
                  {item.session.is_group ? '群聊' : '私聊'} · {item.sender_name}
                </span>
              </div>
              <time>
                {item.recalled_at ? '撤回于 ' : '发现于 '}
                {timeLabel(item.recalled_at || item.observed_at)}
              </time>
            </div>
            <p className='recall-notice'>{item.notice}</p>
            <div
              className={`recall-original ${item.original ? '' : 'recall-missing'}`}
            >
              <small>
                {item.original
                  ? item.original.content
                    ? '已保存原文'
                    : '已保存消息摘要'
                  : '原文未捕获'}
              </small>
              {item.original ? (
                <div className='recall-text'>
                  <WechatText
                    text={
                      item.original.content ||
                      item.original.title ||
                      item.original.file_name ||
                      item.original.summary ||
                      '[非文本消息]'
                    }
                  />
                </div>
              ) : (
                <p>撤回前的消息未进入接收缓存，无法还原内容。</p>
              )}
            </div>
            <footer>
              <span>消息 ID：{item.server_id}</span>
              <Button
                variant='ghost'
                size='sm'
                onClick={() =>
                  openChat(
                    item.session,
                    item.jump_server_id
                      ? { server: item.jump_server_id }
                      : item.jump_message_id
                        ? { messageId: item.jump_message_id }
                        : undefined
                  )
                }
              >
                {item.jump_server_id || item.jump_message_id
                  ? '查看聊天位置'
                  : '打开聊天'}
                <ArrowUpRight size={15} />
              </Button>
            </footer>
          </article>
        ))}
        {list.hasNextPage && (
          <Button
            variant='ghost'
            className='recalls-load'
            disabled={list.isFetching}
            onClick={() => void list.fetchNextPage()}
          >
            {list.isFetchingNextPage ? '读取中…' : '加载更早的撤回记录'}
          </Button>
        )}
      </div>
    </section>
  )
}
