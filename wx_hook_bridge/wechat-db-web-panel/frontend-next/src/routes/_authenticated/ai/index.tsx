import { createFileRoute } from '@tanstack/react-router'
import { AIPage } from '@/features/ai'

export const Route = createFileRoute('/_authenticated/ai/')({
  component: AIPage,
})
