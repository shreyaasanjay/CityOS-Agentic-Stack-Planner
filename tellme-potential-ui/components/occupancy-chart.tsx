'use client'

import { useMemo, useRef, useState } from 'react'
import { Table2, LineChart } from 'lucide-react'
import type { OccupancyPoint } from '@/lib/api/types'
import { translate, type LanguageMode, type TranslationKey } from '@/lib/i18n'
import { cn } from '@/lib/utils'

// Fixed drawing space; the SVG scales to its container via viewBox.
const WIDTH = 720
const HEIGHT = 200
const PAD = { top: 14, right: 14, bottom: 28, left: 34 }
const PLOT_W = WIDTH - PAD.left - PAD.right
const PLOT_H = HEIGHT - PAD.top - PAD.bottom

function formatOffset(seconds: number) {
  const whole = Math.max(0, Math.round(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** Evenly spaced whole-person ticks -- half a person is not a reading. */
function countTicks(maxCount: number, max = 5) {
  const step = [1, 2, 5, 10, 20, 50].find((candidate) => maxCount / candidate <= max)
    ?? Math.ceil(maxCount / max)
  const ticks: number[] = []
  for (let value = 0; value <= maxCount; value += step) ticks.push(value)
  return ticks
}

export function OccupancyChart({
  points,
  language,
}: {
  points: OccupancyPoint[]
  language: LanguageMode
}) {
  const t = (key: TranslationKey, values?: Record<string, string | number>) =>
    translate(language, key, values)
  const [asTable, setAsTable] = useState(false)
  const [hover, setHover] = useState<{ index: number; x: number; y: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const model = useMemo(() => {
    const sorted = [...points].sort((a, b) => a.t - b.t)
    const lastT = sorted.length ? sorted[sorted.length - 1].t : 0
    const span = lastT > 0 ? lastT : 1
    const peak = sorted.reduce((max, point) => Math.max(max, point.count), 0)
    // Always show a 0 baseline -- occupancy is a count, so a truncated axis would
    // exaggerate every change -- plus one person of headroom, otherwise a steady
    // count draws as a filled block pinned to the top edge.
    const yMax = Math.max(2, peak + 1)
    const x = (value: number) => PAD.left + (value / span) * PLOT_W
    const y = (value: number) => PAD.top + PLOT_H - (value / yMax) * PLOT_H
    return { sorted, span, peak, yMax, x, y, lastT }
  }, [points])

  const { sorted, peak, yMax, x, y } = model

  const { line, area } = useMemo(() => {
    if (!sorted.length) return { line: '', area: '' }
    // Step, not a smooth curve: the count holds at a value until the next
    // reading, and interpolating would invent fractional people in between.
    const segments: string[] = [`M ${x(sorted[0].t)} ${y(sorted[0].count)}`]
    for (let index = 1; index < sorted.length; index += 1) {
      segments.push(`L ${x(sorted[index].t)} ${y(sorted[index - 1].count)}`)
      segments.push(`L ${x(sorted[index].t)} ${y(sorted[index].count)}`)
    }
    const baseline = PAD.top + PLOT_H
    const lastX = x(sorted[sorted.length - 1].t)
    return {
      line: segments.join(' '),
      area: `${segments.join(' ')} L ${lastX} ${baseline} L ${x(sorted[0].t)} ${baseline} Z`,
    }
  }, [sorted, x, y])

  if (!sorted.length) {
    return (
      <p className="rounded-xl border border-dashed border-border bg-secondary/40 px-4 py-6 text-center text-[13px] text-muted-foreground">
        {t('chart.unavailable')}
      </p>
    )
  }

  function handlePointer(event: React.PointerEvent<SVGSVGElement>) {
    const svg = svgRef.current
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    // Map client px into the fixed viewBox space.
    const localX = ((event.clientX - rect.left) / rect.width) * WIDTH
    let nearest = 0
    for (let index = 1; index < sorted.length; index += 1) {
      if (Math.abs(x(sorted[index].t) - localX) < Math.abs(x(sorted[nearest].t) - localX)) {
        nearest = index
      }
    }
    setHover({ index: nearest, x: x(sorted[nearest].t), y: y(sorted[nearest].count) })
  }

  const active = hover ? sorted[hover.index] : null
  const summary = t('chart.summary', { peak, duration: formatOffset(model.lastT) })

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{t('chart.title')}</h3>
          <p className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">{summary}</p>
        </div>
        <button
          type="button"
          onClick={() => setAsTable((previous) => !previous)}
          aria-pressed={asTable}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
        >
          {asTable ? <LineChart className="size-3.5" aria-hidden="true" /> : <Table2 className="size-3.5" aria-hidden="true" />}
          {asTable ? t('chart.showChart') : t('chart.showTable')}
        </button>
      </div>

      {asTable ? (
        <div className="max-h-72 overflow-auto rounded-xl border border-border">
          <table className="w-full text-left text-[13px]">
            <caption className="sr-only">{t('chart.title')}</caption>
            <thead className="sticky top-0 bg-secondary/80 text-[11px] uppercase text-muted-foreground backdrop-blur">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">{t('chart.axisTime')}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t('chart.axisPeople')}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((point, index) => (
                <tr key={`${point.t}-${index}`} className="border-t border-border">
                  <td className="px-3 py-1.5 tabular-nums text-muted-foreground">{formatOffset(point.t)}</td>
                  <td className="px-3 py-1.5 tabular-nums font-medium text-foreground">{point.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative rounded-xl border border-border bg-card p-1">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            className="h-auto w-full touch-none"
            role="img"
            aria-label={`${t('chart.title')}. ${summary}`}
            onPointerMove={handlePointer}
            onPointerLeave={() => setHover(null)}
          >
            {countTicks(yMax).map((tick) => (
              <g key={tick}>
                <line
                  x1={PAD.left}
                  x2={WIDTH - PAD.right}
                  y1={y(tick)}
                  y2={y(tick)}
                  stroke="var(--border)"
                  strokeWidth={1}
                />
                <text
                  x={PAD.left - 8}
                  y={y(tick) + 3.5}
                  textAnchor="end"
                  className="fill-muted-foreground text-[10px] tabular-nums"
                >
                  {tick}
                </text>
              </g>
            ))}

            {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
              <text
                key={fraction}
                x={PAD.left + fraction * PLOT_W}
                y={HEIGHT - 8}
                textAnchor={fraction === 0 ? 'start' : fraction === 1 ? 'end' : 'middle'}
                className="fill-muted-foreground text-[10px] tabular-nums"
              >
                {formatOffset(model.lastT * fraction)}
              </text>
            ))}

            <path d={area} fill="var(--chart-1)" opacity={0.12} />
            <path
              d={line}
              fill="none"
              stroke="var(--chart-1)"
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {hover && active && (
              <g>
                <line
                  x1={hover.x}
                  x2={hover.x}
                  y1={PAD.top}
                  y2={PAD.top + PLOT_H}
                  stroke="var(--muted-foreground)"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                />
                {/* Surface ring keeps the marker legible wherever it lands. */}
                <circle cx={hover.x} cy={hover.y} r={5.5} fill="var(--card)" />
                <circle cx={hover.x} cy={hover.y} r={4} fill="var(--chart-1)" />
              </g>
            )}
          </svg>

          {hover && active && (
            <div
              className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-lg border border-border bg-card px-2.5 py-1.5 text-[11px] shadow-md"
              style={{
                left: `${(hover.x / WIDTH) * 100}%`,
                top: `calc(${(hover.y / HEIGHT) * 100}% - 10px)`,
              }}
            >
              <p className="font-semibold tabular-nums text-foreground">
                {t(active.count === 1 ? 'chart.personCount' : 'chart.peopleCount', { count: active.count })}
              </p>
              <p className="tabular-nums text-muted-foreground">{formatOffset(active.t)}</p>
            </div>
          )}
        </div>
      )}

      <p className={cn('text-[11px] leading-relaxed text-muted-foreground')}>
        {t('chart.note')}
      </p>
    </div>
  )
}
