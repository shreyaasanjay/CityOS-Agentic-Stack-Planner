import { NextResponse } from 'next/server'

import type { Agent, EvidenceItem, QueryResult } from '@/lib/api/types'
import { getRequestApiKeyOriginPolicy } from '@/lib/security/api-key-origin'
import {
  asArray,
  asNumber,
  asObject,
  asString,
  type JsonObject,
  runnerJson,
} from '@/lib/api/server/runner'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

interface AnswerRequest {
  query?: string
  mirrorApiUrl?: string
  agentProvider?: 'openai' | 'anthropic' | 'openrouter' | 'local'
  agentModel?: string
  agentApiKey?: string
}

function safeLabel(value: string) {
  return value.replace(/[^a-z0-9 _-]/gi, '').replace(/\s+/g, ' ').trim().slice(0, 40)
}

function safeAnswerText(value: string) {
  const text = value.replace(/\s+/g, ' ').trim()
  if (!text) return ''
  if (/[a-z]:\\|\/users\/|source_data|framepath|localpath/i.test(text)) return ''
  return text.slice(0, 600)
}

function aggregateAnswer(query: string, answer: JsonObject): string {
  // TeLLMe presents the generated TraceFix agent's answer; it must not silently
  // replace that answer with a second, UI-authored interpretation of the data.
  const agentAnswer = safeAnswerText(
    asString(answer.answer) || asString(answer.chat_answer) || asString(answer.chatAnswer),
  )
  if (agentAnswer) return agentAnswer

  const cameras = asArray(answer.cameras).map(asObject)
  if (/\b(how many people|occupancy|occupied)\b/i.test(query) && cameras.length) {
    const peaks = cameras.map((camera) => asNumber(camera.peakPeople)).filter((value): value is number => value !== null)
    const latest = cameras.map((camera) => asNumber(camera.lastPeople)).filter((value): value is number => value !== null)
    if (peaks.length) {
      const peak = Math.max(...peaks)
      const latestValue = latest.length ? Math.max(...latest) : null
      return latestValue === null
        ? `The approved occupancy summary shows up to ${peak} ${peak === 1 ? 'person' : 'people'}.`
        : `The approved occupancy summary shows up to ${peak} ${peak === 1 ? 'person' : 'people'}, with ${latestValue} in the latest aggregate reading.`
    }
  }

  const counts = asObject(answer.requestedActivityCounts)
  const requested = asArray(answer.requestedActivities)
    .map(asString)
    .map(safeLabel)
    .filter(Boolean)
  const labels = requested.length ? requested : Object.keys(counts).map(safeLabel).filter(Boolean)
  const entries = labels
    .map((label) => [label, asNumber(counts[label])] as const)
    .filter((entry): entry is readonly [string, number] => entry[1] !== null)

  if (entries.length === 1) {
    const [label, count] = entries[0]
    return `${label.charAt(0).toUpperCase()}${label.slice(1)} appeared in ${count} approved activity ${count === 1 ? 'record' : 'records'}.`
  }
  if (entries.length > 1) {
    const summary = entries.map(([label, count]) => `${label}: ${count}`).join('; ')
    return `The approved sensor summary found these activity records: ${summary}. These aggregate counts do not identify people or establish that separate activities involved the same person.`
  }

  const backendText = cameras.length === 0 ? safeAnswerText(asString(answer.text)) : ''
  if (backendText) return backendText

  return 'The smart-room data was processed, but no privacy-safe aggregate answer was available for this request.'
}

function safeFinalResult(envelope: JsonObject, query: string, model: string): QueryResult {
  const data = asObject(envelope.data)
  const answer = asObject(data.web_data_answer)
  const cameras = asArray(answer.cameras)
  const reportedEvidenceCount = asNumber(answer.evidence_used_count)
  const evidenceCount = reportedEvidenceCount === null
    ? cameras.length
    : Math.max(0, Math.floor(reportedEvidenceCount))
  const evidence: EvidenceItem[] = Array.from({ length: evidenceCount }, (_, index) => ({
    id: `private-evidence-${index + 1}`,
    kind: 'sensor',
    title: 'Approved private sensor summary',
    sourceId: '',
    capturedAt: '',
    summary: '',
    confidence: 0,
  }))
  const agents: Agent[] = [
    {
      id: 'tracefix',
      name: 'TraceFix verifier',
      type: 'Verification service',
      role: 'Verified the workflow before smart-room access.',
      status: 'Verified',
    },
    {
      id: 'cityos',
      name: safeLabel(asString(answer.producer_agent)) || 'TraceFix answer agent',
      type: 'Generated runtime agent',
      role: 'Produced the final answer from approved evidence packets.',
      status: 'Complete',
    },
  ]
  const reportedConfidence = asNumber(answer.confidence)

  return {
    id: asString(envelope.run_id) || asString(data.query_id) || `tellme_${Date.now()}`,
    answer: aggregateAnswer(query, answer),
    keyPoints: [
      'TraceFix verification completed before smart-room access.',
      'Only an aggregate result is shown.',
      'Raw captures, identities, source names, timestamps, and file paths were withheld.',
    ],
    confidence: reportedConfidence === null ? null : Math.max(0, Math.min(1, reportedConfidence)),
    agents,
    evidence,
    guidelines: [],
    model: model || undefined,
    workflow: { requiresVerification: false },
    createdAt: new Date().toISOString(),
  }
}

export async function POST(request: Request) {
  let body: AnswerRequest
  try {
    body = await request.json() as AnswerRequest
  } catch {
    return NextResponse.json({ error: 'The answer request must be valid JSON.' }, { status: 400 })
  }

  const query = body.query?.trim() || ''
  if (!query) return NextResponse.json({ error: 'The original question is required.' }, { status: 400 })
  const agentProvider = body.agentProvider || 'local'
  const agentModel = body.agentModel?.trim() || 'gemma3:4b'
  const agentApiKey = body.agentApiKey?.trim() || ''
  if (agentProvider !== 'local' && !agentApiKey) {
    return NextResponse.json({ error: 'Add the API key used by the generated CityOS answer agent.' }, { status: 400 })
  }
  if (agentApiKey && !getRequestApiKeyOriginPolicy(request).canUseApiKeys) {
    return NextResponse.json({ error: getRequestApiKeyOriginPolicy(request).message }, { status: 403 })
  }

  let sourceUrl: URL
  try {
    sourceUrl = new URL(body.mirrorApiUrl || '')
    if (!['http:', 'https:'].includes(sourceUrl.protocol)) throw new Error('Unsupported protocol')
  } catch {
    return NextResponse.json({ error: 'Enter a valid smart-room API URL.' }, { status: 400 })
  }

  try {
    const currentCityos = await runnerJson('/api/cityos/current')
    const cityosData = asObject(currentCityos.payload.data)
    const cityosResult = asObject(cityosData.result)
    const manifestPath = asString(cityosResult.manifestPath)
    if (!currentCityos.response.ok || currentCityos.payload.ok !== true || !manifestPath) {
      return NextResponse.json({ error: 'The verified smart-room workflow is not ready.' }, { status: 502 })
    }

    const webRun = await runnerJson('/api/synth/run-web-data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        manifestPath,
        sourceUrl: sourceUrl.toString(),
        sourceMode: 'auto',
        timeoutSeconds: 30,
        question: query,
        agentProvider,
        agentModel,
        agentApiKey: agentApiKey || undefined,
      }),
    }, 180_000)
    if (!webRun.response.ok || webRun.payload.ok === false) {
      return NextResponse.json({ error: 'The smart-room service could not produce an answer.' }, { status: 502 })
    }

    const currentTellme = await runnerJson('/api/tellme/current')
    if (!currentTellme.response.ok || currentTellme.payload.ok !== true) {
      return NextResponse.json({ error: 'The final answer was not available.' }, { status: 502 })
    }
    return NextResponse.json(safeFinalResult(currentTellme.payload, query, agentModel))
  } catch {
    return NextResponse.json({ error: 'The smart-room answer service is unavailable.' }, { status: 502 })
  }
}
