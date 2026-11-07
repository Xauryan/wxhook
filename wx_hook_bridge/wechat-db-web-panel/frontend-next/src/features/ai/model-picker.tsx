import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { ErrorText } from '@/features/group-assistant/shared'
import { selectStyle } from '@/features/group-assistant/utils'
import {
  aiApi,
  type ModelCapabilities,
  type ModelConfig,
  type ModelProfile,
  type Provider,
} from './api'
import { modelFormats, newModel } from './utils'

/** Pick several models at once from the provider's own model list. */
export function ModelPicker({
  open,
  provider,
  existing,
  catalog,
  onOpenChange,
  onAdded,
}: {
  open: boolean
  provider: Provider
  existing: ModelProfile[]
  catalog: Record<string, ModelCapabilities>
  onOpenChange: (open: boolean) => void
  onAdded: (count: number) => void
}) {
  const discovered = useQuery({
    queryKey: ['ai-discover', provider.id, provider.revision],
    queryFn: () => aiApi.discoverModels(provider.id),
    enabled: open,
    retry: false,
    staleTime: 60_000,
  })
  const [selected, setSelected] = useState<string[]>([])
  const [manual, setManual] = useState<string[]>([])
  const [typed, setTyped] = useState('')
  const [filter, setFilter] = useState('')
  const [format, setFormat] = useState<ModelConfig['api_format']>('auto')
  const added = useMemo(
    () =>
      new Set(
        existing
          .filter((profile) => profile.model.api_format === format)
          .map((profile) => profile.model.model)
      ),
    [existing, format]
  )
  const names = useMemo(() => {
    const listed = discovered.data?.models ?? []
    const extra = manual
      .filter((id) => !listed.some((item) => item.id === id))
      .map((id) => ({ id, name: null }))
    return [...extra, ...listed]
  }, [discovered.data, manual])
  const visible = names.filter(
    (item) =>
      !filter ||
      item.id.toLowerCase().includes(filter.toLowerCase()) ||
      item.name?.toLowerCase().includes(filter.toLowerCase())
  )
  const toggle = (id: string, on: boolean) =>
    setSelected((current) =>
      on
        ? [...new Set([...current, id])]
        : current.filter((item) => item !== id)
    )
  const addTyped = () => {
    const id = typed.trim()
    if (!id) return
    setManual((current) => [...new Set([id, ...current])])
    toggle(id, true)
    setTyped('')
  }
  const choosable = visible.filter((item) => !added.has(item.id))
  const save = useMutation({
    mutationFn: () =>
      aiApi.addModels(
        provider.id,
        selected
          .filter((id) => !added.has(id))
          .map((id) => newModel(id, catalog, format))
      ),
    onSuccess: (result) => onAdded(result.items.length),
  })
  const count = selected.filter((id) => !added.has(id)).length

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className='w-full gap-0 sm:max-w-lg'>
        <SheetHeader className='border-b'>
          <SheetTitle>添加模型</SheetTitle>
          <SheetDescription>
            {provider.config.name} · 从供应商的模型列表里勾选，可一次添加多个
          </SheetDescription>
        </SheetHeader>
        <div className='flex min-h-0 flex-1 flex-col gap-3 p-4'>
          <div className='flex items-center gap-2'>
            <Input
              aria-label='筛选模型'
              placeholder='搜索模型名'
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
            <Button
              type='button'
              size='icon'
              variant='outline'
              aria-label='重新读取模型列表'
              disabled={discovered.isFetching}
              onClick={() => void discovered.refetch()}
            >
              <RefreshCw
                className={discovered.isFetching ? 'animate-spin' : ''}
              />
            </Button>
          </div>
          <div className='flex items-center justify-between text-sm text-muted-foreground'>
            <span>
              {discovered.isFetching
                ? '正在读取模型列表…'
                : discovered.data
                  ? `供应商提供 ${discovered.data.models.length} 个模型`
                  : ''}
            </span>
            {choosable.length > 0 && (
              <span className='flex gap-1'>
                <Button
                  type='button'
                  size='sm'
                  variant='ghost'
                  onClick={() =>
                    setSelected((current) => [
                      ...new Set([
                        ...current,
                        ...choosable.map((item) => item.id),
                      ]),
                    ])
                  }
                >
                  全选{filter ? '筛选结果' : ''}
                </Button>
                <Button
                  type='button'
                  size='sm'
                  variant='ghost'
                  onClick={() => setSelected([])}
                >
                  清空
                </Button>
              </span>
            )}
          </div>
          {discovered.error && (
            <p className='rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm'>
              {discovered.error.message}。可以在下面手动填写模型名。
            </p>
          )}
          <div className='min-h-32 flex-1 space-y-0.5 overflow-y-auto rounded-lg border p-1'>
            {visible.length === 0 && !discovered.isFetching && (
              <p className='p-3 text-sm text-muted-foreground'>
                {filter ? '没有匹配的模型' : '没有可选的模型'}
              </p>
            )}
            {visible.map((item) => {
              const exists = added.has(item.id)
              return (
                <label
                  key={item.id}
                  className='flex items-center gap-3 rounded px-2 py-1.5 text-sm hover:bg-muted/50 has-[:disabled]:opacity-60'
                >
                  <input
                    type='checkbox'
                    disabled={exists}
                    checked={exists || selected.includes(item.id)}
                    onChange={(event) => toggle(item.id, event.target.checked)}
                  />
                  <span className='min-w-0 flex-1 truncate'>
                    {item.id}
                    {item.name && item.name !== item.id && (
                      <span className='ms-2 text-xs text-muted-foreground'>
                        {item.name}
                      </span>
                    )}
                  </span>
                  {exists && (
                    <span className='text-xs text-muted-foreground'>
                      已添加
                    </span>
                  )}
                </label>
              )
            })}
          </div>
          <form
            className='flex gap-2'
            onSubmit={(event) => {
              event.preventDefault()
              addTyped()
            }}
          >
            <Input
              aria-label='手动填写模型名'
              placeholder='列表里没有？手动填写模型名后回车'
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
            />
            <Button type='submit' variant='outline' disabled={!typed.trim()}>
              加入
            </Button>
          </form>
          <div className='space-y-2'>
            <Label htmlFor='picker-format'>这些模型的用途</Label>
            <select
              id='picker-format'
              className={selectStyle}
              value={format}
              onChange={(event) =>
                setFormat(event.target.value as ModelConfig['api_format'])
              }
            >
              {modelFormats(provider.config.kind).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <p className='text-xs text-muted-foreground'>
              添加后可以单独编辑每个模型的显示名称、推理强度等参数。
            </p>
          </div>
        </div>
        <SheetFooter className='border-t'>
          <ErrorText error={save.error} />
          <div className='flex gap-2'>
            <Button
              disabled={!count || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? '添加中…' : `添加 ${count} 个模型`}
            </Button>
            <Button variant='outline' onClick={() => onOpenChange(false)}>
              取消
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
