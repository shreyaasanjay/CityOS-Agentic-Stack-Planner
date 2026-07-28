import { NextResponse } from 'next/server'

import { asObject, asString, runnerJson } from '@/lib/api/server/runner'
import type { Agent, QueryRequest, QueryResult } from '@/lib/api/types'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

type JsonObject = Record<string, unknown>

function asConfidence(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.max(0, Math.min(1, value))
    : 0
}

function safeResult(
  envelope: JsonObject,
  requestedMode: QueryRequest['mode'],
  requestedModel: string,
): QueryResult {
  const data = asObject(envelope.data)
  const route = asObject(data.route_decision)
  const privacy = asObject(data.privacy_guardrail)
  const answerPacket = asObject(data.answer_packet)
  const status = asString(data.status)
  const privacyStatus = asString(privacy.status)
  const requiresTracefix = route.requires_tracefix === true || status === 'needs_tracefix'

  const answer = asString(data.chat_answer)
    || (privacyStatus === 'blocked' || status === 'not_answerable'
      ? 'This request could not proceed because it did not pass the privacy guardrail.'
      : requiresTracefix
        ? 'Your request passed the privacy check and is ready for verification. A data-backed answer has not been generated yet.'
        : 'Your request was processed within the configured privacy boundary.')

  const agents: Agent[] = [
    {
      id: 'tellme',
      name: 'TeLLMe planner',
      type: 'Planning service',
      role: 'Scoped the request and applied privacy rules.',
      status: privacyStatus === 'passed' ? 'Privacy check passed' : 'Review complete',
    },
  ]
  if (requiresTracefix) {
    agents.push({
      id: 'tracefix',
      name: 'TraceFix verifier',
      type: 'Verification service',
      role: 'Will verify the generated task before a data-backed answer is returned.',
      status: 'Verification required',
    })
  }

  return {
    id: asString(envelope.run_id) || asString(data.query_id) || `tellme_${Date.now()}`,
    answer,
    keyPoints: [
      privacyStatus === 'passed'
        ? 'The request passed the privacy guardrail.'
        : privacyStatus === 'blocked'
          ? 'The privacy guardrail stopped this request.'
          : 'The request was checked against the privacy guardrail.',
      requiresTracefix
        ? 'A verified smart-room answer has not been generated yet.'
        : 'TeLLMe completed the available planning step.',
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

export async function POST(request: Request) {
  let body: QueryRequest
  try {
    body = await request.json() as QueryRequest
  } catch {
    return NextResponse.json({ error: 'The request body must be valid JSON.' }, { status: 400 })
  }

  if (!body.query?.trim()) {
    return NextResponse.json({ error: 'Enter a question before submitting.' }, { status: 400 })
  }

  const mode: QueryRequest['mode'] = body.mode === 'deterministic' ? 'deterministic' : 'llm'
  const model = body.model?.trim() || 'gpt-4.1-mini'

  try {
    const { response: upstream, payload: envelope } = await runnerJson('/api/tellme/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: body.query.trim(),
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
    return NextResponse.json(safeResult(envelope, mode, model), { status: 201 })
  } catch {
    return NextResponse.json(
      { error: 'The local TeLLMe service is unavailable.' },
      { status: 502 },
    )
  }
}
