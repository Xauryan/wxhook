import { memo, useState } from 'react'
import { Info, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { localAsset } from './api'

export const ContactAvatar = memo(function ContactAvatar({
  name,
  url,
  group = false,
}: {
  name: string
  url?: string
  group?: boolean
}) {
  const asset = localAsset(url)
  const [failed, setFailed] = useState<string>()
  const source = asset && failed !== asset ? asset : undefined
  return (
    <span
      className={`wechat-avatar relative inline-flex overflow-hidden ${group ? 'group-avatar' : ''}`}
    >
      <span
        data-slot='avatar-fallback'
        className='flex size-full items-center justify-center'
        aria-hidden={!!source}
        title={`${name} · 本地暂无头像`}
        aria-label={`${name} · 本地暂无头像`}
      >
        {group ? (
          <Users size={21} />
        ) : (
          Array.from(name || '?')
            .slice(0, 1)
            .join('')
        )}
      </span>
      {source && (
        <img
          className='absolute inset-0 size-full object-cover'
          src={source}
          alt={`${name}的头像`}
          loading='lazy'
          decoding='async'
          referrerPolicy='no-referrer'
          onError={() => setFailed(source)}
        />
      )}
    </span>
  )
})
export function Notice({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <div className='chat-notice' role='alert'>
      <Info size={18} />
      <p>{error.message}</p>
      <Button variant='outline' size='sm' onClick={retry}>
        重试
      </Button>
    </div>
  )
}
