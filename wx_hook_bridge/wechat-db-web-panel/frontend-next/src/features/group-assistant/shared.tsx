import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function Panel({
  title,
  description,
  action,
  children,
  className,
}: {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  children?: ReactNode
  className?: string
}) {
  return (
    <section className={cn('space-y-4 rounded-xl border p-5', className)}>
      {(title || action) && (
        <div className='flex flex-wrap items-start justify-between gap-3'>
          <div>
            {title && <h3 className='font-semibold'>{title}</h3>}
            {description && (
              <p className='mt-1 text-sm text-muted-foreground'>
                {description}
              </p>
            )}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className='flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-12 text-center'>
      <p className='font-medium'>{title}</p>
      {description && (
        <p className='max-w-md text-sm text-muted-foreground'>{description}</p>
      )}
      {action}
    </div>
  )
}

export function FieldGroup({
  title,
  description,
  children,
}: {
  title: string
  description?: ReactNode
  children: ReactNode
}) {
  return (
    <fieldset className='space-y-4'>
      <div>
        <legend className='font-medium'>{title}</legend>
        {description && (
          <p className='mt-1 text-sm text-muted-foreground'>{description}</p>
        )}
      </div>
      {children}
    </fieldset>
  )
}

export function ErrorText({ error }: { error?: Error | string | null }) {
  if (!error) return null
  return (
    <p role='alert' className='text-sm text-destructive'>
      {typeof error === 'string' ? error : error.message}
    </p>
  )
}

export function ChipToggle({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (value: boolean) => void
  children: ReactNode
}) {
  return (
    <button
      type='button'
      role='checkbox'
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        'rounded-full border px-3 py-1 text-sm transition-colors',
        checked
          ? 'border-primary bg-primary text-primary-foreground'
          : 'hover:bg-muted'
      )}
    >
      {children}
    </button>
  )
}
