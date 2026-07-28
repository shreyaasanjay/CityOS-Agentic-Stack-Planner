'use client'

import { useRef, useState } from 'react'
import { ArrowUp, FileUp, Paperclip, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'

interface QueryConsoleProps {
  onSubmit: (query: string) => void
  disabled?: boolean
}

export function QueryConsole({ onSubmit, disabled }: QueryConsoleProps) {
  const [value, setValue] = useState('')
  const [showAttachments, setShowAttachments] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  function submit() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSubmit(trimmed)
    setValue('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (
      e.key === 'Enter' &&
      !e.shiftKey &&
      !e.nativeEvent.isComposing &&
      e.keyCode !== 229
    ) {
      e.preventDefault()
      submit()
    }
  }

  function autoGrow(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setValue(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }

  return (
    <div className="rounded-2xl border border-border bg-card p-2 shadow-sm transition-colors focus-within:border-ring">
      {showAttachments && (
        <div className="mb-2 rounded-xl border border-dashed border-border bg-secondary/40 p-3 text-sm">
          <div className="flex flex-col items-center justify-center gap-2 py-3 text-center">
            <span className="flex size-9 items-center justify-center rounded-lg bg-background text-muted-foreground">
              <FileUp className="size-4.5" aria-hidden="true" />
            </span>
            <div>
              <p className="font-medium text-foreground">Upload document</p>
              <p className="text-[12px] text-muted-foreground">
                Drag files here when attachments are enabled.
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-1.5 text-[10px] text-muted-foreground">
              {['PDF', 'Word', 'Images', 'CSV', 'Excel'].map((type) => (
                <span key={type} className="rounded-full border border-border bg-background px-2 py-0.5">
                  {type}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      <textarea
        ref={textareaRef}
        value={value}
        onChange={autoGrow}
        onKeyDown={handleKeyDown}
        rows={1}
        placeholder="Ask TeLLMe what happened, where, or when... e.g. I lost my keys on Maple St this afternoon"
        aria-label="Query"
        className="max-h-[200px] w-full resize-none bg-transparent px-3 py-2 text-sm leading-relaxed text-foreground outline-none placeholder:text-muted-foreground"
      />
      <div className="flex items-center justify-between px-1 pt-1">
        <button
          type="button"
          aria-label="Attach context"
          onClick={() => setShowAttachments((value) => !value)}
          className={cn(
            'inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground',
            showAttachments && 'bg-muted text-foreground',
          )}
        >
          <Plus className="size-3.5" aria-hidden="true" />
          <Paperclip className="size-4" aria-hidden="true" />
          <span className="hidden sm:inline">Upload document</span>
        </button>
        <div className="flex items-center gap-2">
          <span className="hidden text-[11px] text-muted-foreground sm:inline">
            Enter to send - Shift + Enter for new line
          </span>
          <button
            type="button"
            onClick={submit}
            disabled={disabled || !value.trim()}
            aria-label="Send query"
            className={cn(
              'inline-flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-all hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40',
            )}
          >
            <ArrowUp className="size-4" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  )
}