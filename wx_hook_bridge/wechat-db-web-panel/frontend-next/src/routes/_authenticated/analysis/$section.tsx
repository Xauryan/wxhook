import { lazy, Suspense } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { DataAnalysis } from '@/features/data-analysis'
import { RelationshipAnalysis } from '@/features/relationship-analysis'

const ArchiveWorkspace = lazy(() => import('@/features/archive'))

export const Route = createFileRoute('/_authenticated/analysis/$section')({
  validateSearch: (search: Record<string, unknown>) =>
    Object.fromEntries(
      Object.entries(search).filter(
        ([, value]) => typeof value === 'string' || typeof value === 'number'
      )
    ) as Record<string, string | number>,
  component: AnalysisRoute,
})

function AnalysisRoute() {
  const { section } = Route.useParams()
  if (['reports', 'exports', 'notifications'].includes(section))
    return (
      <Suspense fallback={<p className='p-8'>正在加载…</p>}>
        <ArchiveWorkspace section={section} />
      </Suspense>
    )
  if (section === 'relationships') return <RelationshipAnalysis />
  return <DataAnalysis section={section} />
}
