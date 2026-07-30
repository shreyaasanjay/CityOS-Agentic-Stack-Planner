'use client'

import { useEffect, useState } from 'react'
import {
  Box,
  Check,
  ChevronDown,
  Clock,
  Cpu,
  KeyRound,
  Link2,
  MessageSquarePlus,
  Pencil,
  Pin,
  PinOff,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import { ApiKeyInput } from '@/components/api-key-input'
import {
  getApiKeyOriginPolicy,
  type ApiKeyOriginPolicy,
} from '@/lib/security/api-key-origin'
import {
  LANGUAGE_LOCALES,
  translate,
  type LanguageMode,
  type TranslationKey,
} from '@/lib/i18n'
import { cn } from '@/lib/utils'

export interface ConversationSummary {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  pinned: boolean
  turnCount: number
}

export interface RuntimeConfig {
  mode: 'llm' | 'deterministic'
  appModel: string
  openaiApiKey: string
  spaceId: string
  mirrorApiUrl: string
  dataSource: 'smartroom' | 'carla'
  carlaApiKey: string
  timestamp: string
  tracefixProvider: 'openai' | 'anthropic' | 'openrouter' | 'local'
  tracefixModel: string
  tracefixApiKey: string
  cityosAgentProvider: 'openai' | 'anthropic' | 'openrouter' | 'local'
  cityosAgentModel: string
  cityosAgentApiKey: string
}

interface ConversationSidebarProps {
  conversations: ConversationSummary[]
  activeConversationId: string
  search: string
  renamingId: string | null
  renameValue: string
  runtimeConfig: RuntimeConfig
  language: LanguageMode
  onRuntimeConfigChange: (config: RuntimeConfig) => void
  onSearchChange: (value: string) => void
  onNewChat: () => void
  onSelectConversation: (id: string) => void
  onStartRename: (id: string, title: string) => void
  onRenameValueChange: (value: string) => void
  onCommitRename: (id: string) => void
  onCancelRename: () => void
  onTogglePin: (id: string) => void
  onDeleteConversation: (id: string) => void
}

export function ConversationSidebar({
  conversations,
  activeConversationId,
  search,
  renamingId,
  renameValue,
  runtimeConfig,
  language,
  onRuntimeConfigChange,
  onSearchChange,
  onNewChat,
  onSelectConversation,
  onStartRename,
  onRenameValueChange,
  onCommitRename,
  onCancelRename,
  onTogglePin,
  onDeleteConversation,
}: ConversationSidebarProps) {
  const t = (key: TranslationKey) => translate(language, key)
  const filtered = conversations.filter((conversation) =>
    conversation.title.toLowerCase().includes(search.toLowerCase()),
  )
  const pinned = filtered.filter((conversation) => conversation.pinned)
  const recent = filtered.filter((conversation) => !conversation.pinned)

  return (
    <aside className="hidden w-80 shrink-0 border-r border-border bg-card/50 lg:flex lg:flex-col">
      <div className="flex flex-col gap-3 border-b border-border p-3">
        <button
          type="button"
          onClick={onNewChat}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <MessageSquarePlus className="size-4" aria-hidden="true" />
          {t('sidebar.newChat')}
        </button>
        <label className="flex h-9 items-center gap-2 rounded-lg border border-border bg-background px-3 text-sm text-muted-foreground focus-within:border-ring">
          <Search className="size-4 shrink-0" aria-hidden="true" />
          <input
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder={t('sidebar.searchChats')}
            aria-label={t('sidebar.searchChats')}
            className="min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
          />
        </label>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-3">
        <ConnectionSetup
          config={runtimeConfig}
          onChange={onRuntimeConfigChange}
          language={language}
        />

        {pinned.length > 0 && (
          <ConversationGroup
            label={t('sidebar.pinned')}
            language={language}
            conversations={pinned}
            activeConversationId={activeConversationId}
            renamingId={renamingId}
            renameValue={renameValue}
            onSelectConversation={onSelectConversation}
            onStartRename={onStartRename}
            onRenameValueChange={onRenameValueChange}
            onCommitRename={onCommitRename}
            onCancelRename={onCancelRename}
            onTogglePin={onTogglePin}
            onDeleteConversation={onDeleteConversation}
          />
        )}

        <ConversationGroup
          label={t('sidebar.recent')}
          language={language}
          conversations={recent}
          activeConversationId={activeConversationId}
          renamingId={renamingId}
          renameValue={renameValue}
          onSelectConversation={onSelectConversation}
          onStartRename={onStartRename}
          onRenameValueChange={onRenameValueChange}
          onCommitRename={onCommitRename}
          onCancelRename={onCancelRename}
          onTogglePin={onTogglePin}
          onDeleteConversation={onDeleteConversation}
        />
      </div>
    </aside>
  )
}

function ConnectionSetup({
  config,
  onChange,
  language,
}: {
  config: RuntimeConfig
  onChange: (config: RuntimeConfig) => void
  language: LanguageMode
}) {
  const hasOpenAiKey = config.openaiApiKey.trim().length > 0
  const hasTracefixKey = config.tracefixApiKey.trim().length > 0
  const hasCityosAgentKey = config.cityosAgentApiKey.trim().length > 0
  const [originPolicy, setOriginPolicy] = useState<ApiKeyOriginPolicy>({
    canUseApiKeys: false,
    hostname: '',
    protocol: '',
    label: 'Checking origin',
    message: 'Checking whether this origin can safely accept API keys.',
  })

  useEffect(() => {
    setOriginPolicy(getApiKeyOriginPolicy(window.location))
  }, [])
  const t = (key: TranslationKey) => translate(language, key)

  function update<K extends keyof RuntimeConfig>(key: K, value: RuntimeConfig[K]) {
    onChange({ ...config, [key]: value })
  }

  return (
    <section className="rounded-xl border border-border bg-card p-3 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase text-muted-foreground">
            {t('sidebar.connectionSetup')}
          </p>
          <p className="mt-1 text-sm font-semibold text-foreground">
            {t('sidebar.smartRoomRequest')}
          </p>
        </div>
        <span
          className={cn(
            'rounded-full border px-2 py-1 text-[10px] font-medium',
            originPolicy.canUseApiKeys
              ? 'border-primary/30 bg-accent text-accent-foreground'
              : 'border-destructive/30 bg-destructive/10 text-destructive',
          )}
          title={`${originPolicy.protocol}//${originPolicy.hostname}`}
        >
          {originPolicy.label}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Field label={t('sidebar.mode')}>
          <select
            value={config.mode}
            onChange={(event) => update('mode', event.target.value as RuntimeConfig['mode'])}
            className="form-field"
          >
            <option value="llm">LLM / API</option>
            <option value="deterministic">{t('sidebar.deterministic')}</option>
          </select>
        </Field>
        <Field label={t('sidebar.model')}>
          <select
            value={config.appModel}
            onChange={(event) => update('appModel', event.target.value)}
            className="form-field"
          >
            <option value="gpt-4.1-mini">gpt-4.1-mini</option>
            <option value="gpt-5-mini">gpt-5-mini</option>
            <option value="gpt-5">gpt-5</option>
          </select>
        </Field>
      </div>

      <Field
        label={t('sidebar.openAiKey')}
        icon={KeyRound}
        status={hasOpenAiKey ? t('sidebar.detected') : t('sidebar.missing')}
        detected={hasOpenAiKey}
      >
        <ApiKeyInput
          name="openai-api-key"
          value={config.openaiApiKey}
          onValueChange={(value) => update('openaiApiKey', value)}
          placeholder={t('sidebar.pasteProviderKey')}
          policy={originPolicy}
        />
      </Field>

      <Field label={t('sidebar.spaceId')} icon={Box}>
        <input
          value={config.spaceId}
          onChange={(event) => update('spaceId', event.target.value)}
          placeholder="smart_room_1"
          className="form-field"
        />
      </Field>

      <Field label="Data source" icon={Link2}>
        <select value={config.dataSource} onChange={(event) => {
          const dataSource = event.target.value as RuntimeConfig['dataSource']
          onChange({ ...config, dataSource, mirrorApiUrl: dataSource === 'carla' ? 'http://172.16.60.239:8420' : 'http://172.16.60.239:3000/api/v1' })
        }} className="form-field">
          <option value="smartroom">Smart-room recordings</option>
          <option value="carla">CARLA city traces</option>
        </select>
      </Field>
      {config.dataSource === 'carla' && <Field label="CARLA API key" icon={KeyRound}><ApiKeyInput name="carla-api-key" value={config.carlaApiKey} onValueChange={(value) => update('carlaApiKey', value)} placeholder="Paste CARLA server key" policy={originPolicy} /></Field>}
      <Field label={t('sidebar.mirrorUrl')} icon={Link2}>
        <input
          value={config.mirrorApiUrl}
          onChange={(event) => update('mirrorApiUrl', event.target.value)}
          placeholder="http://172.16.60.239:3000/api"
          className="form-field"
        />
      </Field>

      <Field label={t('sidebar.timestamp')} icon={Clock}>
        <div className="flex gap-2">
          <input
            value={config.timestamp}
            onChange={(event) => update('timestamp', event.target.value)}
            placeholder="2026-06-30T19:00:00Z"
            className="form-field min-w-0 flex-1"
          />
          <button
            type="button"
            onClick={() => update('timestamp', new Date().toISOString())}
            className="inline-flex shrink-0 items-center rounded-lg border border-border bg-background px-2 text-[11px] font-medium text-foreground transition-colors hover:bg-muted"
          >
            {t('sidebar.now')}
          </button>
        </div>
      </Field>

      <details className="mt-3 rounded-lg border border-border bg-secondary/40 p-2">
        <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-foreground">
          {t('sidebar.tracefixSetup')}
          <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden="true" />
        </summary>
        <div className="mt-3 flex flex-col gap-2">
          <div className="grid grid-cols-2 gap-2">
            <Field label={t('sidebar.provider')}>
              <select
                value={config.tracefixProvider}
                onChange={(event) => update('tracefixProvider', event.target.value as RuntimeConfig['tracefixProvider'])}
                className="form-field"
              >
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="openrouter">OpenRouter</option>
                <option value="local">Local</option>
              </select>
            </Field>
            <Field label={t('sidebar.model')} icon={Cpu}>
              <input
                value={config.tracefixModel}
                onChange={(event) => update('tracefixModel', event.target.value)}
                className="form-field"
              />
            </Field>
          </div>
          <Field
            label={t('sidebar.tracefixKey')}
            icon={KeyRound}
            status={hasTracefixKey ? t('sidebar.detected') : t('sidebar.missing')}
            detected={hasTracefixKey}
          >
            <ApiKeyInput
              name="tracefix-api-key"
              value={config.tracefixApiKey}
              onValueChange={(value) => update('tracefixApiKey', value)}
              placeholder={t('sidebar.pasteTracefixKey')}
              policy={originPolicy}
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="CityOS agent provider">
              <select
                value={config.cityosAgentProvider}
                onChange={(event) => update('cityosAgentProvider', event.target.value as RuntimeConfig['cityosAgentProvider'])}
                className="form-field"
              >
                <option value="local">Local / Ollama</option>
                <option value="openrouter">OpenRouter</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </Field>
            <Field label="CityOS agent model" icon={Cpu}>
              <input
                value={config.cityosAgentModel}
                onChange={(event) => update('cityosAgentModel', event.target.value)}
                placeholder="gemma3:4b"
                className="form-field"
              />
            </Field>
          </div>
          <Field
            label="CityOS agent API key"
            icon={KeyRound}
            status={config.cityosAgentProvider === 'local' ? 'Not required' : hasCityosAgentKey ? 'Detected' : 'Missing'}
          >
            <ApiKeyInput
              name="cityos-agent-api-key"
              value={config.cityosAgentApiKey}
              onValueChange={(value) => update('cityosAgentApiKey', value)}
              placeholder="Paste CityOS agent provider key"
              policy={originPolicy}
              disabled={config.cityosAgentProvider === 'local'}
            />
          </Field>
          <p className="text-[10px] leading-relaxed text-muted-foreground">
            Generated CityOS retrieval, answer, and monitor agents use this provider and model independently from TraceFix.
          </p>
        </div>
      </details>

      <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
        {originPolicy.message} {t('sidebar.keyNote')}
      </p>
    </section>
  )
}

function Field({
  label,
  icon: Icon,
  status,
  detected,
  children,
}: {
  label: string
  icon?: typeof KeyRound
  status?: string
  detected?: boolean
  children: React.ReactNode
}) {
  return (
    <label className="mt-2 block">
      <span className="mb-1 flex items-center justify-between gap-2 text-[11px] font-semibold text-foreground">
        <span className="inline-flex min-w-0 items-center gap-1.5">
          {Icon && <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />}
          {label}
        </span>
        {status && (
          <span className={cn(
            'rounded-full border px-1.5 py-0.5 text-[9px] font-medium',
            detected
              ? 'border-primary/30 bg-accent text-accent-foreground'
              : 'border-border bg-background text-muted-foreground',
          )}>
            {status}
          </span>
        )}
      </span>
      {children}
    </label>
  )
}

function ConversationGroup({
  label,
  conversations,
  activeConversationId,
  renamingId,
  renameValue,
  onSelectConversation,
  onStartRename,
  onRenameValueChange,
  onCommitRename,
  onCancelRename,
  onTogglePin,
  onDeleteConversation,
  language,
}: Omit<ConversationSidebarProps, 'conversations' | 'search' | 'onSearchChange' | 'onNewChat' | 'runtimeConfig' | 'onRuntimeConfigChange'> & {
  label: string
  conversations: ConversationSummary[]
}) {
  const t = (key: TranslationKey) => translate(language, key)

  return (
    <section className="flex flex-col gap-1.5">
      <p className="px-1 text-[10px] font-semibold uppercase text-muted-foreground">
        {label}
      </p>
      {conversations.length === 0 ? (
        <p className="px-1 py-2 text-xs text-muted-foreground">{t('sidebar.noChats')}</p>
      ) : (
        conversations.map((conversation) => (
          <div
            key={conversation.id}
            className={cn(
              'group rounded-lg border border-transparent p-2 transition-colors hover:border-border hover:bg-background',
              conversation.id === activeConversationId && 'border-border bg-background',
            )}
          >
            {renamingId === conversation.id ? (
              <div className="flex items-center gap-1">
                <input
                  value={renameValue}
                  onChange={(event) => onRenameValueChange(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') onCommitRename(conversation.id)
                    if (event.key === 'Escape') onCancelRename()
                  }}
                  className="min-w-0 flex-1 rounded-md border border-border bg-card px-2 py-1 text-xs outline-none focus:border-ring"
                  autoFocus
                />
                <IconButton
                  label={t('sidebar.saveName')}
                  onClick={() => onCommitRename(conversation.id)}
                >
                  <Check className="size-3.5" aria-hidden="true" />
                </IconButton>
                <IconButton label={t('sidebar.cancelRename')} onClick={onCancelRename}>
                  <X className="size-3.5" aria-hidden="true" />
                </IconButton>
              </div>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => onSelectConversation(conversation.id)}
                  className="block w-full text-left"
                >
                  <span className="line-clamp-2 text-sm font-medium text-foreground">
                    {conversation.title === 'New chat'
                      ? t('sidebar.newChat')
                      : conversation.title}
                  </span>
                  <span className="mt-1 block text-[11px] text-muted-foreground">
                    {formatTimestamp(conversation.updatedAt, language)} - {conversation.turnCount}{' '}
                    {t(conversation.turnCount === 1 ? 'sidebar.turn' : 'sidebar.turns')}
                  </span>
                </button>
                <div className="mt-2 flex items-center gap-1 opacity-100 lg:opacity-0 lg:transition-opacity lg:group-hover:opacity-100">
                  <IconButton
                    label={conversation.pinned ? t('sidebar.unpinChat') : t('sidebar.pinChat')}
                    onClick={() => onTogglePin(conversation.id)}
                  >
                    {conversation.pinned ? (
                      <PinOff className="size-3.5" aria-hidden="true" />
                    ) : (
                      <Pin className="size-3.5" aria-hidden="true" />
                    )}
                  </IconButton>
                  <IconButton
                    label={t('sidebar.renameChat')}
                    onClick={() => onStartRename(conversation.id, conversation.title)}
                  >
                    <Pencil className="size-3.5" aria-hidden="true" />
                  </IconButton>
                  <IconButton
                    label={t('sidebar.deleteChat')}
                    onClick={() => onDeleteConversation(conversation.id)}
                  >
                    <Trash2 className="size-3.5" aria-hidden="true" />
                  </IconButton>
                </div>
              </>
            )}
          </div>
        ))
      )}
    </section>
  )
}

function IconButton({
  label,
  onClick,
  children,
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      {children}
    </button>
  )
}

function formatTimestamp(value: string, language: LanguageMode) {
  const date = new Date(value)
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  const locale = LANGUAGE_LOCALES[language]

  if (sameDay) {
    return date.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' })
  }

  return date.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
}
