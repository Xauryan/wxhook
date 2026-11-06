import { type ReactNode } from 'react'
import {
  AlertCircle,
  ArrowRight,
  BadgeCheck,
  Inbox,
  LoaderCircle,
} from 'lucide-react'
import { assetUrl, type Profile } from '../lib/api'
import { profileName as getProfileName, safeText } from '../lib/format'

export function Card({
  children,
  className = '',
  title,
  action,
}: {
  children: ReactNode
  className?: string
  title?: ReactNode
  action?: ReactNode
}) {
  return (
    <section className={`card ${className}`.trim()}>
      {(title || action) && (
        <div className='card-header'>
          <div className='card-title'>{title}</div>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

export function SectionLink({
  children = '查看全部',
  onClick,
}: {
  children?: ReactNode
  onClick?: () => void
}) {
  return (
    <button className='section-link' type='button' onClick={onClick}>
      {children} <ArrowRight size={14} />
    </button>
  )
}

export function Avatar({
  profile,
  src,
  name,
  size = 40,
  className = '',
}: {
  profile?: Partial<Profile>
  src?: string
  name?: string
  size?: number
  className?: string
}) {
  const resolvedName = name || (profile ? getProfileName(profile) : '本地账号')
  const resolvedSrc = assetUrl(src || profile?.avatar_url)
  const initials = [...resolvedName.trim()].slice(0, 2).join('') || '本'
  return resolvedSrc ? (
    <img
      className={`avatar ${className}`}
      src={resolvedSrc}
      alt={`${resolvedName}的头像`}
      width={size}
      height={size}
      loading='lazy'
      onError={(event) => {
        event.currentTarget.style.visibility = 'hidden'
      }}
    />
  ) : (
    <span
      className={`avatar avatar-fallback ${className}`}
      style={{ width: size, height: size, fontSize: Math.max(11, size * 0.32) }}
      aria-label={`${resolvedName}的占位头像`}
    >
      {initials}
    </span>
  )
}

export type ProfileMetric = {
  label: string
  value: ReactNode
  suffix?: string
  note?: string
  icon?: ReactNode
}

export function ProfileBanner({
  profile,
  subtitle,
  metrics,
  tags = ['联系人'],
  action,
  compact = false,
}: {
  profile?: Partial<Profile>
  subtitle: string
  metrics: ProfileMetric[]
  tags?: string[]
  action?: ReactNode
  compact?: boolean
}) {
  return (
    <Card className={`profile-banner${compact ? 'compact' : ''}`}>
      <div className='profile-identity'>
        <Avatar profile={profile} size={compact ? 74 : 108} />
        <div className='profile-copy'>
          <div className='profile-name'>
            {getProfileName(profile)}{' '}
            <BadgeCheck size={18} fill='currentColor' />
          </div>
          <div className='profile-subtitle'>{subtitle}</div>
          <div className='profile-tags'>
            {tags.map((tag, index) => (
              <span key={tag} className={index === 0 ? 'green' : ''}>
                {tag}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className='profile-metrics'>
        {metrics.map((metric) => (
          <div className='profile-metric' key={metric.label}>
            <div className='metric-heading'>
              {metric.icon}
              <span>{metric.label}</span>
            </div>
            <div className='metric-number'>
              {metric.value} {metric.suffix && <small>{metric.suffix}</small>}
            </div>
            {metric.note && <div className='metric-note'>{metric.note}</div>}
          </div>
        ))}
      </div>
      {action && <div className='profile-action'>{action}</div>}
    </Card>
  )
}

export function LoadingState({
  label = '正在读取本地数据库',
}: {
  label?: string
}) {
  return (
    <div className='state-block' role='status'>
      <LoaderCircle className='spin' size={22} />
      <span>{label}</span>
    </div>
  )
}

export function ErrorState({
  error,
  retry,
}: {
  error: unknown
  retry?: () => void
}) {
  const message = error instanceof Error ? error.message : '数据读取失败'
  return (
    <div className='state-block error' role='alert'>
      <AlertCircle size={21} />
      <span>{message}</span>
      {retry && (
        <button type='button' onClick={retry}>
          重试
        </button>
      )}
    </div>
  )
}

export function EmptyState({
  title = '暂无可展示的数据',
  detail,
}: {
  title?: string
  detail?: string
}) {
  return (
    <div className='state-block empty'>
      <Inbox size={22} />
      <div>
        <strong>{title}</strong>
        {detail && <p>{detail}</p>}
      </div>
    </div>
  )
}

export function DataValue({
  value,
  suffix,
}: {
  value: unknown
  suffix?: string
}) {
  return (
    <>
      {safeText(value)}
      {suffix ? <small>{suffix}</small> : null}
    </>
  )
}

export function Pill({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'neutral' | 'green' | 'orange' | 'blue'
}) {
  return <span className={`pill ${tone}`}>{children}</span>
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className='skeleton-list' aria-hidden='true'>
      {Array.from({ length: rows }, (_, index) => (
        <span key={index} style={{ width: `${92 - index * 7}%` }} />
      ))}
    </div>
  )
}
