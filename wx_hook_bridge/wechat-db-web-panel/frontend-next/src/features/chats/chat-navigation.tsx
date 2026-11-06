import { Link } from '@tanstack/react-router'
import {
  Aperture,
  Box,
  ContactRound,
  Menu,
  MessageCircle,
  RefreshCw,
  Settings,
  Undo2,
} from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import type { LiveStatus } from './api'
import { ContactAvatar } from './chat-ui'

export function ChatNavigation({
  live,
  connectionLabel,
  view,
  onViewChange,
  onRefresh,
  refreshing,
}: {
  live: LiveStatus
  connectionLabel: string
  view: 'chats' | 'contacts' | 'favorites' | 'moments' | 'recalls'
  onViewChange: (
    view: 'chats' | 'contacts' | 'favorites' | 'moments' | 'recalls'
  ) => void
  onRefresh: () => void
  refreshing: boolean
}) {
  const name =
    live.profile.display_name ||
    live.profile.nickname ||
    live.profile.username ||
    '微信'

  return (
    <nav className='chat-navigation' aria-label='微信工作台导航'>
      <Popover>
        <PopoverTrigger asChild>
          <button
            type='button'
            className='chat-account'
            aria-label='当前微信账号与连接状态'
            title={`${name} · ${connectionLabel}`}
          >
            <ContactAvatar name={name} url={live.profile.avatar_url} />
            {(!live.bridge_connected || !live.receive_connected) && (
              <span className='chat-connection-dot' />
            )}
          </button>
        </PopoverTrigger>
        <PopoverContent
          side='right'
          align='start'
          className='chat-account-info'
        >
          <strong>{name}</strong>
          <p role='status'>{connectionLabel}</p>
          <p>
            {live.contacts_error ||
              (live.contacts_syncing || !live.contacts_verified
                ? '正在后台核验并更新当前账号的好友与群聊'
                : '当前账号通讯录已核验')}
          </p>
          <p>
            接收积压 {live.relay_pending} 条，桥接累计丢弃 {live.relay_dropped}{' '}
            条。 未确认的消息不会作为已送达显示。
          </p>
        </PopoverContent>
      </Popover>

      <div className='chat-navigation-items'>
        <button
          type='button'
          className='chat-nav-button'
          aria-label='全部聊天'
          title='聊天'
          aria-pressed={view === 'chats'}
          onClick={() => onViewChange('chats')}
        >
          <MessageCircle className='chat-nav-message' />
        </button>
        <button
          type='button'
          className='chat-nav-button'
          aria-label='联系人'
          title='联系人'
          aria-pressed={view === 'contacts'}
          onClick={() => onViewChange('contacts')}
        >
          <ContactRound />
        </button>
        <button
          type='button'
          className='chat-nav-button'
          aria-label='收藏'
          title='收藏'
          aria-pressed={view === 'favorites'}
          onClick={() => onViewChange('favorites')}
        >
          <Box />
        </button>
        <button
          type='button'
          className='chat-nav-button'
          aria-label='朋友圈'
          title='朋友圈'
          aria-pressed={view === 'moments'}
          onClick={() => onViewChange('moments')}
        >
          <Aperture />
        </button>
        <button
          type='button'
          className='chat-nav-button'
          aria-label='撤回记录'
          title='撤回记录'
          aria-pressed={view === 'recalls'}
          onClick={() => onViewChange('recalls')}
        >
          <Undo2 />
        </button>
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type='button'
            className='chat-nav-button chat-nav-menu'
            aria-label='工作台菜单'
            title='更多'
          >
            <Menu />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side='right' align='end'>
          <DropdownMenuLabel>{connectionLabel}</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={onRefresh} disabled={refreshing}>
            <RefreshCw className={refreshing ? 'spin' : ''} />
            刷新会话
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link to='/settings/appearance'>
              <Settings />
              外观设置
            </Link>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </nav>
  )
}
