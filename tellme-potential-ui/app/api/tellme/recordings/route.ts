import { NextResponse } from 'next/server'

import type { QueryResult } from '@/lib/api/types'
import { asArray, asObject, asString, runnerJson } from '@/lib/api/server/runner'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

export async function POST(request: Request) {
  const body = await request.json().catch(() => null) as { query?: string; mirrorApiUrl?: string; model?: string; timestamp?: string } | null
  const query = body?.query?.trim() || ''
  if (!query) return NextResponse.json({ error: 'The original question is required.' }, { status: 400 })
  let sourceUrl: URL
  try {
    sourceUrl = new URL(body?.mirrorApiUrl || '')
    if (!['http:', 'https:'].includes(sourceUrl.protocol)) throw new Error('Unsupported protocol')
  } catch {
    return NextResponse.json({ error: 'Enter a valid smart-room API URL.' }, { status: 400 })
  }
  try {
    const preflight = await runnerJson('/api/synth/recording-preflight', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sourceUrl: sourceUrl.toString(), question: query, timestamp: body?.timestamp?.trim() || undefined }),
    }, 35_000)
    if (!preflight.response.ok || preflight.payload.ok !== true) {
      return NextResponse.json({ error: 'The smart-room recording list could not be loaded.' }, { status: 502 })
    }
    if (preflight.payload.needsClarification !== true) return NextResponse.json({ needsRecordingSelection: false })
    const answer = asObject(preflight.payload.answer)
    const candidates = asArray(answer.clarificationCandidates).map(asObject).map((candidate) => ({
      recordingId: asString(candidate.recordingId) || [asString(candidate.day), asString(candidate.rec)].filter(Boolean).join('/'),
      label: asString(candidate.label) || 'Available recording',
      detail: asString(candidate.detail) || undefined,
      dateLabel: asString(candidate.dateLabel) || undefined,
      timeLabel: asString(candidate.timeLabel) || undefined,
    })).filter((candidate) => candidate.recordingId)
    const result: QueryResult = {
      id: `tellme_recording_${Date.now()}`,
      answer: 'Choose the recording to analyze. Verification and agent generation will start only after you select one.',
      keyPoints: ['No agents have been created.', 'No recording content has been analyzed.'],
      confidence: null, agents: [], evidence: [], guidelines: [], model: body?.model?.trim() || undefined,
      workflow: { requiresVerification: false },
      recordingSelection: { prompt: asString(answer.clarificationPrompt) || 'Choose a recording to continue.', candidates },
      createdAt: new Date().toISOString(),
    }
    return NextResponse.json({ needsRecordingSelection: true, result })
  } catch {
    return NextResponse.json({ error: 'The smart-room recording list could not be loaded.' }, { status: 502 })
  }
}