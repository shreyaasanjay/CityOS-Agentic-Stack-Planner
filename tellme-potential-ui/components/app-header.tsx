'use client'

import { ArrowUpRight, MessageSquareText, ListChecks, Layers, Server, Wifi } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  SettingsMenu,
  type AppearanceSettings,
} from '@/components/settings-menu'

export type WorkspaceTab = 'conversation' | 'guidelines'

interface AppHeaderProps {
  activeTab: WorkspaceTab
  onTabChange: (tab: WorkspaceTab) => void
  guidelineCount: number
  settings: AppearanceSettings
  onSettingsChange: (settings: AppearanceSettings) => void
}

export function AppHeader({
  activeTab,
  onTabChange,
  guidelineCount,
  settings,
  onSettingsChange,
}: AppHeaderProps) {
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <SettingsMenu settings={settings} onSettingsChange={onSettingsChange} />
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Layers className="size-4.5" aria-hidden="true" />
            </div>
            <div className="min-w-0 leading-tight">
              <div className="flex items-center gap-2">
                <p className="truncate text-sm font-semibold tracking-tight">TeLLMe</p>
                <span className="hidden items-center gap-1 rounded-full border border-primary/25 bg-accent px-1.5 py-0.5 text-[9px] font-semibold text-accent-foreground sm:inline-flex">
                  <Wifi className="size-2.5" aria-hidden="true" />
                  Smart room connected
                </span>
              </div>
              <p className="hidden text-[11px] text-muted-foreground sm:block">
                Grounded answers from city sensors
              </p>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <BackendLink />
          <nav
            aria-label="Workspace views"
            className="flex items-center gap-1 rounded-full border border-border bg-card p-1"
          >
            <TabButton
              active={activeTab === 'conversation'}
              onClick={() => onTabChange('conversation')}
            >
              <MessageSquareText className="size-3.5" aria-hidden="true" />
              <span className="hidden sm:inline">Conversation</span>
            </TabButton>
            <TabButton
              active={activeTab === 'guidelines'}
              onClick={() => onTabChange('guidelines')}
            >
              <ListChecks className="size-3.5" aria-hidden="true" />
              <span className="hidden sm:inline">Guidelines</span>
              {guidelineCount > 0 && (
                <span
                  className={cn(
                    'ml-0.5 inline-flex min-w-4.5 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums',
                    activeTab === 'guidelines'
                      ? 'bg-primary-foreground/20 text-primary-foreground'
                      : 'bg-secondary text-muted-foreground',
                  )}
                >
                  {guidelineCount}
                </span>
              )}
            </TabButton>
          </nav>
        </div>
      </div>
    </header>
  )
}

function BackendLink() {
  return (
    <a
      href="/api/tellme/backend"
      target="_blank"
      rel="noreferrer"
      title="Open the local TraceFix runner and intent decomposition view"
      className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted sm:px-3"
    >
      <Server className="size-3.5" aria-hidden="true" />
      <span className="hidden sm:inline">TraceFix</span>
      <ArrowUpRight className="size-3.5" aria-hidden="true" />
    </a>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'inline-flex min-h-8 items-center gap-1.5 rounded-full px-2.5 py-1.5 text-xs font-medium transition-colors sm:px-3',
        active
          ? 'bg-primary text-primary-foreground'
          : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}