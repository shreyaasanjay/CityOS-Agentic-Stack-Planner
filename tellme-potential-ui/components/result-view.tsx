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
import type { Agent, QueryResult, RecordingCandidate } from '@/lib/api/types'
import { EvidenceCard } from '@/components/evidence-card'
import { MarkdownRenderer } from '@/components/markdown-renderer'
import { formatResponseTime } from '@/components/response-timer'
import { translate, type LanguageMode, type TranslationKey } from '@/lib/i18n'
import { cn } from '@/lib/utils'

type ResultTab = 'answer' | 'agents' | 'evidence'
type Feedback = 'helpful' | 'incorrect'

function confidenceLabel(value: number | null, language: LanguageMode) {
  if (value === null) return translate(language, 'result.confidenceNotReported')
  if (value >= 0.85) return translate(language, 'result.highConfidence')
  if (value >= 0.6) return translate(language, 'result.moderateConfidence')
  return translate(language, 'result.lowConfidence')
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
  language,
  onViewGuidelines,
  onCopy,
  onShare,
  onExport,
  onFeedback,
  onRegenerate,
  onEditPrompt,
  onSelectRecording,
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
  language: LanguageMode
  onViewGuidelines: () => void
  onCopy?: () => void
  onShare?: () => void
  onExport?: () => void
  onFeedback?: (feedback: Feedback) => void
  onRegenerate?: () => void
  onEditPrompt?: () => void
  onSelectRecording?: (candidate: RecordingCandidate) => void
}) {
  const [tab, setTab] = useState<ResultTab>('answer')
  const [selectedRecordingId, setSelectedRecordingId] = useState('')
  const recordingCandidates = result.recordingSelection?.candidates || []
  const selectedRecording = recordingCandidates.find((candidate) => candidate.recordingId === selectedRecordingId) || recordingCandidates[0]
  const pct = result.confidence === null ? null : Math.round(result.confidence * 100)
  const answer = visibleAnswer ?? result.answer
  const t = (key: TranslationKey, values?: Record<string, string | number>) =>
    translate(language, key, values)

  const tabs: { id: ResultTab; label: string; icon: typeof Sparkles; count?: number }[] = [
    { id: 'answer', label: t('result.answer'), icon: MessageSquareText },
    { id: 'agents', label: t('result.sensorsUsed'), icon: Cpu, count: result.agents.length },
    { id: 'evidence', label: t('result.privacyReceipt'), icon: FileSearch, count: result.evidence.length },
  ]

  return (
    <section
      aria-label={t('result.answer')}
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
                {t('result.groundedAnswer')}
                {isStreaming && (
                  <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                    {t('result.streaming')}
                  </span>
                )}
                {isStopped && (
                  <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                    {t('result.stopped')}
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
                {confidenceLabel(result.confidence, language)}{pct === null ? '' : ` - ${pct}%`}
              </span>
            </div>

            <TrustIndicators result={result} responseMs={responseMs} language={language} />

            <MarkdownRenderer
              content={answer}
              className="text-lg text-foreground sm:text-xl"
            />

            {result.recordingSelection && !isStreaming && (
              <div className="flex flex-col gap-3 border-t border-border pt-4">
                <p className="text-sm text-muted-foreground">{result.recordingSelection.prompt}</p>
                {recordingCandidates.length ? (
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <select value={selectedRecording?.recordingId || ''} onChange={(event) => setSelectedRecordingId(event.target.value)} className="min-w-0 flex-1 rounded-lg border border-border bg-background px-3 py-2.5 text-sm" aria-label="Choose a recording">
                      {recordingCandidates.map((candidate, index) => (
                        <option key={candidate.recordingId} value={candidate.recordingId}>
                          {`Recording ${index + 1}: ${[candidate.dateLabel, candidate.timeLabel, candidate.detail, candidate.label].filter(Boolean).join(' - ')}`}
                        </option>
                      ))}
                    </select>
                    <button type="button" onClick={() => selectedRecording && onSelectRecording?.(selectedRecording)} disabled={!selectedRecording || !onSelectRecording} className="rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50">Use recording</button>
                  </div>
                ) : (
                  <p className="text-sm text-destructive">The retrieval agent did not return selectable recording IDs. Regenerate the agents with the latest template.</p>
                )}
              </div>
            )}
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
                    {t('result.helpful')}
                  </ResponseButton>
                  <ResponseButton
                    active={feedback === 'incorrect'}
                    onClick={() => onFeedback('incorrect')}
                  >
                    <ThumbsDown className="size-3.5" aria-hidden="true" />
                    {t('result.incorrect')}
                  </ResponseButton>
                </>
              )}
              {onCopy && (
                <ResponseButton onClick={onCopy}>
                  <Copy className="size-3.5" aria-hidden="true" />
                  {copied ? t('result.copied') : t('result.copy')}
                </ResponseButton>
              )}
              {onShare && (
                <ResponseButton onClick={onShare}>
                  <Share2 className="size-3.5" aria-hidden="true" />
                  {shared ? t('result.shared') : t('result.share')}
                </ResponseButton>
              )}
              {onExport && (
                <ResponseButton onClick={onExport}>
                  <Download className="size-3.5" aria-hidden="true" />
                  {t('result.export')}
                </ResponseButton>
              )}
              {onRegenerate && (
                <ResponseButton onClick={onRegenerate}>
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                  {t('main.regenerate')}
                </ResponseButton>
              )}
              {onEditPrompt && (
                <ResponseButton onClick={onEditPrompt}>
                  <Pencil className="size-3.5" aria-hidden="true" />
                  {t('main.editPrompt')}
                </ResponseButton>
              )}
              {tracefixRunId && !isStreaming && !isStopped && (
                <a
                  href={`/api/tellme/backend?run=${encodeURIComponent(tracefixRunId)}`}
                  target="_blank"
                  rel="noreferrer"
                  title={t('result.tracefixTitle')}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
                >
                  {t('result.tracefixRun')}
                  <ArrowUpRight className="size-3.5" aria-hidden="true" />
                </a>
              )}
              <ResponseButton onClick={onViewGuidelines}>
                {t('result.guidelinesCount', { count: result.guidelines.length })}
                <ArrowUpRight className="size-3.5" aria-hidden="true" />
              </ResponseButton>
            </div>
          </div>
        )}

        {tab === 'agents' && (
          <div className="flex flex-col gap-4">
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              {t('result.sensorsDescription')}
            </p>
            <ul className="flex flex-col gap-3">
              {result.agents.map((agent) => (
                <AgentRow key={agent.id} agent={agent} />
              ))}
            </ul>
            <div className="flex items-center gap-2 rounded-xl border border-border bg-secondary/50 px-3 py-2 text-[11px] text-muted-foreground">
              <ShieldCheck className="size-3.5 shrink-0" aria-hidden="true" />
              {t('result.privacyFirst')}
            </div>
          </div>
        )}

        {tab === 'evidence' && (
          <div className="flex flex-col gap-4">
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              {t('result.evidenceDescription')}
            </p>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {result.evidence.map((item) => (
                <EvidenceCard key={item.id} item={item} language={language} />
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
  language,
}: {
  result: QueryResult
  responseMs?: number
  language: LanguageMode
}) {
  const t = (key: TranslationKey) => translate(language, key)
  const confidence = result.confidence === null
    ? t('result.notReported')
    : `${Math.round(result.confidence * 100)}%`
  const responseTime = typeof responseMs === 'number'
    ? formatResponseTime(responseMs)
    : t('result.pending')
  const metrics = [
    { label: t('result.confidence'), value: confidence, icon: ShieldCheck },
    { label: t('result.sources'), value: result.agents.length.toString(), icon: Database },
    { label: t('result.evidenceUsed'), value: result.evidence.length.toString(), icon: FileSearch },
    { label: t('result.model'), value: result.model || t('result.notReported'), icon: Cpu },
    { label: t('result.responseTime'), value: responseTime, icon: Timer },
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
