'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Sparkles,
  MessageSquareText,
  ShieldCheck,
  MapPin,
  FileText,
} from 'lucide-react'
import { queryApi } from '@/lib/api/client'
import type { QueryProgressStage, QueryResult } from '@/lib/api/types'
import { AppHeader, type WorkspaceTab } from '@/components/app-header'
import type { AppearanceSettings } from '@/components/settings-menu'
import {
  ConversationSidebar,
  type ConversationSummary,
  type RuntimeConfig,
} from '@/components/conversation-sidebar'
import { QueryConsole } from '@/components/query-console'
import { UserQuery } from '@/components/user-query'
import { ResultView } from '@/components/result-view'
import { ThinkingIndicator } from '@/components/thinking-indicator'
import { GuidelinesPanel } from '@/components/guidelines-panel'
import { cn } from '@/lib/utils'

type Feedback = 'helpful' | 'incorrect'

interface Turn {
  id: string
  query: string
  status: 'loading' | 'streaming' | 'done' | 'error' | 'stopped'
  result?: QueryResult
  visibleAnswer?: string
  feedback?: Feedback
  startedAt?: number
  responseMs?: number
  progressStage?: QueryProgressStage
  backendRunId?: string
  errorMessage?: string
}

interface Conversation {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  pinned: boolean
  turns: Turn[]
}

const DEFAULT_RUNTIME_CONFIG: RuntimeConfig = {
  mode: 'llm',
  appModel: 'gpt-4.1-mini',
  openaiApiKey: '',
  spaceId: 'smart_room_1',
  mirrorApiUrl: 'https://smartroom-mirror.vercel.app/api/v1',
  timestamp: '',
  tracefixProvider: 'openrouter',
  tracefixModel: 'z-ai/glm-5.2',
  tracefixApiKey: '',
}

const DEFAULT_SETTINGS: AppearanceSettings = {
  theme: 'light',
  themeColor: 'green',
  textScale: 'comfortable',
  density: 'standard',
  highContrast: false,
  reduceMotion: false,
  streamingEnabled: true,
  answerStyle: 'concise',
  citationStyle: 'summary',
  language: 'en',
}

const SETTINGS_STORAGE_KEY = 'tellme-appearance-settings'
const RUNTIME_CONFIG_STORAGE_KEY = 'tellme-runtime-config-public'
const CONVERSATIONS_STORAGE_KEY = 'tellme-conversations'
const ACTIVE_CONVERSATION_KEY = 'tellme-active-conversation'
const STREAM_CHUNK_WORDS = 3
const STREAM_DELAY_MS = 45

const PLANNED_CAPABILITIES = [
  {
    icon: MessageSquareText,
    title: 'Ask in plain language',
    detail:
      'Residents describe what happened in their own words. No need to know which cameras, sensors, or feeds exist.',
  },
  {
    icon: FileText,
    title: 'Answers with evidence',
    detail:
      'Every answer is meant to arrive with a private evidence receipt showing that sources were checked without exposing raw captures.',
  },
  {
    icon: ShieldCheck,
    title: 'Privacy-aware by design',
    detail:
      'Raw media, source identifiers, exact timestamps, and precise evidence locations are withheld from the resident-facing app.',
  },
  {
    icon: MapPin,
    title: 'Grounded to a place and time',
    detail:
      'Queries would be scoped to the location and window implied by the question, not the whole city.',
  },
]

function createConversation(): Conversation {
  const now = new Date().toISOString()
  return {
    id: `chat_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    title: 'New chat',
    createdAt: now,
    updatedAt: now,
    pinned: false,
    turns: [],
  }
}

function titleFromQuery(query: string) {
  const normalized = query.replace(/\s+/g, ' ').trim()
  if (!normalized) return 'New chat'
  return normalized.length > 42 ? `${normalized.slice(0, 39)}...` : normalized
}

export default function Page() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState('')
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('conversation')
  const [isLoading, setIsLoading] = useState(false)
  const [settings, setSettings] = useState<AppearanceSettings>(DEFAULT_SETTINGS)
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>(DEFAULT_RUNTIME_CONFIG)
  const [editingTurnId, setEditingTurnId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [copiedTurnId, setCopiedTurnId] = useState<string | null>(null)
  const [sharedTurnId, setSharedTurnId] = useState<string | null>(null)
  const [conversationSearch, setConversationSearch] = useState('')
  const [renamingConversationId, setRenamingConversationId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [hasLoadedConversations, setHasLoadedConversations] = useState(false)
  const threadEndRef = useRef<HTMLDivElement>(null)
  const stoppedIdsRef = useRef(new Set<string>())
  const streamTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>[]>>({})
  const requestControllersRef = useRef<Record<string, AbortController>>({})
  const backendRunIdsRef = useRef<Record<string, string>>({})

  const activeConversation = conversations.find(
    (conversation) => conversation.id === activeConversationId,
  )
  const turns = activeConversation?.turns ?? []
  const latestResult = [...turns].reverse().find((turn) => turn.result)?.result
  const guidelines = latestResult?.guidelines ?? []
  const hasTurns = turns.length > 0
  const isCompact = settings.density === 'compact'

  const conversationSummaries = useMemo<ConversationSummary[]>(() => {
    return [...conversations]
      .sort((a, b) => {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
        return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
      })
      .map((conversation) => ({
        id: conversation.id,
        title: conversation.title,
        createdAt: conversation.createdAt,
        updatedAt: conversation.updatedAt,
        pinned: conversation.pinned,
        turnCount: conversation.turns.length,
      }))
  }, [conversations])

  useEffect(() => {
    const storedSettings = window.localStorage.getItem(SETTINGS_STORAGE_KEY)
    if (storedSettings) {
      try {
        setSettings({ ...DEFAULT_SETTINGS, ...JSON.parse(storedSettings) })
      } catch {
        window.localStorage.removeItem(SETTINGS_STORAGE_KEY)
      }
    }

    const storedRuntimeConfig = window.localStorage.getItem(RUNTIME_CONFIG_STORAGE_KEY)
    if (storedRuntimeConfig) {
      try {
        setRuntimeConfig({
          ...DEFAULT_RUNTIME_CONFIG,
          ...JSON.parse(storedRuntimeConfig),
          mode: 'llm',
          openaiApiKey: '',
          tracefixApiKey: '',
        })
      } catch {
        window.localStorage.removeItem(RUNTIME_CONFIG_STORAGE_KEY)
      }
    }

    const storedConversations = window.localStorage.getItem(CONVERSATIONS_STORAGE_KEY)
    const storedActiveId = window.localStorage.getItem(ACTIVE_CONVERSATION_KEY)
    const startFreshConversation = () => {
      const conversation = createConversation()
      setConversations([conversation])
      setActiveConversationId(conversation.id)
    }

    if (storedConversations) {
      try {
        const parsed = JSON.parse(storedConversations) as Conversation[]
        const validConversations = parsed.length > 0 ? parsed : [createConversation()]
        setConversations(validConversations)
        setActiveConversationId(
          validConversations.some((conversation) => conversation.id === storedActiveId)
            ? storedActiveId as string
            : validConversations[0].id,
        )
      } catch {
        window.localStorage.removeItem(CONVERSATIONS_STORAGE_KEY)
        startFreshConversation()
      }
    } else {
      startFreshConversation()
    }
    setHasLoadedConversations(true)
  }, [])

  useEffect(() => {
    window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings))
    document.documentElement.classList.toggle('dark', settings.theme === 'dark')
  }, [settings])

  useEffect(() => {
    if (!hasLoadedConversations) return
    window.localStorage.setItem(CONVERSATIONS_STORAGE_KEY, JSON.stringify(conversations))
    window.localStorage.setItem(ACTIVE_CONVERSATION_KEY, activeConversationId)
  }, [activeConversationId, conversations, hasLoadedConversations])

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({
      behavior: settings.reduceMotion ? 'auto' : 'smooth',
    })
  }, [turns, settings.reduceMotion])

  useEffect(() => {
    return () => {
      Object.values(streamTimersRef.current).flat().forEach(clearTimeout)
      Object.values(requestControllersRef.current).forEach((controller) => controller.abort())
    }
  }, [])

  async function handleSubmit(query: string, replaceTurnId?: string) {
    const id = replaceTurnId ?? `turn_${Date.now()}`
    clearStream(id)
    requestControllersRef.current[id]?.abort()
    const controller = new AbortController()
    requestControllersRef.current[id] = controller
    delete backendRunIdsRef.current[id]
    stoppedIdsRef.current.delete(id)
    setCopiedTurnId(null)
    setSharedTurnId(null)
    setEditingTurnId(null)
    setEditValue('')
    setIsLoading(true)
    setActiveTab('conversation')

    updateActiveConversation((conversation) => {
      const nextTurn: Turn = {
        id,
        query,
        status: 'loading',
        visibleAnswer: '',
        startedAt: Date.now(),
        progressStage: 'planning',
      }
      const nextTurns = replaceTurnId
        ? conversation.turns.map((turn) => (turn.id === id ? nextTurn : turn))
        : [...conversation.turns, nextTurn]

      return {
        ...conversation,
        title:
          conversation.title === 'New chat' && nextTurns.length === 1
            ? titleFromQuery(query)
            : conversation.title,
        updatedAt: new Date().toISOString(),
        turns: nextTurns,
      }
    })

    try {
      const result = await queryApi.submitQuery({
        query,
        spaceId: runtimeConfig.spaceId,
        timestamp: runtimeConfig.timestamp || undefined,
        mode: runtimeConfig.mode,
        model: runtimeConfig.appModel,
        mirrorApiUrl: runtimeConfig.mirrorApiUrl,
        openaiApiKey: runtimeConfig.openaiApiKey,
        tracefixProvider: runtimeConfig.tracefixProvider,
        tracefixModel: runtimeConfig.tracefixModel,
        tracefixApiKey: runtimeConfig.tracefixApiKey,
      }, {
        signal: controller.signal,
        onProgress: (progress) => {
          if (stoppedIdsRef.current.has(id)) return
          if (progress.runId) backendRunIdsRef.current[id] = progress.runId
          updateTurn(id, (turn) => ({
            ...turn,
            progressStage: progress.stage,
            backendRunId: progress.runId || turn.backendRunId,
          }))
        },
      })
      if (stoppedIdsRef.current.has(id)) return
      if (settings.streamingEnabled) {
        beginStreaming(id, result)
      } else {
        updateTurn(id, (turn) => ({
          ...turn,
          status: 'done',
          result,
          visibleAnswer: result.answer,
          responseMs: Math.max(1, Date.now() - (turn.startedAt ?? Date.now())),
        }))
        setIsLoading(false)
      }
    } catch (error) {
      if (stoppedIdsRef.current.has(id)) return
      updateTurn(id, (turn) => ({
        ...turn,
        status: 'error',
        visibleAnswer: '',
        errorMessage: error instanceof Error
          ? error.message
          : 'Something went wrong reaching the backend.',
      }))
      setIsLoading(false)
    } finally {
      if (requestControllersRef.current[id] === controller) {
        delete requestControllersRef.current[id]
      }
    }
  }

  function beginStreaming(id: string, result: QueryResult) {
    const words = result.answer.split(/(\s+)/)
    const timers: ReturnType<typeof setTimeout>[] = []

    updateTurn(id, (turn) => ({
      ...turn,
      status: 'streaming',
      result,
      visibleAnswer: '',
      responseMs: Math.max(1, Date.now() - (turn.startedAt ?? Date.now())),
    }))

    const chunkSize = STREAM_CHUNK_WORDS * 2
    const chunkIndexes: number[] = []
    for (let index = chunkSize; index < words.length; index += chunkSize) {
      chunkIndexes.push(index)
    }
    chunkIndexes.push(words.length)

    chunkIndexes.forEach((index, chunkIndex) => {
      const timer = setTimeout(() => {
        if (stoppedIdsRef.current.has(id)) return
        const visibleAnswer = words.slice(0, index).join('')
        const done = index >= words.length

        updateTurn(id, (turn) => ({
          ...turn,
          status: done ? 'done' : 'streaming',
          result,
          visibleAnswer: done ? result.answer : visibleAnswer,
        }))

        if (done) {
          delete streamTimersRef.current[id]
          setIsLoading(false)
        }
      }, (chunkIndex + 1) * STREAM_DELAY_MS)
      timers.push(timer)
    })

    streamTimersRef.current[id] = timers
  }

  function stopTurn(id: string) {
    stoppedIdsRef.current.add(id)
    clearStream(id)
    requestControllersRef.current[id]?.abort()
    delete requestControllersRef.current[id]
    const backendRunId = backendRunIdsRef.current[id]
    if (backendRunId) void queryApi.stopQuery(backendRunId).catch(() => undefined)
    updateTurn(id, (turn) => ({
      ...turn,
      status: 'stopped',
      visibleAnswer: turn.visibleAnswer || '',
    }))
    setIsLoading(false)
  }

  function updateActiveConversation(updater: (conversation: Conversation) => Conversation) {
    setConversations((prev) =>
      prev.map((conversation) =>
        conversation.id === activeConversationId ? updater(conversation) : conversation,
      ),
    )
  }

  function updateTurn(id: string, updater: (turn: Turn) => Turn) {
    setConversations((prev) =>
      prev.map((conversation) => {
        const hasTurn = conversation.turns.some((turn) => turn.id === id)
        if (!hasTurn) return conversation
        return {
          ...conversation,
          updatedAt: new Date().toISOString(),
          turns: conversation.turns.map((turn) => (turn.id === id ? updater(turn) : turn)),
        }
      }),
    )
  }

  function clearStream(id: string) {
    streamTimersRef.current[id]?.forEach(clearTimeout)
    delete streamTimersRef.current[id]
  }

  function newChat() {
    const conversation = createConversation()
    setConversations((prev) => [conversation, ...prev])
    setActiveConversationId(conversation.id)
    setActiveTab('conversation')
    setEditingTurnId(null)
  }

  function selectConversation(id: string) {
    setActiveConversationId(id)
    setActiveTab('conversation')
    setEditingTurnId(null)
  }

  function startRenamingConversation(id: string, title: string) {
    setRenamingConversationId(id)
    setRenameValue(title)
  }

  function commitRename(id: string) {
    const title = renameValue.trim()
    if (!title) return
    setConversations((prev) =>
      prev.map((conversation) =>
        conversation.id === id
          ? { ...conversation, title, updatedAt: new Date().toISOString() }
          : conversation,
      ),
    )
    setRenamingConversationId(null)
    setRenameValue('')
  }

  function togglePin(id: string) {
    setConversations((prev) =>
      prev.map((conversation) =>
        conversation.id === id
          ? { ...conversation, pinned: !conversation.pinned, updatedAt: new Date().toISOString() }
          : conversation,
      ),
    )
  }

  function deleteConversation(id: string) {
    setConversations((prev) => {
      const remaining = prev.filter((conversation) => conversation.id !== id)
      const next = remaining.length > 0 ? remaining : [createConversation()]
      if (id === activeConversationId) setActiveConversationId(next[0].id)
      return next
    })
  }

  function startEditing(turn: Turn) {
    setEditingTurnId(turn.id)
    setEditValue(turn.query)
  }

  function submitEditedPrompt(id: string) {
    const trimmed = editValue.trim()
    if (!trimmed) return
    handleSubmit(trimmed, id)
  }

  async function copyResponse(turn: Turn) {
    const text = answerText(turn)
    if (!text) return

    try {
      await navigator.clipboard.writeText(text)
      setCopiedTurnId(turn.id)
      window.setTimeout(() => setCopiedTurnId(null), 1600)
    } catch {
      setCopiedTurnId(null)
    }
  }

  async function shareResponse(turn: Turn) {
    const text = answerText(turn)
    if (!text) return

    try {
      if (navigator.share) {
        await navigator.share({ title: activeConversation?.title ?? 'TeLLMe answer', text })
      } else {
        await navigator.clipboard.writeText(text)
      }
      setSharedTurnId(turn.id)
      window.setTimeout(() => setSharedTurnId(null), 1600)
    } catch {
      setSharedTurnId(null)
    }
  }

  function exportResponse(turn: Turn) {
    const text = answerText(turn)
    if (!text) return
    const body = [
      `Conversation: ${activeConversation?.title ?? 'TeLLMe chat'}`,
      `Prompt: ${turn.query}`,
      `Exported: ${new Date().toLocaleString()}`,
      '',
      text,
    ].join('\n')
    const blob = new Blob([body], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${(activeConversation?.title ?? 'tellme-answer')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'tellme-answer'}.txt`
    link.click()
    URL.revokeObjectURL(url)
  }

  function setFeedback(turnId: string, feedback: Feedback) {
    updateTurn(turnId, (turn) => ({
      ...turn,
      feedback: turn.feedback === feedback ? undefined : feedback,
    }))
  }

  function answerText(turn: Turn) {
    return turn.status === 'stopped'
      ? turn.visibleAnswer || turn.result?.answer || ''
      : turn.result?.answer || turn.visibleAnswer || ''
  }

  return (
    <div
      className={cn(
        'flex min-h-screen flex-col bg-background',
        settings.themeColor === 'blue' && '[--primary:oklch(0.58_0.11_230)] [--ring:oklch(0.58_0.11_230)] [--accent:oklch(0.94_0.035_230)] [--accent-foreground:oklch(0.34_0.06_230)]',
        settings.themeColor === 'violet' && '[--primary:oklch(0.58_0.13_300)] [--ring:oklch(0.58_0.13_300)] [--accent:oklch(0.94_0.04_300)] [--accent-foreground:oklch(0.36_0.07_300)]',
        settings.textScale === 'large' && 'text-[110%]',
        settings.highContrast && 'contrast-125',
        settings.reduceMotion && '[&_*]:!scroll-auto [&_*]:!transition-none [&_*]:!animate-none',
      )}
    >
      <AppHeader
        activeTab={activeTab}
        onTabChange={setActiveTab}
        guidelineCount={guidelines.length}
        settings={settings}
        onSettingsChange={setSettings}
      />

      <div className="flex min-h-0 flex-1">
        <ConversationSidebar
          conversations={conversationSummaries}
          activeConversationId={activeConversationId}
          search={conversationSearch}
          renamingId={renamingConversationId}
          renameValue={renameValue}
          runtimeConfig={runtimeConfig}
          onRuntimeConfigChange={setRuntimeConfig}
          onSearchChange={setConversationSearch}
          onNewChat={newChat}
          onSelectConversation={selectConversation}
          onStartRename={startRenamingConversation}
          onRenameValueChange={setRenameValue}
          onCommitRename={commitRename}
          onCancelRename={() => setRenamingConversationId(null)}
          onTogglePin={togglePin}
          onDeleteConversation={deleteConversation}
        />

        {activeTab === 'conversation' ? (
          <div className="flex min-w-0 flex-1 flex-col">
            <main
              className={cn(
                'mx-auto w-full max-w-3xl flex-1 px-4 pb-40 sm:px-6',
                isCompact ? 'pt-5' : 'pt-8',
              )}
            >
              {!hasTurns ? (
                <EmptyState compact={isCompact} />
              ) : (
                <div className={cn('flex flex-col', isCompact ? 'gap-5' : 'gap-8')}>
                  {turns.map((turn) => (
                    <div
                      key={turn.id}
                      className={cn('flex flex-col', isCompact ? 'gap-3' : 'gap-5')}
                    >
                      {editingTurnId === turn.id ? (
                        <EditPromptForm
                          value={editValue}
                          onChange={setEditValue}
                          onCancel={() => setEditingTurnId(null)}
                          onSubmit={() => submitEditedPrompt(turn.id)}
                        />
                      ) : (
                        <UserQuery
                          text={turn.query}
                          onEdit={() => startEditing(turn)}
                        />
                      )}

                      {turn.status === 'loading' && (
                        <ThinkingIndicator
                          stage={turn.progressStage ?? 'planning'}
                          backendRunId={turn.backendRunId}
                          onStop={() => stopTurn(turn.id)}
                        />
                      )}
                      {turn.status === 'error' && (
                        <div className="flex flex-col gap-3 rounded-2xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                          <span>{turn.errorMessage || 'Something went wrong reaching the backend. Please try again.'}</span>
                          <div className="flex gap-2">
                            <ActionButton onClick={() => handleSubmit(turn.query, turn.id)}>
                              Regenerate
                            </ActionButton>
                            <ActionButton onClick={() => startEditing(turn)}>
                              Edit prompt
                            </ActionButton>
                          </div>
                        </div>
                      )}
                      {turn.status === 'stopped' && !turn.result && (
                        <div className="flex flex-col gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-sm text-muted-foreground shadow-sm">
                          <span>Generation stopped before an answer was ready.</span>
                          <div className="flex gap-2">
                            <ActionButton onClick={() => handleSubmit(turn.query, turn.id)}>
                              Regenerate
                            </ActionButton>
                            <ActionButton onClick={() => startEditing(turn)}>
                              Edit prompt
                            </ActionButton>
                          </div>
                        </div>
                      )}
                      {turn.result && (
                        <ResultView
                          result={turn.result}
                          visibleAnswer={turn.visibleAnswer}
                          isStreaming={turn.status === 'streaming'}
                          isStopped={turn.status === 'stopped'}
                          copied={copiedTurnId === turn.id}
                          shared={sharedTurnId === turn.id}
                          feedback={turn.feedback}
                          responseMs={turn.responseMs}
                          tracefixRunId={turn.status === 'done' ? turn.backendRunId : undefined}
                          onViewGuidelines={() => setActiveTab('guidelines')}
                          onCopy={() => copyResponse(turn)}
                          onShare={() => shareResponse(turn)}
                          onExport={() => exportResponse(turn)}
                          onFeedback={(feedback) => setFeedback(turn.id, feedback)}
                          onRegenerate={() => handleSubmit(turn.query, turn.id)}
                          onEditPrompt={() => startEditing(turn)}
                        />
                      )}
                      {turn.status === 'streaming' && (
                        <div className="flex justify-start">
                          <ActionButton onClick={() => stopTurn(turn.id)}>Stop generation</ActionButton>
                        </div>
                      )}
                    </div>
                  ))}
                  <div ref={threadEndRef} />
                </div>
              )}
            </main>

            <div className="sticky bottom-0 z-10 border-t border-border bg-background/80 backdrop-blur-md">
              <div
                className={cn(
                  'mx-auto w-full max-w-3xl px-4 sm:px-6',
                  isCompact ? 'py-2.5' : 'py-4',
                )}
              >
                <QueryConsole onSubmit={(query) => handleSubmit(query)} disabled={isLoading} />
              </div>
            </div>
          </div>
        ) : (
          <main
            className={cn(
              'mx-auto w-full max-w-3xl flex-1 px-4 sm:px-6',
              isCompact ? 'py-5' : 'py-8',
            )}
          >
            <GuidelinesPanel
              guidelines={guidelines}
              query={
                latestResult
                  ? turns.findLast((turn) => turn.result)?.query ?? null
                  : null
              }
            />
          </main>
        )}
      </div>
    </div>
  )
}

function ActionButton({
  children,
  onClick,
}: {
  children: React.ReactNode
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex w-fit items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
    >
      {children}
    </button>
  )
}

function EditPromptForm({
  value,
  onChange,
  onCancel,
  onSubmit,
}: {
  value: string
  onChange: (value: string) => void
  onCancel: () => void
  onSubmit: () => void
}) {
  return (
    <div className="flex justify-end">
      <div className="flex w-full max-w-2xl flex-col gap-2 rounded-2xl border border-border bg-card p-3 shadow-sm">
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={3}
          className="min-h-20 resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm leading-relaxed text-foreground outline-none transition-colors focus:border-ring"
          aria-label="Edit previous prompt"
        />
        <div className="flex justify-end gap-2">
          <ActionButton onClick={onCancel}>Cancel</ActionButton>
          <ActionButton onClick={onSubmit}>Update and regenerate</ActionButton>
        </div>
      </div>
    </div>
  )
}

function EmptyState({ compact }: { compact: boolean }) {
  return (
    <div className={cn('flex flex-col', compact ? 'gap-6 py-5' : 'gap-10 py-8 sm:py-12')}>
      <div className="flex flex-col items-center gap-4 text-center">
        <span className="flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
          <Sparkles className="size-6" aria-hidden="true" />
        </span>
        <div className="max-w-xl">
          <h1 className="text-2xl font-semibold tracking-tight text-balance sm:text-3xl">
            Ask the city a question
          </h1>
          <p className="mt-2 text-pretty leading-relaxed text-muted-foreground">
            Describe what happened in plain language, like losing your keys on
            a street this afternoon. TeLLMe returns a grounded answer backed by
            private verification from nearby city sensors. You do not need to
            know which cameras or sensors exist.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          What TeLLMe is meant to do
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {PLANNED_CAPABILITIES.map(({ icon: Icon, title, detail }) => (
            <div
              key={title}
              className="relative flex flex-col gap-2.5 rounded-xl border border-dashed border-border bg-card/50 p-4"
            >
              <div className="flex items-center justify-between">
                <span className="flex size-8 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                  <Icon className="size-4" aria-hidden="true" />
                </span>
                <span className="rounded-full border border-border bg-background px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                  Planned
                </span>
              </div>
              <h3 className="text-sm font-semibold text-foreground">{title}</h3>
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                {detail}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
