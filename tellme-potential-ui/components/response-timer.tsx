'use client'

import { useEffect, useState } from 'react'
import { Timer } from 'lucide-react'

export function formatResponseTime(milliseconds: number) {
  const seconds = Math.max(0, milliseconds) / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`

  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.floor(seconds % 60)
  return `${minutes}m ${remainingSeconds.toString().padStart(2, '0')}s`
}

export function ResponseTimer({
  startedAt,
  label,
}: {
  startedAt: number
  label: string
}) {
  const [elapsedMs, setElapsedMs] = useState(() => Math.max(0, Date.now() - startedAt))

  useEffect(() => {
    const update = () => setElapsedMs(Math.max(0, Date.now() - startedAt))
    update()
    const interval = window.setInterval(update, 100)
    return () => window.clearInterval(interval)
  }, [startedAt])

  return (
    <span
      className="inline-flex min-w-[5.75rem] items-center justify-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium tabular-nums text-muted-foreground"
      aria-label={`${label} ${formatResponseTime(elapsedMs)}`}
    >
      <Timer className="size-3.5" aria-hidden="true" />
      {formatResponseTime(elapsedMs)}
    </span>
  )
}
