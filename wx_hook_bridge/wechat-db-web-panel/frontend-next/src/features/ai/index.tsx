import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ellipsis, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ThemeSwitch } from '@/components/theme-switch'
import { EmptyState, ErrorText, Panel } from '@/features/group-assistant/shared'
import { formatTime } from '@/features/group-assistant/utils'
import { replyApi } from '@/features/group-replies/api'
import { reportApi } from '@/features/group-reports/api'
import { aiApi, providerKinds, type ModelProfile, type Provider } from './api'
import { ModelPicker } from './model-picker'
import { ModelSheet } from './model-sheet'
import { ProviderSheet } from './provider-sheet'
import { decisionFormats, modelName, modelUsage, purposeName } from './utils'

type TestResult = { ok: boolean; text: string }
type Removal =
  | { type: 'provider'; provider: Provider; models: ModelProfile[] }
  | { type: 'model'; profile: ModelProfile }

const host = (url: string) => {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export function AIPage() {
  const client = useQueryClient()
  const status = useQuery({ queryKey: ['ai-profiles'], queryFn: aiApi.status })
  const reports = useQuery({
    queryKey: ['group-reports'],
    queryFn: reportApi.status,
  })
  const replies = useQuery({
    queryKey: ['group-replies'],
    queryFn: replyApi.status,
  })
  const [providerSheet, setProviderSheet] = useState<{
    provider?: Provider
  } | null>(null)
  const [modelSheet, setModelSheet] = useState<{
    provider: Provider
    profile?: ModelProfile
  } | null>(null)
  const [picker, setPicker] = useState<Provider | null>(null)
  const [removing, setRemoving] = useState<Removal | null>(null)
  const [results, setResults] = useState<Record<string, TestResult>>({})
  const refresh = () => client.invalidateQueries({ queryKey: ['ai-profiles'] })

  const usage = useMemo(
    () => modelUsage(reports.data?.rules ?? [], replies.data?.rules ?? []),
    [reports.data, replies.data]
  )
  const profiles = status.data?.profiles ?? []
  const providers = status.data?.providers ?? []

  const report = (id: string, result: TestResult) =>
    setResults((current) => ({ ...current, [id]: result }))
  const test = useMutation({
    mutationFn: aiApi.test,
    onMutate: (id) => report(id, { ok: true, text: '测试中…' }),
    onSuccess: (result, id) => {
      report(id, {
        ok: true,
        text: `连接正常 · ${(result.elapsed_ms / 1000).toFixed(1)} 秒`,
      })
      void refresh()
    },
    onError: (error: Error, id) =>
      report(id, { ok: false, text: error.message }),
  })
  const check = useMutation({
    mutationFn: aiApi.discoverModels,
    onMutate: (id) => report(id, { ok: true, text: '检测中…' }),
    onSuccess: (result, id) =>
      report(id, {
        ok: true,
        text: `连接正常 · 供应商提供 ${result.models.length} 个模型 · ${(result.elapsed_ms / 1000).toFixed(1)} 秒`,
      }),
    onError: (error: Error, id) =>
      report(id, { ok: false, text: error.message }),
  })
  const testSearch = useMutation({
    mutationFn: ({ profile }: { provider: string; profile: string }) =>
      aiApi.testSearchTools(profile),
    onMutate: ({ provider }) => report(provider, { ok: true, text: '测试中…' }),
    onSuccess: (result, { provider }) =>
      report(provider, {
        ok: true,
        text: `Search / Fetch 正常 · ${result.search_results} 条结果 · ${result.fetch_chars} 字正文`,
      }),
    onError: (error: Error, { provider }) =>
      report(provider, { ok: false, text: error.message }),
  })
  const remove = useMutation({
    mutationFn: (target: Removal) =>
      target.type === 'provider'
        ? aiApi.removeProvider(target.provider.id, target.provider.revision)
        : aiApi.remove(target.profile.id, target.profile.revision),
    onSuccess: () => {
      setRemoving(null)
      void refresh()
    },
  })

  return (
    <>
      <Header>
        <ThemeSwitch />
      </Header>
      <Main className='space-y-6'>
        <div className='flex flex-wrap items-start justify-between gap-4'>
          <div>
            <h1 className='text-2xl font-bold'>AI 接入</h1>
            <p className='mt-2 text-sm text-muted-foreground'>
              先添加供应商（接口地址和密钥），再在它下面添加要用的模型。群日报、群问答从这里选模型。
            </p>
          </div>
          <Button onClick={() => setProviderSheet({})}>
            <Plus /> 添加供应商
          </Button>
        </div>
        <ErrorText error={status.error} />

        {status.data && providers.length === 0 && (
          <EmptyState
            title='还没有供应商'
            description='添加一个模型服务的接口地址和 API Key，然后在它下面添加模型。'
            action={
              <Button onClick={() => setProviderSheet({})}>添加供应商</Button>
            }
          />
        )}

        {providers.map((provider) => {
          const models = profiles.filter(
            (profile) => profile.provider_id === provider.id
          )
          const result = results[provider.id]
          return (
            <Panel
              key={provider.id}
              className={cn(!provider.config.enabled && 'opacity-70')}
              title={
                <span className='flex flex-wrap items-center gap-2'>
                  {provider.config.name}
                  <Badge variant='outline'>
                    {providerKinds[provider.config.kind].label}
                  </Badge>
                  {!provider.config.enabled && (
                    <Badge variant='secondary'>已停用</Badge>
                  )}
                  {!provider.api_key_set && (
                    <Badge variant='secondary'>未配置密钥</Badge>
                  )}
                  {provider.config.search_fetch && (
                    <Badge variant='secondary'>Search / Fetch</Badge>
                  )}
                </span>
              }
              description={host(provider.config.base_url)}
              action={
                <div className='flex gap-2'>
                  <Button
                    size='sm'
                    variant='outline'
                    disabled={
                      check.isPending && check.variables === provider.id
                    }
                    onClick={() => check.mutate(provider.id)}
                  >
                    检测连接
                  </Button>
                  <Button
                    size='sm'
                    variant='outline'
                    onClick={() => setPicker(provider)}
                  >
                    <Plus /> 添加模型
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        size='icon'
                        variant='ghost'
                        aria-label={`${provider.config.name} 更多操作`}
                      >
                        <Ellipsis />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align='end'>
                      <DropdownMenuItem
                        onSelect={() => setProviderSheet({ provider })}
                      >
                        编辑地址和密钥
                      </DropdownMenuItem>
                      {provider.config.search_fetch && models[0] && (
                        <DropdownMenuItem
                          onSelect={() =>
                            testSearch.mutate({
                              provider: provider.id,
                              profile: models[0].id,
                            })
                          }
                        >
                          测试 Search / Fetch
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant='destructive'
                        onSelect={() =>
                          setRemoving({ type: 'provider', provider, models })
                        }
                      >
                        删除供应商
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              }
            >
              {result && (
                <p
                  role='status'
                  className={cn(
                    'text-sm',
                    result.ok ? 'text-muted-foreground' : 'text-destructive'
                  )}
                >
                  {result.text}
                </p>
              )}
              {models.length === 0 ? (
                <p className='text-sm text-muted-foreground'>
                  还没有模型，点“添加模型”。
                </p>
              ) : (
                <ul className='divide-y rounded-lg border'>
                  {models.map((profile) => (
                    <ModelRow
                      key={profile.id}
                      profile={profile}
                      usedBy={usage.get(profile.id) ?? []}
                      result={results[profile.id]}
                      testing={test.isPending && test.variables === profile.id}
                      onTest={() => test.mutate(profile.id)}
                      onEdit={() => setModelSheet({ provider, profile })}
                      onRemove={() => setRemoving({ type: 'model', profile })}
                    />
                  ))}
                </ul>
              )}
            </Panel>
          )
        })}

        {status.data && status.data.recent_usage.length > 0 && (
          <Panel title='最近调用'>
            <div className='overflow-x-auto'>
              <table className='w-full text-left text-sm'>
                <thead className='text-muted-foreground'>
                  <tr className='border-b'>
                    <th className='py-2 font-normal'>时间</th>
                    <th className='font-normal'>用途</th>
                    <th className='font-normal'>模型</th>
                    <th className='font-normal'>结果</th>
                    <th className='text-right font-normal'>Token</th>
                    <th className='text-right font-normal'>耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {status.data.recent_usage.map((row, index) => (
                    <tr key={index} className='border-b last:border-0'>
                      <td className='py-2 whitespace-nowrap'>
                        {formatTime(row.created_at)}
                      </td>
                      <td>{purposeName(row.purpose)}</td>
                      <td>
                        {profiles.find((item) => item.id === row.profile_id)
                          ?.config.name ?? row.model}
                      </td>
                      <td>
                        {row.status === 'completed' ? (
                          '完成'
                        ) : (
                          <span className='text-destructive'>失败</span>
                        )}
                      </td>
                      <td className='text-right'>
                        {row.usage.total_tokens?.toLocaleString() ?? '—'}
                      </td>
                      <td className='text-right'>
                        {(row.elapsed_ms / 1000).toFixed(1)} 秒
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
      </Main>

      {providerSheet && (
        <ProviderSheet
          open
          provider={providerSheet.provider}
          onOpenChange={(open) => !open && setProviderSheet(null)}
          onSaved={(provider, created) => {
            setProviderSheet(null)
            void refresh()
            if (created) setPicker(provider)
          }}
        />
      )}
      {picker && status.data && (
        <ModelPicker
          open
          provider={picker}
          existing={profiles.filter((item) => item.provider_id === picker.id)}
          catalog={status.data.catalog}
          onOpenChange={(open) => !open && setPicker(null)}
          onAdded={(count) => {
            report(picker.id, { ok: true, text: `已添加 ${count} 个模型` })
            setPicker(null)
            void refresh()
          }}
        />
      )}
      {modelSheet && status.data && (
        <ModelSheet
          open
          provider={modelSheet.provider}
          profile={modelSheet.profile}
          catalog={status.data.catalog}
          onOpenChange={(open) => !open && setModelSheet(null)}
          onSaved={() => {
            setModelSheet(null)
            void refresh()
          }}
        />
      )}
      <AlertDialog
        open={!!removing}
        onOpenChange={(open) => !open && setRemoving(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {removing?.type === 'provider'
                ? `删除供应商“${removing.provider.config.name}”？`
                : removing
                  ? `删除模型“${removing.profile.config.name}”？`
                  : ''}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className='space-y-2'>
                {removing?.type === 'provider' && (
                  <p>
                    会同时删除它下面的 {removing.models.length}{' '}
                    个模型和保存的密钥。
                  </p>
                )}
                {removing && <InUseWarning target={removing} usage={usage} />}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ErrorText error={remove.error} />
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className='bg-destructive text-white hover:bg-destructive/90'
              disabled={remove.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (removing) remove.mutate(removing)
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function InUseWarning({
  target,
  usage,
}: {
  target: Removal
  usage: Map<string, string[]>
}) {
  const ids =
    target.type === 'provider'
      ? target.models.map((model) => model.id)
      : [target.profile.id]
  const used = ids.flatMap((id) => usage.get(id) ?? [])
  if (!used.length) return null
  return (
    <p className='text-destructive'>
      正在被使用：{used.join('、')}
      。删除后这些功能会因找不到模型而失败，需要重新选择。
    </p>
  )
}

function ModelRow({
  profile,
  usedBy,
  result,
  testing,
  onTest,
  onEdit,
  onRemove,
}: {
  profile: ModelProfile
  usedBy: string[]
  result?: TestResult
  testing: boolean
  onTest: () => void
  onEdit: () => void
  onRemove: () => void
}) {
  const decision = profile.model.api_format in decisionFormats
  return (
    <li
      className={cn(
        'flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3',
        !profile.model.enabled && 'opacity-60'
      )}
    >
      <div className='min-w-0 flex-1 basis-60'>
        <p className='flex flex-wrap items-center gap-2 text-sm'>
          <span className='font-medium'>{modelName(profile)}</span>
          {profile.model.label && (
            <code className='text-xs text-muted-foreground'>
              {profile.model.model}
            </code>
          )}
          <Badge variant={decision ? 'secondary' : 'outline'}>
            {decision ? '消息过滤' : '文本生成'}
          </Badge>
          {!profile.model.enabled && <Badge variant='secondary'>已停用</Badge>}
        </p>
        <p className='truncate text-xs text-muted-foreground'>
          {usedBy.length ? `用于 ${usedBy.join('、')}` : '暂未被使用'}
        </p>
        {result && (
          <p
            role='status'
            className={cn(
              'text-xs',
              result.ok ? 'text-emerald-600' : 'text-destructive'
            )}
          >
            {result.text}
          </p>
        )}
      </div>
      <div className='flex items-center gap-1'>
        <Button
          size='sm'
          variant='ghost'
          disabled={!profile.config.enabled || testing}
          onClick={onTest}
        >
          测试
        </Button>
        <Button size='sm' variant='ghost' onClick={onEdit}>
          编辑
        </Button>
        <Button
          size='sm'
          variant='ghost'
          className='text-destructive'
          onClick={onRemove}
        >
          删除
        </Button>
      </div>
    </li>
  )
}
