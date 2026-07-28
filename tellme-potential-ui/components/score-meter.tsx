import { cn } from '@/lib/utils'

interface ScoreMeterProps {
  label: string
  /** value between 0 and 1 */
  value: number
  className?: string
}

/** A compact labeled progress bar used for confidence / relevance scores. */
export function ScoreMeter({ label, value, className }: ScoreMeterProps) {
  const pct = Math.round(Math.min(Math.max(value, 0), 1) * 100)
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <div className="flex items-center justify-between text-[11px] font-medium text-muted-foreground">
        <span>{label}</span>
        <span className="tabular-nums text-foreground">{pct}%</span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-secondary"
        role="progressbar"
        aria-label={label}
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
