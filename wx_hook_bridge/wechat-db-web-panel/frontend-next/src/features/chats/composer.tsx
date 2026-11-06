import { useRef, useState } from 'react'
import {
  ChevronDown,
  Folder,
  MessageCircleMore,
  Phone,
  Scissors,
  Smile,
  Video,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Textarea } from '@/components/ui/textarea'
import { chatApi, type SendReceipt } from './api'

type Draft = { text: string; request?: string; uncertain?: boolean }
type SendNotice = {
  text: string
  request?: string
  status?: SendReceipt['status']
}
const EMOTICONS = [
  '微笑',
  '呲牙',
  '愉快',
  '偷笑',
  '憨笑',
  '调皮',
  '抱拳',
  '强',
  '握手',
  '爱心',
  '庆祝',
  '玫瑰',
]
export function Composer({
  account,
  username,
  enabled,
  confirmedRequests,
  onSearch,
  onSubmit,
  onReceipt,
  onUnknown,
}: {
  account: string
  username: string
  enabled: boolean
  confirmedRequests?: ReadonlySet<string>
  onSearch?: () => void
  onSubmit?: (request: string, text: string) => void
  onReceipt?: (receipt: SendReceipt) => void
  onUnknown?: (request: string) => void
}) {
  const key = `live-chat-draft:${account}:${username}`
  const [draft, setDraft] = useState<Draft>(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || '{}')
      return {
        text: typeof saved.text === 'string' ? saved.text : '',
        request: saved.request,
        uncertain: !!saved.request,
      }
    } catch {
      return { text: '' }
    }
  })
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<SendNotice>()
  // Derive visibility from the matching native echo, including echoes that
  // arrive before the HTTP receipt. Older echoes cannot clear a newer notice.
  const visibleNotice =
    notice?.request &&
    confirmedRequests?.has(notice.request) &&
    (notice.status === 'dispatching' || notice.status === 'accepted')
      ? undefined
      : notice
  const failed =
    visibleNotice?.status === 'unknown' || visibleNotice?.status === 'failed'
  const [emojiOpen, setEmojiOpen] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const inFlight = useRef(false)
  const save = (value: Draft) => {
    setDraft(value)
    try {
      sessionStorage.setItem(key, JSON.stringify(value))
    } catch {
      /* The in-memory draft remains available. */
    }
  }
  const applyReceipt = (receipt: SendReceipt) => {
    onReceipt?.(receipt)
    setNotice({
      text: receipt.detail || '请求处理中，请稍后查询状态',
      request: receipt.request_id,
      status: receipt.status,
    })
    if (receipt.status === 'accepted') save({ text: '' })
    else if (receipt.status === 'failed') save({ text: draft.text })
    else save({ ...draft, request: receipt.request_id, uncertain: true })
  }
  const send = async () => {
    if (
      inFlight.current ||
      busy ||
      !enabled ||
      draft.uncertain ||
      !draft.text.trim()
    )
      return
    inFlight.current = true
    const request = crypto.randomUUID()
    save({ ...draft, request, uncertain: true })
    setBusy(true)
    setNotice({ text: '正在提交给微信…', request, status: 'dispatching' })
    onSubmit?.(request, draft.text)
    try {
      applyReceipt(await chatApi.send(account, username, draft.text, request))
    } catch (error) {
      onUnknown?.(request)
      setNotice({
        text: `${error instanceof Error ? error.message : '网络异常'}。结果未确认，不会自动重发。`,
        request,
        status: 'unknown',
      })
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  const check = async () => {
    if (!draft.request || busy) return
    setBusy(true)
    try {
      applyReceipt(await chatApi.receipt(account, draft.request))
    } catch (error) {
      setNotice({
        text: error instanceof Error ? error.message : '查询失败',
        request: draft.request,
        status: 'unknown',
      })
    } finally {
      setBusy(false)
    }
  }
  const insertEmoticon = (name: string) => {
    if (busy || draft.uncertain) return
    const start = input.current?.selectionStart ?? draft.text.length
    const end = input.current?.selectionEnd ?? start
    const value = `[${name}]`
    if (draft.text.length - (end - start) + value.length > 10000) return
    save({ text: draft.text.slice(0, start) + value + draft.text.slice(end) })
    setEmojiOpen(false)
    requestAnimationFrame(() => {
      input.current?.focus()
      input.current?.setSelectionRange(
        start + value.length,
        start + value.length
      )
    })
  }
  return (
    <div
      className={`chat-composer ${visibleNotice || draft.uncertain ? 'has-notice' : ''}`}
    >
      <div
        className='composer-toolbar'
        role='toolbar'
        aria-label='聊天输入工具'
      >
        <Popover open={emojiOpen} onOpenChange={setEmojiOpen}>
          <PopoverTrigger asChild>
            <button
              type='button'
              className='composer-tool'
              aria-label='插入表情'
              title='表情'
              disabled={busy || draft.uncertain}
            >
              <Smile />
            </button>
          </PopoverTrigger>
          <PopoverContent
            side='top'
            align='start'
            className='chat-emoticons'
            onCloseAutoFocus={(event) => {
              event.preventDefault()
              input.current?.focus()
            }}
          >
            <p>插入微信文字表情</p>
            <div>
              {EMOTICONS.map((name) => (
                <button
                  type='button'
                  key={name}
                  onClick={() => insertEmoticon(name)}
                >{`[${name}]`}</button>
              ))}
            </div>
          </PopoverContent>
        </Popover>
        <button
          type='button'
          className='composer-tool'
          aria-label='发送文件（暂未接入）'
          title='文件发送暂未接入，仅支持文本发送'
          disabled
        >
          <Folder />
        </button>
        <button
          type='button'
          className='composer-tool composer-capture'
          aria-label='截图（暂未接入）'
          title='截图暂未接入'
          disabled
        >
          <Scissors />
          <ChevronDown className='composer-chevron' />
        </button>
        <button
          type='button'
          className='composer-tool'
          aria-label='搜索当前已加载消息'
          title='聊天记录'
          disabled={!onSearch}
          onClick={onSearch}
        >
          <MessageCircleMore />
        </button>
        <span className='composer-toolbar-spacer' />
        <button
          type='button'
          className='composer-tool'
          aria-label='语音通话（暂未接入）'
          title='语音通话暂未接入'
          disabled
        >
          <Phone />
        </button>
        <button
          type='button'
          className='composer-tool'
          aria-label='视频通话（暂未接入）'
          title='视频通话暂未接入'
          disabled
        >
          <Video />
        </button>
      </div>
      <Textarea
        ref={input}
        aria-label='聊天消息'
        title='Enter 发送，Shift + Enter 换行'
        placeholder={enabled ? '' : '微信未连接，可暂存草稿'}
        value={draft.text}
        maxLength={10000}
        disabled={busy || draft.uncertain}
        onChange={(event) => save({ text: event.target.value })}
        onKeyDown={(event) => {
          if (
            event.key === 'Enter' &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing &&
            event.keyCode !== 229
          ) {
            event.preventDefault()
            void send()
          }
        }}
      />
      {visibleNotice && (
        <p role={failed ? 'alert' : 'status'}>{visibleNotice.text}</p>
      )}
      <div className='composer-actions'>
        <span className='composer-hint'>Enter 发送，Shift + Enter 换行</span>
        {draft.uncertain ? (
          <>
            <Button
              size='sm'
              variant='outline'
              disabled={busy}
              onClick={() => void check()}
            >
              查询状态
            </Button>
            <Button
              size='sm'
              variant='ghost'
              disabled={busy}
              onClick={() => {
                save({ text: draft.text })
                setNotice({
                  text: '已由你核对发送结果，请谨慎决定是否再次发送',
                })
              }}
            >
              已在微信核对，继续编辑
            </Button>
          </>
        ) : (
          <Button
            className='composer-send'
            size='sm'
            disabled={!enabled || busy || !draft.text.trim()}
            onClick={() => void send()}
          >
            发送
          </Button>
        )}
      </div>
    </div>
  )
}
