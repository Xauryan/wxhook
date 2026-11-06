import { type QueryClient } from '@tanstack/react-query'
import {
  createRootRouteWithContext,
  Outlet,
  redirect,
} from '@tanstack/react-router'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import { TanStackRouterDevtools } from '@tanstack/react-router-devtools'
import { Toaster } from '@/components/ui/sonner'
import { NavigationProgress } from '@/components/navigation-progress'
import { GeneralError } from '@/features/errors/general-error'
import { NotFoundError } from '@/features/errors/not-found-error'

export const Route = createRootRouteWithContext<{
  queryClient: QueryClient
}>()({
  beforeLoad: ({ location }) => {
    const path = location.pathname.replace(/\/+$/, '') || '/'
    const retiredPages = [
      '/users',
      '/clerk',
      '/sign-in',
      '/sign-in-2',
      '/sign-up',
      '/forgot-password',
      '/otp',
      '/help-center',
      '/errors',
    ]
    if (
      retiredPages.some((page) => path === page || path.startsWith(page + '/'))
    ) {
      throw redirect({ to: '/chats', replace: true })
    }
    if (
      [
        '/settings',
        '/settings/account',
        '/settings/notifications',
        '/settings/display',
      ].includes(path)
    ) {
      throw redirect({ to: '/settings/appearance', replace: true })
    }
  },
  component: () => {
    return (
      <>
        <NavigationProgress />
        <Outlet />
        <Toaster duration={5000} />
        {import.meta.env.MODE === 'development' && (
          <>
            <ReactQueryDevtools buttonPosition='bottom-left' />
            <TanStackRouterDevtools position='bottom-right' />
          </>
        )}
      </>
    )
  },
  notFoundComponent: NotFoundError,
  errorComponent: GeneralError,
})
