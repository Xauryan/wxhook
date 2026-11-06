import { Outlet } from '@tanstack/react-router'
import { Separator } from '@/components/ui/separator'
import { ConfigDrawer } from '@/components/config-drawer'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'

export function Settings() {
  return (
    <>
      <Header>
        <Search className='me-auto' />
        <ThemeSwitch />
        <ConfigDrawer />
      </Header>
      <Main fixed>
        <div className='space-y-0.5'>
          <h1 className='text-2xl font-bold tracking-tight md:text-3xl'>
            外观设置
          </h1>
          <p className='text-muted-foreground'>
            设置工作台的主题与字体。布局可通过右上角的外观设置调整。
          </p>
        </div>
        <Separator className='my-4 lg:my-6' />
        <div className='flex w-full overflow-y-auto p-1'>
          <Outlet />
        </div>
      </Main>
    </>
  )
}
