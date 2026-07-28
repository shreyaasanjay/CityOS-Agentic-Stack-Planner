import { ArrowUpRight, Check, Circle, Loader2, Search, Server, Square } from 'lucide-react'

import type { QueryProgressStage } from '@/lib/api/types'

const STEPS = [
  { id: 'planning', label: 'Understanding request' },
  { id: 'verifying', label: 'Verifying workflow' },
  { id: 'synthesizing', label: 'Preparing data access' },
  { id: 'answering', label: 'Generating answer' },
] satisfies { id: QueryProgressStage; label: string }[]

export function ThinkingIndicator({
  stage,
  backendRunId,
  onStop,
}: {
  stage: QueryProgressStage
  backendRunId?: string
  onStop?: () => void
}) {
  const activeIndex = Math.max(0, STEPS.findIndex((step) => step.id === stage))
  const backendHref = backendRunId
    ? `/api/tellme/backend?run=${encodeURIComponent(backendRunId)}`
    : '/api/tellme/backend'

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-border bg-card px-4 py-3.5 text-sm text-muted-foreground shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3 text-foreground">
          <span className="flex size-6 items-center justify-center rounded-md bg-accent text-accent-foreground">
            <Search className="size-3.5" aria-hidden="true" />
          </span>
          <span className="font-medium">Preparing grounded answer</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={backendHref}
            target="_blank"
            rel="noreferrer"
            title="Open TraceFix runner and intent decomposition"
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
          >
            <Server className="size-3" aria-hidden="true" />
            {backendRunId ? 'TraceFix run' : 'TraceFix'}
            <ArrowUpRight className="size-3" aria-hidden="true" />
          </a>
          {onStop && (
            <button
              type="button"
              onClick={onStop}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              <Square className="size-3" aria-hidden="true" />
              Stop
            </button>
          )}
        </div>
      </div>

      <ol className="grid gap-2 text-[13px] sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((step, index) => {
          const complete = index < activeIndex
          const active = index === activeIndex
          return (
            <li
              key={step.id}
              className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2"
            >
              {complete ? (
                <span className="flex size-5 items-center justify-center rounded-full bg-accent text-accent-foreground">
                  <Check className="size-3" aria-hidden="true" />
                </span>
              ) : active ? (
                <span className="flex size-5 items-center justify-center rounded-full bg-secondary text-muted-foreground">
                  <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                </span>
              ) : (
                <span className="flex size-5 items-center justify-center text-muted-foreground/50">
                  <Circle className="size-3" aria-hidden="true" />
                </span>
              )}
              <span className={complete || active ? 'text-foreground' : 'text-muted-foreground'}>
                {step.label}
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}