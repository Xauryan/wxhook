import { memo } from 'react'
import type { Message } from './api'
import { ContactAvatar } from './chat-ui'
import { MessageContent } from './message-content'
import { displayTime } from './message-flow'

export const MessageRow = memo(function MessageRow({
  account,
  message,
  separator,
  group,
  selfName,
  highlighted,
  locate,
}: {
  account: string
  message: Message
  separator: boolean
  group: boolean
  selfName: string
  highlighted: boolean
  locate: (server: string) => void
}) {
  const name = message.is_self ? selfName : message.sender_name || '未知发送人'
  return (
    <div
      data-message-id={message.id}
      className={`message-block ${highlighted ? 'message-highlight' : ''}`}
    >
      {separator && (
        <div className='message-time'>
          {displayTime(message.create_time, true)}
        </div>
      )}
      {['system', 'pat'].includes(message.category || '') ? (
        <div className='system-message'>{message.content || '系统通知'}</div>
      ) : (
        <article className={`message-row ${message.is_self ? 'is-self' : ''}`}>
          <ContactAvatar name={name} url={message.avatar_url} />
          <div className='message-body'>
            {!message.is_self && group && (
              <span className='sender-name'>{name}</span>
            )}
            <div
              className={`message-bubble message-kind-${message.category || 'other'}`}
            >
              <MessageContent
                account={account}
                message={message}
                locate={locate}
              />
            </div>
          </div>
        </article>
      )}
    </div>
  )
})
