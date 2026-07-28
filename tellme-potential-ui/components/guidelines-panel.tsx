'use client'

import { useState } from 'react'
import { Copy, Check, ListChecks, Code2, Terminal } from 'lucide-react'
import type { Guideline, GuidelineSeverity } from '@/lib/api/types'
import { cn } from '@/lib/utils'

const SEVERITY_STYLES: Record<GuidelineSeverity, string> = {
  required: 'border-primary/30 bg-accent text-accent-foreground',
  recommended: 'border-border bg-secondary text-secondary-foreground',
  optional: 'border-border bg-background text-muted-foreground',
}

export function GuidelinesPanel({
  guidelines,
  query,
}: {
  guidelines: Guideline[]
  query: string | null
}) {
  const [copied, setCopied] = useState(false)

  if (guidelines.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-card/50 px-6 py-16 text-center">
        <span className="flex size-10 items-center justify-center rounded-xl bg-secondary text-muted-foreground">
          <ListChecks className="size-5" aria-hidden="true" />
        </span>
        <div className="max-w-sm">
          <p className="text-sm font-medium">No guidelines yet</p>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Ask a question in the conversation view. Each answer synthesizes a
            machine-readable guideline set for your downstream software to
            consume.
          </p>
        </div>
      </div>
    )
  }

  async function copyJson() {
    const payload = JSON.stringify({ query, guidelines }, null, 2)
    try {
      await navigator.clipboard.writeText(payload)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      // clipboard unavailable
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3 rounded-2xl border border-border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Code2 className="size-4" aria-hidden="true" />
          </span>
          <div>
            <p className="text-sm font-semibold">Downstream guidelines</p>
            <p className="text-[13px] text-muted-foreground">
              Machine-readable rules generated from the latest query. Consumed
              by backend services, not shown to end users.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={copyJson}
          className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 sm:self-auto"
        >
          {copied ? (
            <Check className="size-3.5" aria-hidden="true" />
          ) : (
            <Copy className="size-3.5" aria-hidden="true" />
          )}
          {copied ? 'Copied' : 'Copy as JSON'}
        </button>
      </div>

      <ol className="flex flex-col gap-3">
        {guidelines.map((g) => (
          <li
            key={g.id}
            className="rounded-xl border border-border bg-card p-4 transition-shadow hover:shadow-sm"
          >
            <div className="flex items-start gap-3">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-secondary font-mono text-xs font-semibold tabular-nums text-foreground">
                {g.index}
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="text-sm font-semibold">{g.title}</h4>
                  <span
                    className={cn(
                      'rounded-full border px-2 py-0.5 text-[10px] font-medium capitalize',
                      SEVERITY_STYLES[g.severity],
                    )}
                  >
                    {g.severity}
                  </span>
                  <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                    {g.category}
                  </span>
                </div>
                <p className="text-[13px] leading-relaxed text-muted-foreground">
                  {g.detail}
                </p>
              </div>
            </div>
          </li>
        ))}
      </ol>

      <div className="flex items-center gap-2 rounded-xl border border-border bg-secondary/50 px-3 py-2 text-[11px] text-muted-foreground">
        <Terminal className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="font-mono">
          POST /v1/guidelines · {guidelines.length} rules · ready for ingestion
        </span>
      </div>
    </div>
  )
}
