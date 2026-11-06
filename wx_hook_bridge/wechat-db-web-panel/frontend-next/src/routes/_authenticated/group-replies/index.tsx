import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authenticated/group-replies/')({
  beforeLoad: () => {
    throw redirect({ to: '/group-assistant', search: { tab: 'replies' } })
  },
})
