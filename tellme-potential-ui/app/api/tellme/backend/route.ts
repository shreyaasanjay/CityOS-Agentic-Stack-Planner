import { NextResponse } from 'next/server'

import { RUNNER_URL, runnerJson } from '@/lib/api/server/runner'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

export async function GET(request: Request) {
  const runId = new URL(request.url).searchParams.get('run')?.trim() || ''

  if (!runId) {
    try {
      const { response } = await runnerJson('/api/ui-info', {}, 10_000)
      if (!response.ok) {
        return NextResponse.json({ error: 'The local TraceFix service is unavailable.' }, { status: 502 })
      }
      return NextResponse.redirect(`${RUNNER_URL}/`)
    } catch {
      return NextResponse.json(
        { error: 'The local TraceFix service is unavailable.' },
        { status: 502 },
      )
    }
  }

  if (!/^[a-z0-9][a-z0-9_-]{5,80}$/i.test(runId)) {
    return NextResponse.json({ error: 'Invalid TraceFix run.' }, { status: 400 })
  }

  try {
    const { response } = await runnerJson(`/api/runs/${runId}`, {}, 10_000)
    if (!response.ok) {
      return NextResponse.json({ error: 'TraceFix run was not found.' }, { status: 404 })
    }

    const backendUrl = new URL(`${RUNNER_URL}/`)
    backendUrl.searchParams.set('run', runId)
    return NextResponse.redirect(backendUrl)
  } catch {
    return NextResponse.json(
      { error: 'The local TraceFix service is unavailable.' },
      { status: 502 },
    )
  }
}