'use client'

import { Pencil } from 'lucide-react'
import { translate, type LanguageMode } from '@/lib/i18n'

export function UserQuery({
  text,
  onEdit,
  language,
}: {
  text: string
  onEdit?: () => void
  language: LanguageMode
}) {
  return (
    <div className="flex justify-end">
      <div className="flex max-w-[85%] flex-col items-end gap-1.5 sm:max-w-[70%]">
        <div className="rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground">
          {text}
        </div>
        {onEdit && (
          <button
            type="button"
            onClick={onEdit}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <Pencil className="size-3" aria-hidden="true" />
            {translate(language, 'main.editPrompt')}
          </button>
        )}
      </div>
    </div>
  )
}
