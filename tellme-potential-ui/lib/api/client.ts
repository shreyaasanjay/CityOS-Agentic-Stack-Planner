import type {
  QueryApi,
  QueryProgressStage,
  QueryRequest,
  QueryResult,
  QuerySubmitOptions,
} from './types'
import { getApiKeyOriginPolicy } from '@/lib/security/api-key-origin'

interface VerificationStart {
  runId: string
}

interface VerificationStatus {
  status: string
  completed: boolean
  failed: boolean
}

async function readJson<T>(response: Response): Promise<T> {
const body = await response.text()
  if (!body.trim()) {
    throw new Error('The local workflow service returned an empty response. Please try again.')
  }

  let payload: T & { error?: string }
  try {
    payload = JSON.parse(body) as T & { error?: string }
  } catch {
    throw new Error('The local workflow service returned an invalid response. Please try again.')
  }
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
    const originPolicy = getApiKeyOriginPolicy(window.location)
    if (!originPolicy.canUseApiKeys && (
      req.openaiApiKey?.trim()
      || req.tracefixApiKey?.trim()
      || req.cityosAgentApiKey?.trim()
    )) {
      throw new Error(originPolicy.message)
    }
    if (req.mode === 'llm' && !req.openaiApiKey?.trim()) {
      throw new Error('Add an OpenAI API key in Connection setup before submitting an LLM request.')
    }

    const verificationKey = req.tracefixApiKey?.trim()
      || (req.tracefixProvider === 'openai' ? req.openaiApiKey?.trim() : '')
    const cityosAgentKey = req.cityosAgentApiKey?.trim() || verificationKey
    report(options, 'planning')
    const plan = await postJson<QueryResult>('/api/tellme/query', req, options?.signal)
    if (!plan.workflow?.requiresVerification) return plan

    const recordingPreflight = await postJson<{ needsRecordingSelection: boolean; result?: QueryResult }>('/api/tellme/recordings', {
      query: req.query,
      mirrorApiUrl: req.mirrorApiUrl,
      model: req.tracefixModel,
      timestamp: req.timestamp,
    }, options?.signal)
    if (recordingPreflight.needsRecordingSelection && recordingPreflight.result) return recordingPreflight.result

    report(options, 'verifying')
    const verification = await postJson<VerificationStart>('/api/tellme/verify', {
      provider: req.tracefixProvider,
      model: req.tracefixModel,
      apiKey: verificationKey,
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
    await postJson<{ ok: true }>('/api/tellme/synthesize', {
      provider: req.tracefixProvider,
      model: req.tracefixModel,
      apiKey: req.tracefixApiKey?.trim()
        || (req.tracefixProvider === 'openai' ? req.openaiApiKey?.trim() : ''),
    }, options?.signal)

    report(options, 'answering', verification.runId)
    return postJson<QueryResult>('/api/tellme/answer', {
      query: req.query,
      mirrorApiUrl: req.mirrorApiUrl,
      agentProvider: req.cityosAgentProvider,
      agentModel: req.cityosAgentModel,
      agentApiKey: cityosAgentKey,
      model: req.tracefixModel,
      language: req.language,
    }, options?.signal)
  },

  async selectRecording(req: QueryRequest, recordingId: string, options?: QuerySubmitOptions): Promise<QueryResult> {
    report(options, 'verifying')
    const verification = await postJson<VerificationStart>('/api/tellme/verify', {
      provider: req.tracefixProvider,
      model: req.tracefixModel,
      apiKey: req.tracefixApiKey?.trim() || (req.tracefixProvider === 'openai' ? req.openaiApiKey?.trim() : ''),
    }, options?.signal)
    const deadline = Date.now() + 30 * 60 * 1000
    while (Date.now() < deadline) {
      await wait(1200, options?.signal)
      const response = await fetch(`/api/tellme/verify/${verification.runId}`, { cache: 'no-store', signal: options?.signal })
      const status = await readJson<VerificationStatus>(response)
      if (status.failed) throw new Error('TraceFix could not verify this request.')
      if (status.completed) break
    }
    if (Date.now() >= deadline) throw new Error('TraceFix verification timed out.')
    report(options, 'synthesizing', verification.runId)
    await postJson<{ ok: true }>('/api/tellme/synthesize', {
      provider: req.tracefixProvider, model: req.tracefixModel,
      apiKey: req.tracefixApiKey?.trim() || (req.tracefixProvider === 'openai' ? req.openaiApiKey?.trim() : ''),
    }, options?.signal)
    report(options, 'answering', verification.runId)
    return postJson<QueryResult>('/api/tellme/answer', {
      query: req.query,
      mirrorApiUrl: req.mirrorApiUrl,
      agentProvider: req.cityosAgentProvider,
      agentModel: req.cityosAgentModel,
      agentApiKey: req.cityosAgentApiKey?.trim()
        || req.tracefixApiKey?.trim()
        || (req.tracefixProvider === 'openai' ? req.openaiApiKey?.trim() : ''),
      model: req.tracefixModel,
      language: req.language,
      timestamp: req.timestamp,
      recordingOverride: { recordingId },
    }, options?.signal)
  },

  async stopQuery(runId: string): Promise<void> {
    const response = await fetch(`/api/tellme/verify/${runId}`, { method: 'DELETE' })
    await readJson<{ ok: true }>(response)
  },
}

export const queryApi: QueryApi = httpQueryApi
