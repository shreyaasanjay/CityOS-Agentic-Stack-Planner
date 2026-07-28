import { NextResponse } from 'next/server'

import { asString, runnerJson } from '@/lib/api/server/runner'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

type RouteContext = { params: Promise<{ runId: string }> }

async function safeRunId(context: RouteContext) {
  const { runId } = await context.params
  return /^[a-z0-9][a-z0-9_-]{5,80}$/i.test(runId) ? runId : ''
}

export async function GET(_request: Request, context: RouteContext) {
  const runId = await safeRunId(context)
  if (!runId) return NextResponse.json({ error: 'Invalid verification run.' }, { status: 400 })

  try {
    const { response, payload } = await runnerJson(`/api/runs/${runId}`)
    if (!response.ok) {
      return NextResponse.json({ error: 'Verification run was not found.' }, { status: 404 })
    }
    const status = asString(payload.status) || 'running'
    return NextResponse.json({
      status,
      completed: status === 'completed',
      failed: status === 'failed' || status === 'verification_incomplete',
    })
  } catch {
    return NextResponse.json({ error: 'Unable to read verification status.' }, { status: 502 })
  }
}

export async function DELETE(_request: Request, context: RouteContext) {
  const runId = await safeRunId(context)
  if (!runId) return NextResponse.json({ error: 'Invalid verification run.' }, { status: 400 })

  try {
    const { response } = await runnerJson(`/api/runs/${runId}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    if (!response.ok) {
      return NextResponse.json({ error: 'The verification run could not be stopped.' }, { status: 502 })
    }
    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: 'The local TraceFix service is unavailable.' }, { status: 502 })
  }
}
