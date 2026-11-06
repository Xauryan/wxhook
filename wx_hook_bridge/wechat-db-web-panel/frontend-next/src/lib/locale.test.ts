import { describe, expect, it } from 'vitest'
import { sidebarData } from '@/components/layout/data/sidebar-data'
import { apps } from '@/features/apps/data/apps'
import { statuses } from '@/features/tasks/data/data'
import { roles } from '@/features/users/data/data'
import { UI_LOCALE, uiLabel } from './locale'

describe('Chinese UI metadata', () => {
  it('localizes navigation while preserving route addresses', () => {
    const items = sidebarData.navGroups.flatMap((group) => group.items)
    expect(items.find((item) => item.url === '/chats')?.title).toBe('微信聊天')
    expect(items.find((item) => item.url === '/tasks')?.title).toBe('任务管理')
    expect(items.find((item) => item.url === '/apps')?.title).toBe('自动化')
    expect(UI_LOCALE).toBe('zh-CN')
  })
  it('keeps machine-readable status and role values unchanged', () => {
    expect(statuses.find((item) => item.value === 'in progress')?.label).toBe(
      '进行中'
    )
    expect(roles.find((item) => item.value === 'superadmin')?.label).toBe(
      '超级管理员'
    )
    expect(uiLabel('active')).toBe('已启用')
    expect(uiLabel('fullName')).toBe('姓名')
  })
  it('preserves product names and unknown text verbatim', () => {
    expect(apps.some((item) => item.name === 'Medium')).toBe(true)
    expect(uiLabel('Alice <test>\noriginal text')).toBe(
      'Alice <test>\noriginal text'
    )
  })
})
