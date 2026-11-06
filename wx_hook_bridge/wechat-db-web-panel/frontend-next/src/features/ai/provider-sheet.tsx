import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { cn } from '@/lib/utils'
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
import { Switch } from '@/components/ui/switch'
import { ErrorText, FieldGroup } from '@/features/group-assistant/shared'
import {
  aiApi,
  providerKinds,
  type Provider,
  type ProviderConfig,
  type ProviderKind,
} from './api'

const defaults: ProviderConfig = {
  name: '',
  kind: 'openai',
  base_url: providerKinds.openai.url,
  search_fetch: false,
  enabled: true,
}

export function ProviderSheet({
  open,
  provider,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  provider?: Provider
  onOpenChange: (open: boolean) => void
  onSaved: (provider: Provider, created: boolean) => void
}) {
  const [config, setConfig] = useState<ProviderConfig>(
    provider?.config ?? defaults
  )
  const [key, setKey] = useState('')
  const [clear, setClear] = useState(false)
  const update = <K extends keyof ProviderConfig>(
    field: K,
    value: ProviderConfig[K]
  ) => setConfig((current) => ({ ...current, [field]: value }))
  const chooseKind = (kind: ProviderKind) =>
    setConfig((current) => {
      const known = Object.values(providerKinds).map((item) => item.url)
      return {
        ...current,
        kind,
        // Only replace an address the user has not typed themselves.
        base_url:
          !current.base_url || known.includes(current.base_url)
            ? providerKinds[kind].url
            : current.base_url,
        name:
          !current.name ||
          Object.values(providerKinds).some(
            (item) => item.label === current.name
          )
            ? kind === 'compatible'
              ? ''
              : providerKinds[kind].label
            : current.name,
        search_fetch: kind === 'deepseek',
      }
    })
  const save = useMutation({
    mutationFn: () =>
      aiApi.saveProvider(
        provider?.id,
        provider?.revision ?? 0,
        config,
        key,
        clear
      ),
    onSuccess: (saved) => onSaved(saved, !provider),
  })
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className='w-full gap-0 sm:max-w-lg'>
        <SheetHeader className='border-b'>
          <SheetTitle>{provider ? '编辑供应商' : '添加供应商'}</SheetTitle>
          <SheetDescription>
            接口地址和密钥只填一次，这个供应商下的所有模型共用。
          </SheetDescription>
        </SheetHeader>
        <form
          id='provider-form'
          className='flex-1 space-y-8 overflow-y-auto p-4'
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          <FieldGroup title='接口类型'>
            <div className='grid gap-2' role='radiogroup'>
              {(
                Object.entries(providerKinds) as [
                  ProviderKind,
                  (typeof providerKinds)[ProviderKind],
                ][]
              ).map(([kind, item]) => (
                <button
                  key={kind}
                  type='button'
                  role='radio'
                  aria-checked={config.kind === kind}
                  onClick={() => chooseKind(kind)}
                  className={cn(
                    'rounded-lg border px-3 py-2 text-left',
                    config.kind === kind
                      ? 'border-primary bg-primary/5'
                      : 'hover:bg-muted'
                  )}
                >
                  <span className='block text-sm font-medium'>
                    {item.label}
                  </span>
                  <span className='text-xs text-muted-foreground'>
                    {item.hint}
                  </span>
                </button>
              ))}
            </div>
          </FieldGroup>
          <FieldGroup title='连接'>
            <div className='space-y-2'>
              <Label htmlFor='provider-name'>名称</Label>
              <Input
                id='provider-name'
                required
                maxLength={60}
                value={config.name}
                onChange={(e) => update('name', e.target.value)}
                placeholder='例如 OpenAI 官方、某某中转'
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='provider-url'>接口根地址</Label>
              <Input
                id='provider-url'
                type='url'
                required
                value={config.base_url}
                onChange={(e) => update('base_url', e.target.value)}
                placeholder='https://你的模型服务/v1'
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='provider-key'>API Key</Label>
              <Input
                id='provider-key'
                type='password'
                autoComplete='new-password'
                value={key}
                disabled={clear}
                onChange={(e) => setKey(e.target.value)}
                placeholder={
                  provider?.api_key_set
                    ? '已配置，填写新值可替换'
                    : '本机免密服务可留空'
                }
              />
              <p className='text-xs text-muted-foreground'>
                使用 Windows DPAPI 加密保存在本机。更换接口地址时需要重新填写。
              </p>
            </div>
            {provider?.api_key_set && (
              <label className='flex items-center gap-2 text-sm'>
                <input
                  type='checkbox'
                  checked={clear}
                  onChange={(e) => {
                    setClear(e.target.checked)
                    setKey('')
                  }}
                />
                清除已保存的密钥
              </label>
            )}
          </FieldGroup>
          <FieldGroup title='状态'>
            <div className='flex items-center gap-3'>
              <Switch
                id='provider-enabled'
                checked={config.enabled}
                onCheckedChange={(value) => update('enabled', value)}
              />
              <Label htmlFor='provider-enabled'>
                启用（关闭后它下面的模型都不可用）
              </Label>
            </div>
            {config.kind === 'deepseek' && (
              <div className='flex items-center gap-3'>
                <Switch
                  id='provider-search'
                  checked={config.search_fetch}
                  onCheckedChange={(value) => update('search_fetch', value)}
                />
                <Label htmlFor='provider-search'>
                  同时用于 DeepSeek Search / Fetch 网页检索
                </Label>
              </div>
            )}
          </FieldGroup>
        </form>
        <SheetFooter className='border-t'>
          <ErrorText error={save.error} />
          <div className='flex gap-2'>
            <Button
              type='submit'
              form='provider-form'
              disabled={save.isPending}
            >
              {save.isPending
                ? '保存中…'
                : provider
                  ? '保存'
                  : '保存并添加模型'}
            </Button>
            <Button
              type='button'
              variant='outline'
              onClick={() => onOpenChange(false)}
            >
              取消
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
