import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { PromptKey, ReplyConfig, RetrievalConfig } from './api'

const retrievalDefaults: RetrievalConfig = {
  search_queries: 2,
  deepseek_count: 6,
  max_documents: 5,
  evidence_limit: 10,
  chunk_chars: 1100,
  chunks_per_document: 3,
  search_cache_seconds: 900,
  time_budget_seconds: 40,
  page_cache_seconds: 1800,
  allow_unverified_sources: false,
}
// Same default as the backend; checked against 129 real questions from the group.
const questionPattern =
  '[？?]|吗|么|呢|啥|什么|怎么|怎样|如何|为什么|为啥|几|多少|哪|谁|是不是|能不能|可不可以|会不会|' +
  '有没有|区别|意思|咋|想问|请问|问下|问一下|求助|求教|帮忙|帮看|有人知道|谁知道'
const promptLabels: Record<PromptKey, string> = {
  decision_prompt: '决策分类提示词',
  plan_prompt: '问题解析与检索规划提示词',
  answer_prompt: '回答提示词',
  review_prompt: '答案核验提示词',
  search_prompt: 'GPT 网页搜索提示词',
}

export function PipelineSettings({
  config,
  prompts,
  onChange,
}: {
  config: ReplyConfig
  prompts: Record<PromptKey, string>
  onChange: (config: ReplyConfig) => void
}) {
  const update = <K extends keyof ReplyConfig>(key: K, value: ReplyConfig[K]) =>
    onChange({ ...config, [key]: value })
  const retrieval = config.retrieval ?? retrievalDefaults
  return (
    <div className='space-y-4'>
      <details className='rounded-lg border p-4'>
        <summary className='cursor-pointer font-medium'>本地初筛</summary>
        <div className='mt-4 space-y-4'>
          <div className='max-w-xs space-y-2'>
            <Label htmlFor='reply-min-text'>最少文本字符</Label>
            <Input
              id='reply-min-text'
              type='number'
              min={1}
              max={100}
              value={config.local_min_text_chars ?? 1}
              onChange={(event) =>
                update('local_min_text_chars', Number(event.target.value))
              }
            />
          </div>
          <label className='flex items-center gap-2 text-sm'>
            <input
              type='checkbox'
              checked={config.local_require_question_signal ?? true}
              onChange={(event) =>
                update('local_require_question_signal', event.target.checked)
              }
            />
            普通消息要含疑问词才继续（关闭后，不含疑问词的也交给过滤模型判断）
          </label>
          {config.local_require_question_signal && (
            <div className='space-y-2'>
              <Label htmlFor='reply-question-pattern'>疑问信号正则表达式</Label>
              <Input
                id='reply-question-pattern'
                value={config.local_question_pattern ?? questionPattern}
                onChange={(event) =>
                  update('local_question_pattern', event.target.value)
                }
              />
            </div>
          )}
        </div>
      </details>
      <details className='rounded-lg border p-4'>
        <summary className='cursor-pointer font-medium'>
          上下文与回复设置
        </summary>
        <div className='mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3'>
          {(
            [
              ['context_messages', '前文最多条数', 4, 60, 1, 24],
              ['context_minutes', '前文时间范围（分钟）', 1, 60, 1, 15],
              [
                'context_message_chars',
                '每条消息字符上限',
                100,
                50000,
                1,
                1600,
              ],
              ['context_quote_chars', '引用消息字符上限', 100, 50000, 1, 1200],
              ['cooldown_seconds', '主动回复间隔（秒）', 0, 600, 1, 60],
              [
                'processing_timeout_seconds',
                '问答流程超时（秒）',
                30,
                3600,
                1,
                360,
              ],
              ['live_expiry_seconds', '实时消息有效期（秒）', 30, 3600, 1, 300],
              ['confidence_threshold', '答案核验阈值', 0.8, 1, 0.01, 0.9],
              ['max_reply_chars', '回复正文字符上限', 200, 2400, 1, 1200],
            ] as const
          ).map(([key, label, min, max, step, fallback]) => (
            <div key={key} className='space-y-2'>
              <Label htmlFor={`reply-${key}`}>{label}</Label>
              <Input
                id={`reply-${key}`}
                type='number'
                min={min}
                max={max}
                step={step}
                value={config[key] ?? fallback}
                onChange={(event) => update(key, Number(event.target.value))}
              />
            </div>
          ))}
        </div>
        <label className='mt-4 flex items-center gap-2 text-sm'>
          <input
            type='checkbox'
            checked={config.exclude_same_second ?? true}
            onChange={(event) =>
              update('exclude_same_second', event.target.checked)
            }
          />
          排除与触发消息同秒的前文
        </label>
      </details>
      <details className='rounded-lg border p-4'>
        <summary className='cursor-pointer font-medium'>检索与证据设置</summary>
        <div className='mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3'>
          {(
            [
              ['search_queries', '搜索词最多条数', 1, 10],
              ['deepseek_count', '每次 DeepSeek 搜索结果数', 1, 50],
              ['max_documents', '读取正文最多篇数', 1, 20],
              ['evidence_limit', '送入模型的证据段数', 1, 40],
              ['chunk_chars', '每段证据字符数', 300, 10000],
              ['chunks_per_document', '每篇正文最多选段数', 1, 10],
              ['time_budget_seconds', '检索总时限（秒）', 5, 300],
              ['search_cache_seconds', '搜索缓存（秒，0 为关闭）', 0, 86400],
              ['page_cache_seconds', '网页正文缓存（秒）', 0, 86400],
            ] as const
          ).map(([key, label, min, max]) => (
            <div key={key} className='space-y-2'>
              <Label htmlFor={`retrieval-${key}`}>{label}</Label>
              <Input
                id={`retrieval-${key}`}
                type='number'
                min={min}
                max={max}
                value={retrieval[key]}
                onChange={(event) =>
                  update('retrieval', {
                    ...retrieval,
                    [key]: Number(event.target.value),
                  })
                }
              />
            </div>
          ))}
        </div>
        <label className='mt-4 flex items-center gap-2 text-sm'>
          <input
            type='checkbox'
            checked={retrieval.allow_unverified_sources}
            onChange={(event) =>
              update('retrieval', {
                ...retrieval,
                allow_unverified_sources: event.target.checked,
              })
            }
          />
          允许读取未验证权威性的来源
        </label>
      </details>
      <details className='rounded-lg border p-4'>
        <summary className='cursor-pointer font-medium'>各阶段提示词</summary>
        <p className='mt-3 text-sm text-muted-foreground'>
          保留各阶段要求的 JSON 字段。GPT 搜索提示词支持 {'{question}'} 和{' '}
          {'{at}'}。任务会保存生效的提示词与参数。
        </p>
        <div className='mt-4 space-y-5'>
          {(Object.entries(promptLabels) as [PromptKey, string][]).map(
            ([key, label]) => (
              <div key={key} className='space-y-2'>
                <div className='flex items-center justify-between gap-3'>
                  <Label htmlFor={`reply-${key}`}>{label}</Label>
                  <Button
                    type='button'
                    size='sm'
                    variant='outline'
                    onClick={() => update(key, null)}
                  >
                    恢复默认
                  </Button>
                </div>
                <textarea
                  id={`reply-${key}`}
                  rows={key === 'search_prompt' ? 4 : 10}
                  maxLength={20000}
                  className='w-full rounded-md border bg-background p-3 text-sm leading-relaxed'
                  value={config[key] ?? prompts[key] ?? ''}
                  onChange={(event) => update(key, event.target.value)}
                />
              </div>
            )
          )}
        </div>
      </details>
    </div>
  )
}
