/**
 * Shared API contract for the Groundwork frontend.
 *
 * These types define the shape of data the UI expects. As long as your real
 * backend returns objects matching `QueryResult`, the entire UI works
 * unchanged. Swap the implementation in `lib/api/client.ts` -- nothing in the
 * components imports the mock directly.
 */

/**
 * Privacy note: TeLLMe may use private evidence to verify an answer, but the
 * resident-facing UI must not display raw media, direct sensor readings,
 * source identifiers, timestamps, precise locations, or identifying details.
 */
export type EvidenceKind = 'camera' | 'sensor' | 'document'

/** A single extensible label/value pair retained for backend-only evidence metadata. */
export interface EvidenceField {
  label: string
  value: string
}

export interface EvidenceItem {
  id: string
  kind: EvidenceKind
  title: string
  /** Backend-only source identifier. Do not render in resident-facing UI. */
  sourceId: string
  /** Backend-only capture timestamp. Do not render in resident-facing UI. */
  capturedAt: string
  /** Backend-only location. Do not render in resident-facing UI. */
  location?: string
  /** Backend-only redacted still URL. Do not render in resident-facing UI. */
  previewUrl?: string
  /** Backend-only evidence summary. Do not render in resident-facing UI. */
  summary: string
  /** Backend-only evidence confidence. Do not render per-source in resident-facing UI. */
  confidence: number
  /** Backend-only labels. Do not render in resident-facing UI. */
  tags?: string[]
  /** Backend-only metadata. Do not render in resident-facing UI. */
  fields?: EvidenceField[]
}

/** A sensor or system that contributed to producing the answer. */
export interface Agent {
  id: string
  /** Human-friendly name, e.g. "Street camera network". */
  name: string
  /** Category of sensor/system, e.g. "Camera", "Motion sensor". */
  type: string
  /** What this agent contributed, in plain language. Kept high-level. */
  role: string
  /** Short status, e.g. "2 sources scanned". */
  status?: string
}

export type GuidelineSeverity = 'required' | 'recommended' | 'optional'

export interface Guideline {
  id: string
  /** 1-based ordering used by downstream software. */
  index: number
  title: string
  detail: string
  category: string
  severity: GuidelineSeverity
}

export interface QueryResult {
  id: string
  /** The prominent, grounded natural-language answer. */
  answer: string
  /** Short bullet takeaways shown alongside the answer. */
  keyPoints: string[]
  /** Overall confidence in the answer, 0-1. */
  confidence: number | null
  /** Sensors / systems that were used to reach the answer. */
  agents: Agent[]
  /** Private evidence records backing the answer; display only non-sensitive receipt info. */
  evidence: EvidenceItem[]
  /** Machine-readable guidelines synthesized for downstream software. */
  guidelines: Guideline[]
  /** Model reported for the completed workflow, when available. */
  model?: string
  /** Internal UI workflow hint; not rendered as backend detail. */
  workflow?: {
    requiresVerification: boolean
  }
  createdAt: string
}

export interface QueryRequest {
  query: string
  spaceId: string
  timestamp?: string
  mode: 'llm' | 'deterministic'
  model: string
  mirrorApiUrl: string
  openaiApiKey?: string
  tracefixProvider: 'openai' | 'anthropic' | 'openrouter' | 'local'
  tracefixModel: string
  tracefixApiKey?: string
}

export type QueryProgressStage = 'planning' | 'verifying' | 'synthesizing' | 'answering'

export interface QueryProgress {
  stage: QueryProgressStage
  runId?: string
}

export interface QuerySubmitOptions {
  signal?: AbortSignal
  onProgress?: (progress: QueryProgress) => void
}

/** The single integration point your backend plugs into. */
export interface QueryApi {
  submitQuery(req: QueryRequest, options?: QuerySubmitOptions): Promise<QueryResult>
  stopQuery(runId: string): Promise<void>
}
