import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { api, type Me, type Member } from './api'

export function Members({ me }: { me: Me }) {
  const client = useQueryClient()
  const members = useQuery({
    queryKey: ['members'],
    queryFn: () => api<Member[]>('/members'),
  })
  const [target, setTarget] = useState<Member | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return (
    <>
      <div>
        <h1 className='text-2xl font-semibold'>成员管理</h1>
        <p className='mt-2 text-sm text-muted-foreground'>
          成员填写 Git
          邮箱即可参与，无需邀请或设置密码。邮箱是自填署名，不代表已验证身份。
        </p>
      </div>
      {members.error && (
        <div role='alert' className='text-sm text-destructive'>
          {members.error.message}
          <Button variant='ghost' onClick={() => void members.refetch()}>
            重试
          </Button>
        </div>
      )}
      <div className='overflow-hidden rounded-lg border'>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Git 邮箱 / 历史账号</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {members.data?.map((m) => (
              <TableRow key={m.id}>
                <TableCell className='max-w-sm break-all whitespace-normal'>
                  {m.login}
                </TableCell>
                <TableCell>
                  <Badge variant='outline'>
                    {m.id === me.id
                      ? '所有者'
                      : !m.has_account
                        ? '历史账号'
                        : m.disabled
                          ? '已停用'
                          : '可参与'}
                  </Badge>
                </TableCell>
                <TableCell>
                  {m.id !== me.id && m.has_account && (
                    <Button
                      size='sm'
                      variant='outline'
                      onClick={() => {
                        setTarget(m)
                        setError('')
                      }}
                    >
                      {m.disabled ? '恢复使用' : '停用成员'}
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <Dialog
        open={!!target}
        onOpenChange={(open) => {
          if (!open && !busy) setTarget(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {target?.disabled ? '恢复成员' : '停用成员'}
            </DialogTitle>
            <DialogDescription>
              停用后该邮箱不能继续参与，已有记录保留。此操作不改变 Git
              仓库权限。
            </DialogDescription>
          </DialogHeader>
          <form
            className='space-y-4'
            onSubmit={async (e) => {
              e.preventDefault()
              setBusy(true)
              setError('')
              const reason = new FormData(e.currentTarget).get('reason')
              try {
                await api('/members/' + target!.id + '/state', {
                  method: 'POST',
                  headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': me.csrf,
                  },
                  body: JSON.stringify({ enabled: target!.disabled, reason }),
                })
                setTarget(null)
                await Promise.all(
                  ['members', 'task'].map((key) =>
                    client.invalidateQueries({ queryKey: [key] })
                  )
                )
              } catch (e) {
                setError((e as Error).message)
              } finally {
                setBusy(false)
              }
            }}
          >
            <label className='grid gap-2 text-sm'>
              操作原因
              <Input name='reason' required maxLength={500} />
            </label>
            {error && (
              <p role='alert' className='text-sm text-destructive'>
                {error}
              </p>
            )}
            <Button disabled={busy}>确认操作</Button>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
