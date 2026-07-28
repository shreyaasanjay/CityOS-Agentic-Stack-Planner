import type {
  QueryApi,
  QueryProgressStage,
  QueryRequest,
  QueryResult,
  QuerySubmitOptions,
} from './types'

interface VerificationStart {
  runId: string
}

interface VerificationStatus {
  status: string
  completed: boolean
  failed: boolean
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json() as T & { error?: string }
  if (!response.ok) {
    throw new Error(payload.error || 'The local workflow request failed.')
  }
  return payload
}

async function postJson<T>(url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  return readJson<T>(response)
}

function wait(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      window.clearTimeout(timer)
      reject(new DOMException('Request stopped', 'AbortError'))
    }, { once: true })
  })
}

function report(options: QuerySubmitOptions | undefined, stage: QueryProgressStage, runId?: string) {
  options?.onProgress?.({ stage, runId })
}

/** Browser client for the privacy-filtering Next.js proxy workflow. */
const httpQueryApi: QueryApi = {
  async submitQuery(req: QueryRequest, options?: QuerySubmitOptions): Promise<QueryResult> {
    report(options, 'planning')
    const plan = await postJson<QueryResult>('/api/tellme/query', req, options?.signal)
    if (!plan.workflow?.requiresVerification) return plan

    report(options, 'verifying')
    const verification = await postJson<VerificationStart>('/api/tellme/verify', {
      provider: req.tracefixProvider,
      model: req.tracefixModel,
      apiKey: req.tracefixApiKey?.trim()
        || (req.tracefixProvider === 'openai' ? req.openaiApiKey?.trim() : ''),
    }, options?.signal)
    report(options, 'verifying', verification.runId)

    const deadline = Date.now() + 30 * 60 * 1000
    while (Date.now() < deadline) {
      await wait(1200, options?.signal)
      const response = await fetch(`/api/tellme/verify/${verification.runId}`, {
        cache: 'no-store',
        signal: options?.signal,
      })
      const status = await readJson<VerificationStatus>(response)
      if (status.failed) throw new Error('TraceFix could not verify this request.')
      if (status.completed) break
    }
    if (Date.now() >= deadline) throw new Error('TraceFix verification timed out.')

    report(options, 'synthesizing', verification.runId)
    await postJson<{ ok: true }>('/api/tellme/synthesize', {}, options?.signal)

    report(options, 'answering', verification.runId)
    return postJson<QueryResult>('/api/tellme/answer', {
      query: req.query,
      mirrorApiUrl: req.mirrorApiUrl,
      model: req.tracefixModel,
    }, options?.signal)
  },

  async stopQuery(runId: string): Promise<void> {
    const response = await fetch(`/api/tellme/verify/${runId}`, { method: 'DELETE' })
    await readJson<{ ok: true }>(response)
  },
}

export const queryApi: QueryApi = httpQueryApi
