import { ContactAvatar } from './chat-ui'
import type { PendingSend } from './pending-sends'

const labels = {
  dispatching: '正在发送…',
  accepted: '已提交，等待微信回显',
  unknown: '结果未确认，请在微信核对',
  failed: '发送失败',
}

export function PendingSendRow({
  message,
  selfName,
  avatarUrl,
}: {
  message: PendingSend
  selfName: string
  avatarUrl?: string
}) {
  const error = message.status === 'failed' || message.status === 'unknown'
  return (
    <div
      className='message-block pending-send'
      data-pending-request-id={message.request}
    >
      <article className='message-row is-self' aria-label='本地发送记录'>
        <ContactAvatar name={selfName} url={avatarUrl} />
        <div className='message-body'>
          <div className='message-bubble message-kind-text'>
            <p className='message-text'>{message.text}</p>
          </div>
          <p
            className={`pending-send-status ${error ? 'is-error' : ''}`}
            role='status'
          >
            {labels[message.status]}
          </p>
        </div>
      </article>
    </div>
  )
}
