import { cn } from '@/lib/utils'

type Block =
  | { type: 'paragraph'; text: string }
  | { type: 'list'; items: string[] }
  | { type: 'code'; language: string; code: string }
  | { type: 'table'; headers: string[]; rows: string[][] }

function parseMarkdown(markdown: string): Block[] {
  const lines = markdown.split(/\r?\n/)
  const blocks: Block[] = []
  let paragraph: string[] = []
  let listItems: string[] = []

  function flushParagraph() {
    if (paragraph.length === 0) return
    blocks.push({ type: 'paragraph', text: paragraph.join(' ') })
    paragraph = []
  }

  function flushList() {
    if (listItems.length === 0) return
    blocks.push({ type: 'list', items: listItems })
    listItems = []
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    const trimmed = line.trim()

    if (trimmed.startsWith('```')) {
      flushParagraph()
      flushList()
      const language = trimmed.slice(3).trim()
      const code: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        code.push(lines[i])
        i += 1
      }
      blocks.push({ type: 'code', language, code: code.join('\n') })
      continue
    }

    if (isTableStart(lines, i)) {
      flushParagraph()
      flushList()
      const headers = splitTableRow(lines[i])
      i += 2
      const rows: string[][] = []
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(splitTableRow(lines[i]))
        i += 1
      }
      i -= 1
      blocks.push({ type: 'table', headers, rows })
      continue
    }

    const listMatch = trimmed.match(/^[-*]\s+(.+)/)
    if (listMatch) {
      flushParagraph()
      listItems.push(listMatch[1])
      continue
    }

    if (trimmed === '') {
      flushParagraph()
      flushList()
      continue
    }

    flushList()
    paragraph.push(trimmed)
  }

  flushParagraph()
  flushList()
  return blocks
}

function isTableStart(lines: string[], index: number) {
  return isTableRow(lines[index]) && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1])
}

function isTableRow(line: string) {
  return line.includes('|') && splitTableRow(line).length > 1
}

function splitTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function inlineParts(text: string) {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean)
}

function InlineText({ text }: { text: string }) {
  return (
    <>
      {inlineParts(text).map((part, index) => {
        if (part.startsWith('`') && part.endsWith('`')) {
          return (
            <code
              key={`${part}-${index}`}
              className="rounded bg-secondary px-1 py-0.5 font-mono text-[0.9em] text-foreground"
            >
              {part.slice(1, -1)}
            </code>
          )
        }

        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>
        }

        return <span key={`${part}-${index}`}>{part}</span>
      })}
    </>
  )
}

export function MarkdownRenderer({
  content,
  className,
}: {
  content: string
  className?: string
}) {
  const blocks = parseMarkdown(content)

  if (blocks.length === 0) return null

  return (
    <div className={cn('flex flex-col gap-4', className)}>
      {blocks.map((block, index) => {
        if (block.type === 'paragraph') {
          return (
            <p key={index} className="leading-relaxed text-pretty">
              <InlineText text={block.text} />
            </p>
          )
        }

        if (block.type === 'list') {
          return (
            <ul key={index} className="flex list-disc flex-col gap-1.5 pl-5 text-sm leading-relaxed">
              {block.items.map((item) => (
                <li key={item}>
                  <InlineText text={item} />
                </li>
              ))}
            </ul>
          )
        }

        if (block.type === 'table') {
          return (
            <div key={index} className="overflow-x-auto rounded-xl border border-border">
              <table className="w-full min-w-[28rem] border-collapse text-left text-sm">
                <thead className="bg-secondary text-foreground">
                  <tr>
                    {block.headers.map((header) => (
                      <th key={header} className="border-b border-border px-3 py-2 font-semibold">
                        <InlineText text={header} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="border-t border-border odd:bg-background even:bg-secondary/30">
                      {row.map((cell, cellIndex) => (
                        <td key={`${rowIndex}-${cellIndex}`} className="px-3 py-2 align-top text-muted-foreground">
                          <InlineText text={cell} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }

        return (
          <div key={index} className="overflow-hidden rounded-xl border border-border bg-secondary/40">
            <div className="flex items-center justify-between border-b border-border px-3 py-1.5 text-[11px] font-medium text-muted-foreground">
              <span>{block.language || 'code'}</span>
            </div>
            <pre className="overflow-x-auto p-3 text-xs leading-relaxed">
              <code className="font-mono text-foreground">{block.code}</code>
            </pre>
          </div>
        )
      })}
    </div>
  )
}