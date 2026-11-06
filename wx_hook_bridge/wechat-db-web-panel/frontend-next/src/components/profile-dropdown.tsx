import { Link } from '@tanstack/react-router'
import { MessagesSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function ProfileDropdown() {
  return (
    <Button variant='ghost' size='icon' className='rounded-full' asChild>
      <Link to='/chats' aria-label='返回微信聊天' title='微信聊天'>
        <MessagesSquare aria-hidden='true' />
      </Link>
    </Button>
  )
}
