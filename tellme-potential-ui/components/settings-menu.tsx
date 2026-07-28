'use client'

import { useState } from 'react'
import {
  Check,
  ChevronDown,
  Eye,
  EyeOff,
  Globe2,
  LogIn,
  Moon,
  Palette,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Sun,
  Type,
} from 'lucide-react'
import { cn } from '@/lib/utils'

export type ThemeMode = 'light' | 'dark'
export type TextScale = 'comfortable' | 'large'
export type DensityMode = 'standard' | 'compact'
export type AnswerStyle = 'concise' | 'detailed'
export type ThemeColor = 'green' | 'blue' | 'violet'
export type CitationStyle = 'inline' | 'summary'
export type LanguageMode = 'en' | 'es' | 'hi'

export interface AppearanceSettings {
  theme: ThemeMode
  themeColor: ThemeColor
  textScale: TextScale
  density: DensityMode
  highContrast: boolean
  reduceMotion: boolean
  streamingEnabled: boolean
  answerStyle: AnswerStyle
  citationStyle: CitationStyle
  language: LanguageMode
}

interface SettingsMenuProps {
  settings: AppearanceSettings
  onSettingsChange: (settings: AppearanceSettings) => void
}

function updateSetting<K extends keyof AppearanceSettings>(
  settings: AppearanceSettings,
  key: K,
  value: AppearanceSettings[K],
) {
  return { ...settings, [key]: value }
}

export function SettingsMenu({ settings, onSettingsChange }: SettingsMenuProps) {
  const [open, setOpen] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  function setSetting<K extends keyof AppearanceSettings>(
    key: K,
    value: AppearanceSettings[K],
  ) {
    onSettingsChange(updateSetting(settings, key, value))
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label="Open settings"
        className="inline-flex size-9 items-center justify-center rounded-lg border border-border bg-card text-foreground shadow-sm transition-colors hover:bg-muted"
      >
        <Settings className="size-4.5" aria-hidden="true" />
      </button>

      {open && (
        <div className="absolute left-0 top-11 z-50 max-h-[calc(100vh-5rem)] w-[min(22rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-border bg-popover p-2 text-popover-foreground shadow-xl">
          <div className="flex items-center gap-3 rounded-lg bg-secondary/70 p-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <LogIn className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">User access</p>
              <p className="text-[12px] leading-relaxed text-muted-foreground">
                Sign in support can connect here when accounts are ready.
              </p>
            </div>
            <button
              type="button"
              className="inline-flex shrink-0 items-center justify-center rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              Sign in
            </button>
          </div>

          <div className="mt-2 flex flex-col gap-1">
            <MenuSectionLabel>Common</MenuSectionLabel>
            <SegmentedSetting
              icon={settings.theme === 'dark' ? Moon : Sun}
              label="Dark mode"
              options={[
                { label: 'Light', value: 'light' as const },
                { label: 'Dark', value: 'dark' as const },
              ]}
              value={settings.theme}
              onChange={(value) => setSetting('theme', value)}
            />
            <SegmentedSetting
              icon={Palette}
              label="Theme color"
              options={[
                { label: 'Green', value: 'green' as const },
                { label: 'Blue', value: 'blue' as const },
                { label: 'Violet', value: 'violet' as const },
              ]}
              value={settings.themeColor}
              onChange={(value) => setSetting('themeColor', value)}
              columns={3}
            />
            <SegmentedSetting
              icon={Type}
              label="Font size"
              options={[
                { label: 'Comfort', value: 'comfortable' as const },
                { label: 'Large', value: 'large' as const },
              ]}
              value={settings.textScale}
              onChange={(value) => setSetting('textScale', value)}
            />
            <ToggleSetting
              icon={Sparkles}
              label="Streaming"
              description="Show answers as they are generated."
              checked={settings.streamingEnabled}
              onChange={(checked) => setSetting('streamingEnabled', checked)}
            />
          </div>

          <button
            type="button"
            onClick={() => setAdvancedOpen((value) => !value)}
            className="mt-2 flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-sm font-medium transition-colors hover:bg-muted"
          >
            Advanced settings
            <ChevronDown
              className={cn('size-4 transition-transform', advancedOpen && 'rotate-180')}
              aria-hidden="true"
            />
          </button>

          {advancedOpen && (
            <div className="flex flex-col gap-1 border-t border-border pt-2">
              <SegmentedSetting
                icon={Globe2}
                label="Language"
                options={[
                  { label: 'English', value: 'en' as const },
                  { label: 'Spanish', value: 'es' as const },
                  { label: 'Hindi', value: 'hi' as const },
                ]}
                value={settings.language}
                onChange={(value) => setSetting('language', value)}
                columns={3}
              />
              <SegmentedSetting
                icon={ShieldCheck}
                label="Citation style"
                options={[
                  { label: 'Inline', value: 'inline' as const },
                  { label: 'Summary', value: 'summary' as const },
                ]}
                value={settings.citationStyle}
                onChange={(value) => setSetting('citationStyle', value)}
              />
              <SegmentedSetting
                icon={SlidersHorizontal}
                label="Spacing"
                options={[
                  { label: 'Standard', value: 'standard' as const },
                  { label: 'Compact', value: 'compact' as const },
                ]}
                value={settings.density}
                onChange={(value) => setSetting('density', value)}
              />
              <ToggleSetting
                icon={Eye}
                label="High contrast"
                description="Increase visual separation for controls and panels."
                checked={settings.highContrast}
                onChange={(checked) => setSetting('highContrast', checked)}
              />
              <ToggleSetting
                icon={EyeOff}
                label="Reduced motion"
                description="Limit animations and smooth scrolling."
                checked={settings.reduceMotion}
                onChange={(checked) => setSetting('reduceMotion', checked)}
              />
              <SegmentedSetting
                icon={SlidersHorizontal}
                label="Answer style"
                options={[
                  { label: 'Concise', value: 'concise' as const },
                  { label: 'Detailed', value: 'detailed' as const },
                ]}
                value={settings.answerStyle}
                onChange={(value) => setSetting('answerStyle', value)}
              />
              <div className="flex items-start gap-3 rounded-lg bg-secondary/60 px-2.5 py-2">
                <ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
                <div>
                  <p className="text-sm font-medium">Evidence privacy locked</p>
                  <p className="text-[12px] leading-relaxed text-muted-foreground">
                    Raw captures and precise source details stay hidden for every user.
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function MenuSectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-2.5 py-1 text-[10px] font-semibold uppercase text-muted-foreground">
      {children}
    </p>
  )
}

function SegmentedSetting<T extends string>({
  icon: Icon,
  label,
  options,
  value,
  onChange,
  columns = 2,
}: {
  icon: typeof Settings
  label: string
  options: { label: string; value: T }[]
  value: T
  onChange: (value: T) => void
  columns?: 2 | 3
}) {
  return (
    <div className="rounded-lg px-2.5 py-2 hover:bg-muted/60">
      <div className="mb-2 flex items-center gap-2 text-sm font-medium">
        <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
        {label}
      </div>
      <div
        className={cn(
          'grid gap-1 rounded-lg border border-border bg-background p-1',
          columns === 3 ? 'grid-cols-3' : 'grid-cols-2',
        )}
      >
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={cn(
              'inline-flex min-h-8 items-center justify-center gap-1.5 rounded-md px-2 text-xs font-medium transition-colors',
              value === option.value
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground',
            )}
          >
            {value === option.value && <Check className="size-3" aria-hidden="true" />}
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function ToggleSetting({
  icon: Icon,
  label,
  description,
  checked,
  onChange,
}: {
  icon: typeof Settings
  label: string
  description: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-lg px-2.5 py-2 transition-colors hover:bg-muted/60">
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{label}</span>
        <span className="block text-[12px] leading-relaxed text-muted-foreground">
          {description}
        </span>
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 size-4 accent-primary"
      />
    </label>
  )
}