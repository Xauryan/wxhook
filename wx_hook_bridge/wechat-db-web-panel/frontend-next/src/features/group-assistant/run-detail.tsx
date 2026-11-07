import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { replyApi } from '@/features/group-replies/api'
import { ErrorText } from './shared'
import { reasonText } from './utils'

const sourceKinds: Record<string, string> = {
  official: '官方来源',
  community_wiki: '社区资料站',
}

export function RunDetailSheet({
  id,
  onClose,
}: {
  id: string
  onClose: () => void
}) {
  return (
    <Sheet open={!!id} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className='w-full gap-0 sm:max-w-2xl'>
        <SheetHeader className='border-b'>
          <SheetTitle>处理详情</SheetTitle>
          <SheetDescription>
            模型实际读到的前文、判断过程和引用的资料。
          </SheetDescription>
        </SheetHeader>
        <div className='flex-1 overflow-y-auto p-4'>
          {id && <RunDetail id={id} />}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function Step({ label, children }: { label: string; children: ReactNode }) {
  return (
    <li className='grid grid-cols-[5rem_1fr] gap-3 text-sm'>
      <span className='text-muted-foreground'>{label}</span>
      <div className='min-w-0 break-words'>{children}</div>
    </li>
  )
}

function RunDetail({ id }: { id: string }) {
  const query = useQuery({
    queryKey: ['reply-run', id],
    queryFn: () => replyApi.run(id),
    refetchInterval: 3000,
  })
  if (query.error) return <ErrorText error={query.error} />
  if (!query.data)
    return <p className='text-sm text-muted-foreground'>加载中…</p>
  const data = query.data
  const result = data.result
  return (
    <div className='space-y-6'>
      <section className='space-y-3'>
        <div className='flex flex-wrap items-center gap-2'>
          {result ? (
            <Badge variant={result.should_reply ? 'default' : 'secondary'}>
              {result.should_reply ? '回答' : '不回答'}
            </Badge>
          ) : (
            <Badge variant='outline'>处理中</Badge>
          )}
          {result && (
            <span className='text-sm'>{reasonText(result.reason)}</span>
          )}
          {data.delivery_status && (
            <span className='text-sm text-muted-foreground'>
              · {data.delivery_status}
            </span>
          )}
        </div>
        <blockquote className='rounded-lg bg-muted/50 p-3 text-sm whitespace-pre-wrap'>
          {data.context.current.text}
        </blockquote>
        {result?.answer && (
          <div className='rounded-lg border p-4 text-sm break-words whitespace-pre-wrap'>
            {result.answer}
          </div>
        )}
        {result?.detail && (
          <p className='text-xs text-muted-foreground'>{result.detail}</p>
        )}
      </section>

      {result && (
        <section className='space-y-3'>
          <h3 className='font-medium'>判断过程</h3>
          <ol className='space-y-2'>
            {result.decision && (
              <Step label='消息过滤'>
                {result.decision.model
                  ? `${result.decision.model} · ${result.decision.choice}`
                  : '未使用决策模型'}
                {result.decision.attempts.map((attempt, index) => (
                  <p key={index} className='text-xs text-muted-foreground'>
                    {attempt.profile_id} ·{' '}
                    {attempt.status === 'completed' ? '完成' : '故障'} ·{' '}
                    {attempt.elapsed_ms} ms {attempt.detail}
                  </p>
                ))}
              </Step>
            )}
            {result.images?.map((image) => (
              <Step key={image.message_id} label='看截图'>
                {image.text}
              </Step>
            ))}
            {result.plan && (
              <Step label='识别问题'>
                {result.plan.question || result.plan.reason}
              </Step>
            )}
            {result.faq && (
              <Step label='常见问题'>
                复用之前的回答：{result.faq.question}
              </Step>
            )}
            {result.searches?.length > 0 && (
              <Step label='检索'>
                {result.searches.map((search, index) => (
                  <p key={index}>
                    {search.provider} · {search.query} · {search.results} 条
                  </p>
                ))}
              </Step>
            )}
            {result.review && <Step label='核验'>{result.review.reason}</Step>}
            {result.attempts?.some(
              (attempt) => attempt.status === 'failed'
            ) && (
              <Step label='换用模型'>
                {result.attempts.map((attempt, index) => (
                  <p
                    key={index}
                    className={
                      attempt.status === 'failed'
                        ? 'text-destructive'
                        : 'text-muted-foreground'
                    }
                  >
                    {attempt.stage} · {attempt.profile_id} ·{' '}
                    {attempt.status === 'completed'
                      ? '完成'
                      : `失败 ${attempt.detail ?? ''}`}
                  </p>
                ))}
              </Step>
            )}
            {result.retrieval_errors?.length > 0 && (
              <Step label='检索错误'>
                {result.retrieval_errors.map((error, index) => (
                  <p key={index} className='text-destructive'>
                    {error}
                  </p>
                ))}
              </Step>
            )}
          </ol>
        </section>
      )}

      {result?.evidence && result.evidence.length > 0 && (
        <section className='space-y-2'>
          <h3 className='font-medium'>引用资料（{result.evidence.length}）</h3>
          {result.evidence.map((source) => (
            <details key={source.id} className='rounded-lg border p-3'>
              <summary className='cursor-pointer text-sm'>
                {source.id} · {source.title} ·{' '}
                {sourceKinds[source.source_kind] ?? '网页来源'}
              </summary>
              <a
                className='mt-2 block text-xs break-all underline'
                href={source.url}
                target='_blank'
                rel='noreferrer'
              >
                {source.url}
              </a>
              {source.published_at && (
                <p className='text-xs text-muted-foreground'>
                  {source.published_at}
                </p>
              )}
              <pre className='mt-3 text-xs break-words whitespace-pre-wrap'>
                {source.text}
              </pre>
            </details>
          ))}
        </section>
      )}

      <details className='rounded-lg border p-3'>
        <summary className='cursor-pointer text-sm font-medium'>
          模型读到的前文（{data.context.previous.length} 条）
        </summary>
        <div className='mt-3 max-h-96 space-y-2 overflow-auto'>
          {[...data.context.previous, data.context.current].map((message) => {
            const current =
              message.message_id === data.context.current.message_id
            return (
              <div
                key={message.message_id}
                className={`rounded p-2 text-sm ${current ? 'border bg-muted/40' : ''}`}
              >
                <span className='text-xs text-muted-foreground'>
                  {message.time} · {message.speaker}
                  {current ? ' · 当前问题' : ''}
                </span>
                <p className='whitespace-pre-wrap'>{message.text}</p>
                {message.quote && (
                  <p className='mt-1 border-l-2 pl-2 text-xs text-muted-foreground'>
                    引用 {message.quote.text}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      </details>

      <details className='rounded-lg border p-3'>
        <summary className='cursor-pointer text-sm font-medium'>
          当次配置与版本
        </summary>
        <p className='mt-2 text-xs text-muted-foreground'>
          消息时间 {data.context.at} · 模型配置版本 {data.model_revision} ·
          上下文指纹 <code className='break-all'>{data.context_hash}</code>
        </p>
        <pre className='mt-3 max-h-96 overflow-auto text-xs whitespace-pre-wrap'>
          {JSON.stringify(
            { config: data.config, profile_revisions: data.profile_revisions },
            null,
            2
          )}
        </pre>
      </details>
    </div>
  )
}
