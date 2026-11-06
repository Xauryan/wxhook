import z from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { GroupAssistantPage } from '@/features/group-assistant'

const searchSchema = z.object({
  room: z.string().optional().catch(undefined),
  tab: z.enum(['reports', 'replies', 'replays']).optional().catch(undefined),
})

export const Route = createFileRoute('/_authenticated/group-assistant/')({
  validateSearch: searchSchema,
  component: GroupAssistantPage,
})
