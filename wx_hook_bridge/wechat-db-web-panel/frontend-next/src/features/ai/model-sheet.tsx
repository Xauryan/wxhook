import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
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
import { selectStyle } from '@/features/group-assistant/utils'
import {
  aiApi,
  type ModelCapabilities,
  type ModelConfig,
  type ModelProfile,
  type ModelSettings,
  type Provider,
} from './api'
import { decisionFormats } from './utils'

const suggested: Partial<Record<Provider['config']['kind'], string>> = {
  deepseek: 'deepseek-flash',
  openai: 'gpt-6-astra',
}

function formats(kind: Provider['config']['kind']) {
  const text: [ModelConfig['api_format'], string][] = [
    ['auto', '文本生成（自动选择协议）'],
  ]
  if (kind === 'compatible')
    text.push(
      ['responses', '文本生成 · OpenAI Responses'],
      ['chat_completions', '文本生成 · OpenAI Chat Completions'],
      ['anthropic', '文本生成 · Anthropic Messages']
    )
  if (kind === 'openai' || kind === 'compatible')
    for (const [value, label] of Object.entries(decisionFormats))
      if (kind === 'compatible' || value === 'openai_decisions')
        text.push([value as ModelConfig['api_format'], `消息过滤 · ${label}`])
  return text
}

export function ModelSheet({
  open,
  provider,
  profile,
  catalog,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  provider: Provider
  profile?: ModelProfile
  catalog: Record<string, ModelCapabilities>
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const initialModel = suggested[provider.config.kind] ?? ''
  const [config, setConfig] = useState<ModelSettings>(
    profile?.model ?? {
      label: null,
      model: initialModel,
      api_format: 'auto',
      reasoning_effort: null,
      enabled: true,
      timeout_seconds: 120,
      max_output_tokens: catalog[initialModel]?.max_output_tokens ?? 32768,
      context_chars: null,
      token_parameter: 'max_tokens',
      json_mode: false,
      temperature: null,
    }
  )
  const update = <K extends keyof ModelSettings>(
    field: K,
    value: ModelSettings[K]
  ) => setConfig((current) => ({ ...current, [field]: value }))
  const decision = config.api_format in decisionFormats
  const capability =
    catalog[config.model] ??
    (config.model.startsWith('gpt-6-astra-')
      ? catalog['gpt-6-astra']
      : undefined)
  const save = useMutation({
    mutationFn: () =>
      aiApi.saveModel(
        profile?.id,
        profile?.revision ?? 0,
        provider.id,
        decision
          ? {
              ...config,
              max_output_tokens: Math.min(config.max_output_tokens, 4096),
              reasoning_effort: null,
            }
          : config
      ),
    onSuccess: onSaved,
  })
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className='w-full gap-0 sm:max-w-lg'>
        <SheetHeader className='border-b'>
          <SheetTitle>{profile ? '编辑模型' : '添加模型'}</SheetTitle>
          <SheetDescription>
            {provider.config.name} · 使用该供应商的地址和密钥
          </SheetDescription>
        </SheetHeader>
        <form
          id='model-form'
          className='flex-1 space-y-8 overflow-y-auto p-4'
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          <FieldGroup title='模型'>
            <div className='space-y-2'>
              <Label htmlFor='model-name'>模型名</Label>
              <Input
                id='model-name'
                required
                list='model-catalog'
                value={config.model}
                onChange={(e) => {
                  const model = e.target.value
                  setConfig((current) => ({
                    ...current,
                    model,
                    max_output_tokens:
                      catalog[model]?.max_output_tokens ??
                      current.max_output_tokens,
                    reasoning_effort: null,
                  }))
                }}
                placeholder='和服务商文档里的模型 ID 一致'
              />
              <datalist id='model-catalog'>
                {Object.keys(catalog).map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            </div>
            <div className='space-y-2'>
              <Label htmlFor='model-label'>显示名称（可留空）</Label>
              <Input
                id='model-label'
                maxLength={60}
                value={config.label ?? ''}
                onChange={(e) => update('label', e.target.value || null)}
                placeholder={config.model || '默认显示模型名'}
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='model-format'>用途</Label>
              <select
                id='model-format'
                className={selectStyle}
                value={config.api_format}
                onChange={(e) =>
                  update(
                    'api_format',
                    e.target.value as ModelConfig['api_format']
                  )
                }
              >
                {formats(provider.config.kind).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <p className='text-xs text-muted-foreground'>
                {decision
                  ? '消息过滤模型只做分类，用在群问答里判断一条消息是不是在求助。'
                  : '文本生成模型可用于群日报、群问答回答、问题解析和答案核验。'}
              </p>
            </div>
            {capability && !decision && (
              <p className='rounded-lg bg-muted/50 p-3 text-sm'>
                上下文 {capability.context_window_tokens.toLocaleString()} Token
                · 最大输出 {capability.max_output_tokens.toLocaleString()} Token
              </p>
            )}
            <div className='flex items-center gap-3'>
              <Switch
                id='model-enabled'
                checked={config.enabled}
                onCheckedChange={(value) => update('enabled', value)}
              />
              <Label htmlFor='model-enabled'>允许功能选用这个模型</Label>
            </div>
          </FieldGroup>

          <Collapsible>
            <CollapsibleTrigger className='flex w-full items-center justify-between rounded-lg border px-3 py-2 text-sm font-medium [&[data-state=open]>svg]:rotate-180'>
              高级参数 <ChevronDown className='size-4' />
            </CollapsibleTrigger>
            <CollapsibleContent className='grid gap-4 pt-4 sm:grid-cols-2'>
              {!decision && (
                <>
                  <div className='space-y-2'>
                    <Label htmlFor='model-reasoning'>推理强度</Label>
                    <select
                      id='model-reasoning'
                      className={selectStyle}
                      value={config.reasoning_effort ?? ''}
                      onChange={(e) =>
                        update(
                          'reasoning_effort',
                          (e.target.value ||
                            null) as ModelSettings['reasoning_effort']
                        )
                      }
                    >
                      <option value=''>
                        服务商默认
                        {capability
                          ? `（${capability.default_reasoning_effort}）`
                          : ''}
                      </option>
                      {(capability?.reasoning_efforts ?? []).map((effort) => (
                        <option key={effort} value={effort}>
                          {effort === 'none' ? '关闭思考' : effort}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className='space-y-2'>
                    <Label htmlFor='model-output'>单次输出 Token 预算</Label>
                    <Input
                      id='model-output'
                      type='number'
                      min={128}
                      max={capability?.max_output_tokens ?? 1048576}
                      value={config.max_output_tokens}
                      onChange={(e) =>
                        update('max_output_tokens', Number(e.target.value))
                      }
                    />
                  </div>
                  <div className='space-y-2'>
                    <Label htmlFor='model-temperature'>
                      Temperature（可留空）
                    </Label>
                    <Input
                      id='model-temperature'
                      type='number'
                      min={0}
                      max={2}
                      step={0.1}
                      value={config.temperature ?? ''}
                      onChange={(e) =>
                        update(
                          'temperature',
                          e.target.value === '' ? null : Number(e.target.value)
                        )
                      }
                    />
                  </div>
                  <div className='space-y-2'>
                    <Label htmlFor='model-context'>
                      输入字符上限（可留空）
                    </Label>
                    <Input
                      id='model-context'
                      type='number'
                      min={4000}
                      value={config.context_chars ?? ''}
                      onChange={(e) =>
                        update(
                          'context_chars',
                          e.target.value === '' ? null : Number(e.target.value)
                        )
                      }
                    />
                  </div>
                  {provider.config.kind === 'compatible' && (
                    <div className='space-y-2'>
                      <Label htmlFor='model-token-field'>
                        Chat Completions Token 参数
                      </Label>
                      <select
                        id='model-token-field'
                        className={selectStyle}
                        value={config.token_parameter}
                        onChange={(e) =>
                          update(
                            'token_parameter',
                            e.target.value as ModelSettings['token_parameter']
                          )
                        }
                      >
                        <option>max_tokens</option>
                        <option>max_completion_tokens</option>
                      </select>
                    </div>
                  )}
                </>
              )}
              <div className='space-y-2'>
                <Label htmlFor='model-timeout'>超时（秒）</Label>
                <Input
                  id='model-timeout'
                  type='number'
                  min={10}
                  max={600}
                  value={config.timeout_seconds}
                  onChange={(e) =>
                    update('timeout_seconds', Number(e.target.value))
                  }
                />
              </div>
            </CollapsibleContent>
          </Collapsible>
        </form>
        <SheetFooter className='border-t'>
          <ErrorText error={save.error} />
          <div className='flex gap-2'>
            <Button type='submit' form='model-form' disabled={save.isPending}>
              {save.isPending ? '保存中…' : '保存'}
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
