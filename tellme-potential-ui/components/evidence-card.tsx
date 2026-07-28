'use client'

import { Camera, RadioTower, FileText, ShieldCheck, EyeOff } from 'lucide-react'
import type { EvidenceItem, EvidenceKind } from '@/lib/api/types'

const KIND_META: Record<
  EvidenceKind,
  { label: string; icon: typeof Camera }
> = {
  camera: { label: 'Camera signal', icon: Camera },
  sensor: { label: 'Sensor signal', icon: RadioTower },
  document: { label: 'Document signal', icon: FileText },
}

export function EvidenceCard({ item }: { item: EvidenceItem }) {
  const meta = KIND_META[item.kind]
  const Icon = meta.icon

  return (
    <article className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
          <Icon className="size-5" aria-hidden="true" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold leading-snug">{meta.label}</h4>
            <span className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-accent px-2 py-0.5 text-[10px] font-medium text-accent-foreground">
              <ShieldCheck className="size-3" aria-hidden="true" />
              Verified privately
            </span>
          </div>
          <p className="text-[13px] leading-relaxed text-muted-foreground">
            This source helped verify the answer, but captured media, raw sensor
            readings, locations, timestamps, device IDs, and identifying details
            are not displayed in the resident app.
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2 rounded-lg border border-dashed border-border bg-secondary/50 px-3 py-2 text-[11px] text-muted-foreground">
        <EyeOff className="size-3.5 shrink-0" aria-hidden="true" />
        Evidence details withheld to protect resident privacy.
      </div>
    </article>
  )
}
