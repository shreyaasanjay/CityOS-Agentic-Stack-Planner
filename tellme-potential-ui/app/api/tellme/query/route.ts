import { NextRequest, NextResponse } from 'next/server'

import { asObject, asString, runnerJson } from '@/lib/api/server/runner'
import type { Agent, QueryRequest, QueryResult } from '@/lib/api/types'
import type { LanguageMode } from '@/lib/i18n'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

type JsonObject = Record<string, unknown>

const EXPLICIT_DATE = /\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,?\s+\d{2,4})?)\b/i
const RELATIVE_DATE = /\b(?:today|yesterday|tomorrow|last\s+(?:night|week|month)|this\s+(?:morning|afternoon|evening|week)|on\s+\w+day)\b/i

function needsRecordingDate(query: string, timestamp?: string) {
  return !timestamp?.trim() && !EXPLICIT_DATE.test(query) && !RELATIVE_DATE.test(query)
}

function dateFollowUp(query: string, model: string): QueryResult {
  return {
    id: `tellme_date_${Date.now()}`,
    answer: 'Which date should I check? I need a date before I can choose the exact smart-room recording for this request.',
    keyPoints: ['No recording was opened.', 'Reply with a date, for example: July 21, 2026.'],
    confidence: null,
    agents: [{ id: 'tellme', name: 'TeLLMe planner', type: 'Planning service', role: 'Requested the missing recording date before retrieval.', status: 'Waiting for date' }],
    evidence: [],
    guidelines: [],
    model,
    workflow: { requiresVerification: false },
    createdAt: new Date().toISOString(),
  }
}
function asConfidence(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.max(0, Math.min(1, value))
    : 0
}

function safeResult(
  envelope: JsonObject,
  requestedMode: QueryRequest['mode'],
  requestedModel: string,
  language: LanguageMode,
): QueryResult {
  const copy = PLANNER_COPY[language]
  const data = asObject(envelope.data)
  const route = asObject(data.route_decision)
  const privacy = asObject(data.privacy_guardrail)
  const answerPacket = asObject(data.answer_packet)
  const status = asString(data.status)
  const privacyStatus = asString(privacy.status)
  const requiresTracefix = route.requires_tracefix === true || status === 'needs_tracefix'

  const answer = asString(data.chat_answer)
    || (privacyStatus === 'blocked' || status === 'not_answerable'
      ? copy.blocked
      : requiresTracefix
        ? copy.ready
        : copy.processed)

  const agents: Agent[] = [
    {
      id: 'tellme',
      name: copy.plannerName,
      type: copy.plannerType,
      role: copy.plannerRole,
      status: privacyStatus === 'passed' ? copy.privacyPassed : copy.reviewComplete,
    },
  ]
  if (requiresTracefix) {
    agents.push({
      id: 'tracefix',
      name: copy.tracefixName,
      type: copy.tracefixType,
      role: copy.tracefixRole,
      status: copy.verificationRequired,
    })
  }

  return {
    id: asString(envelope.run_id) || asString(data.query_id) || `tellme_${Date.now()}`,
    answer,
    keyPoints: [
      privacyStatus === 'passed'
        ? copy.passedPoint
        : privacyStatus === 'blocked'
          ? copy.blockedPoint
          : copy.checkedPoint,
      requiresTracefix
        ? copy.notGeneratedPoint
        : copy.completedPoint,
    ],
    confidence: asConfidence(answerPacket.confidence),
    agents,
    evidence: [],
    guidelines: [],
    model: requestedMode === 'llm' ? requestedModel : 'Deterministic',
    workflow: { requiresVerification: requiresTracefix },
    createdAt: new Date().toISOString(),
  }
}

export async function POST(request: NextRequest) {
  let body: QueryRequest
  try {
    body = await request.json() as QueryRequest
  } catch {
    return NextResponse.json({ error: 'The request body must be valid JSON.' }, { status: 400 })
  }

  if (!body.query?.trim()) {
    return NextResponse.json({ error: 'Enter a question before submitting.' }, { status: 400 })
  }

  const pendingQuery = request.cookies.get('tellme_pending_recording_question')?.value || ''
  const dateReply = EXPLICIT_DATE.test(body.query) || RELATIVE_DATE.test(body.query) || Boolean(body.timestamp?.trim())
  const effectiveQuery = pendingQuery && dateReply ? `${pendingQuery} on ${body.query}` : body.query

  if (needsRecordingDate(effectiveQuery, body.timestamp)) {
    const response = NextResponse.json(dateFollowUp(effectiveQuery, body.model?.trim() || 'TeLLMe'), { status: 200 })
    response.cookies.set('tellme_pending_recording_question', effectiveQuery, { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 900 })
    return response
  }
  const mode: QueryRequest['mode'] = body.mode === 'deterministic' ? 'deterministic' : 'llm'
  const model = body.model?.trim() || 'gpt-4.1-mini'
  const language = normalizedLanguage(body.language)

  try {
    const { response: upstream, payload: envelope } = await runnerJson('/api/tellme/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: effectiveQuery.trim(),
        space_id: body.spaceId?.trim() || 'smart_room_1',
        timestamp: body.timestamp?.trim() || null,
        mode,
        model,
        api_key: mode === 'llm' ? body.openaiApiKey?.trim() || '' : undefined,
      }),
    })
    if (!upstream.ok || envelope.ok !== true) {
      const upstreamErrors = Array.isArray(envelope.errors)
        ? envelope.errors.map(asString).filter(Boolean)
        : []
      return NextResponse.json(
        { error: upstreamErrors[0] || 'TeLLMe could not process this request.' },
        { status: upstream.status >= 400 ? upstream.status : 502 },
      )
    }
    const response = NextResponse.json(safeResult(envelope, mode, model), { status: 201 })
    response.cookies.delete('tellme_pending_recording_question')
    return response
  } catch {
    return NextResponse.json(
      { error: 'The local TeLLMe service is unavailable.' },
      { status: 502 },
    )
  }
}
