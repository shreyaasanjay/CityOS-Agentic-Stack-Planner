'use client'

import { useState } from 'react'
import {
  Sparkles,
  ShieldCheck,
  Check,
  ArrowUpRight,
  MessageSquareText,
  Cpu,
  FileSearch,
  Camera,
  RadioTower,
  Server,
  Copy,
  RotateCcw,
  Pencil,
  ThumbsUp,
  ThumbsDown,
  Share2,
  Download,
  Timer,
  Database,
} from 'lucide-react'
import type { Agent, QueryResult } from '@/lib/api/types'
import { EvidenceCard } from '@/components/evidence-card'
import { MarkdownRenderer } from '@/components/markdown-renderer'
import { cn } from '@/lib/utils'

type ResultTab = 'answer' | 'agents' | 'evidence'
type Feedback = 'helpful' | 'incorrect'

function confidenceLabel(value: number | null) {
  if (value === null) return 'Confidence not reported'
  if (value >= 0.85) return 'High confidence'
  if (value >= 0.6) return 'Moderate confidence'
  return 'Low confidence'
}

function agentIcon(type: string) {
  const t = type.toLowerCase()
  if (t.includes('camera')) return Camera
  if (t.includes('motion') || t.includes('sensor')) return RadioTower
  return Server
}

export function ResultView({
  result,
  visibleAnswer,
  isStreaming,
  isStopped,
  copied,
  shared,
  feedback,
  responseMs,
  tracefixRunId,
  onViewGuidelines,
  onCopy,
  onShare,
  onExport,
  onFeedback,
  onRegenerate,
  onEditPrompt,
}: {
  result: QueryResult
  visibleAnswer?: string
  isStreaming?: boolean
  isStopped?: boolean
  copied?: boolean
  shared?: boolean
  feedback?: Feedback
  responseMs?: number
  tracefixRunId?: string
  onViewGuidelines: () => void
  onCopy?: () => void
  onShare?: () => void
  onExport?: () => void
  onFeedback?: (feedback: Feedback) => void
  onRegenerate?: () => void
  onEditPrompt?: () => void
}) {
  const [tab, setTab] = useState<ResultTab>('answer')
  const pct = result.confidence === null ? null : Math.round(result.confidence * 100)
  const answer = visibleAnswer ?? result.answer

  const tabs: { id: ResultTab; label: string; icon: typeof Sparkles; count?: number }[] = [
    { id: 'answer', label: 'Answer', icon: MessageSquareText },
    { id: 'agents', label: 'Sensors used', icon: Cpu, count: result.agents.length },
    { id: 'evidence', label: 'Privacy receipt', icon: FileSearch, count: result.evidence.length },
  ]

  return (
    <section
      aria-label="Answer"
      className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm"
    >
      <div className="flex items-center gap-1 border-b border-border bg-secondary/50 px-2 py-2 sm:px-3">
        {tabs.map(({ id, label, icon: Icon, count }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-current={tab === id ? 'true' : undefined}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors sm:px-3',
              tab === id
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-background hover:text-foreground',
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            {label}
            {typeof count === 'number' && count > 0 && (
              <span
                className={cn(
                  'ml-0.5 inline-flex min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums',
                  tab === id
                    ? 'bg-primary-foreground/20 text-primary-foreground'
                    : 'bg-secondary text-muted-foreground',
                )}
              >
                {count}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="p-5 sm:p-6">
        {tab === 'answer' && (
          <div className="flex flex-col gap-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-2 text-sm font-medium">
                <span className="flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
                  <Sparkles className="size-3.5" aria-hidden="true" />
                </span>
                Grounded answer
                {isStreaming && (
                  <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                    Streaming
                  </span>
                )}
                {isStopped && (
                  <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                    Stopped
                  </span>
                )}
              </div>
              <span
                className={cn(
                  'inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium',
                  result.confidence !== null && result.confidence >= 0.85
                    ? 'border-primary/30 bg-accent text-accent-foreground'
                    : 'border-border bg-background text-muted-foreground',
                )}
              >
                <ShieldCheck className="size-3.5" aria-hidden="true" />
                {confidenceLabel(result.confidence)}{pct === null ? '' : ` - ${pct}%`}
              </span>
            </div>

            <TrustIndicators result={result} responseMs={responseMs} />

            <MarkdownRenderer
              content={answer}
              className="text-lg text-foreground sm:text-xl"
            />

            {result.keyPoints.length > 0 && !isStreaming && (
              <ul className="flex flex-col gap-2 border-t border-border pt-4">
                {result.keyPoints.map((point) => (
                  <li key={point} className="flex items-start gap-2.5 text-sm">
                    <span className="mt-0.5 flex size-4.5 shrink-0 items-center justify-center rounded-full bg-accent text-accent-foreground">
                      <Check className="size-3" aria-hidden="true" />
                    </span>
                    <span className="leading-relaxed text-muted-foreground">
                      {point}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            <div className="flex flex-wrap items-center gap-2">
              {onFeedback && (
                <>
                  <ResponseButton
                    active={feedback === 'helpful'}
                    onClick={() => onFeedback('helpful')}
                  >
                    <ThumbsUp className="size-3.5" aria-hidden="true" />
                    Helpful
                  </ResponseButton>
                  <ResponseButton
                    active={feedback === 'incorrect'}
                    onClick={() => onFeedback('incorrect')}
                  >
                    <ThumbsDown className="size-3.5" aria-hidden="true" />
                    Incorrect
                  </ResponseButton>
                </>
              )}
              {onCopy && (
                <ResponseButton onClick={onCopy}>
                  <Copy className="size-3.5" aria-hidden="true" />
                  {copied ? 'Copied' : 'Copy'}
                </ResponseButton>
              )}
              {onShare && (
                <ResponseButton onClick={onShare}>
                  <Share2 className="size-3.5" aria-hidden="true" />
                  {shared ? 'Shared' : 'Share'}
                </ResponseButton>
              )}
              {onExport && (
                <ResponseButton onClick={onExport}>
                  <Download className="size-3.5" aria-hidden="true" />
                  Export
                </ResponseButton>
              )}
              {onRegenerate && (
                <ResponseButton onClick={onRegenerate}>
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                  Regenerate
                </ResponseButton>
              )}
              {onEditPrompt && (
                <ResponseButton onClick={onEditPrompt}>
                  <Pencil className="size-3.5" aria-hidden="true" />
                  Edit prompt
                </ResponseButton>
              )}
              {tracefixRunId && !isStreaming && !isStopped && (
                <a
                  href={`/api/tellme/backend?run=${encodeURIComponent(tracefixRunId)}`}
                  target="_blank"
                  rel="noreferrer"
                  title="Open the technical TraceFix view in a new tab"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
                >
                  TraceFix run
                  <ArrowUpRight className="size-3.5" aria-hidden="true" />
                </a>
              )}
              <ResponseButton onClick={onViewGuidelines}>
                {result.guidelines.length} guidelines
                <ArrowUpRight className="size-3.5" aria-hidden="true" />
              </ResponseButton>
            </div>
          </div>
        )}

        {tab === 'agents' && (
          <div className="flex flex-col gap-4">
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              These are the only sensors and systems TeLLMe used for this
              answer. Each was limited to what was needed; nothing more was
              exposed in the user-facing result.
            </p>
            <ul className="flex flex-col gap-3">
              {result.agents.map((agent) => (
                <AgentRow key={agent.id} agent={agent} />
              ))}
            </ul>
            <div className="flex items-center gap-2 rounded-xl border border-border bg-secondary/50 px-3 py-2 text-[11px] text-muted-foreground">
              <ShieldCheck className="size-3.5 shrink-0" aria-hidden="true" />
              Privacy first -- no faces, voices, identities, raw captures, or precise source details are shown here.
            </div>
          </div>
        )}

        {tab === 'evidence' && (
          <div className="flex flex-col gap-4">
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              TeLLMe can use approved sensors to verify an answer, but captured
              media and raw evidence details are never shown in this app.
            </p>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {result.evidence.map((item) => (
                <EvidenceCard key={item.id} item={item} />
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

function TrustIndicators({
  result,
  responseMs,
}: {
  result: QueryResult
  responseMs?: number
}) {
  const confidence = result.confidence === null
    ? 'Not reported'
    : `${Math.round(result.confidence * 100)}%`
  const responseTime = typeof responseMs === 'number'
    ? `${(responseMs / 1000).toFixed(responseMs >= 1000 ? 1 : 2)}s`
    : 'Pending'
  const metrics = [
    { label: 'Confidence', value: confidence, icon: ShieldCheck },
    { label: 'Sources', value: result.agents.length.toString(), icon: Database },
    { label: 'Evidence used', value: result.evidence.length.toString(), icon: FileSearch },
    { label: 'Model', value: result.model || 'Not reported', icon: Cpu },
    { label: 'Response time', value: responseTime, icon: Timer },
  ]

  return (
    <div className="grid grid-cols-2 gap-2 rounded-xl border border-border bg-secondary/40 p-2 sm:grid-cols-5">
      {metrics.map(({ label, value, icon: Icon }) => (
        <div key={label} className="rounded-lg bg-background px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase text-muted-foreground">
            <Icon className="size-3" aria-hidden="true" />
            {label}
          </div>
          <p className="mt-1 text-sm font-semibold text-foreground">{value}</p>
        </div>
      ))}
    </div>
  )
}

function ResponseButton({
  active,
  onClick,
  children,
}: {
  active?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted',
        active && 'border-primary/30 bg-accent text-accent-foreground',
      )}
    >
      {children}
    </button>
  )
}

function AgentRow({ agent }: { agent: Agent }) {
  const Icon = agentIcon(agent.type)
  return (
    <li className="flex items-start gap-3 rounded-xl border border-border bg-background p-3.5">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
        <Icon className="size-4.5" aria-hidden="true" />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-sm font-semibold">{agent.name}</h4>
          <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
            {agent.type}
          </span>
          {agent.status && (
            <span className="rounded-full border border-border px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
              {agent.status}
            </span>
          )}
        </div>
        <p className="text-[13px] leading-relaxed text-muted-foreground">
          {agent.role}
        </p>
      </div>
    </li>
  )
}
