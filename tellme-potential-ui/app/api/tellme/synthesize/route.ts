import { NextResponse } from 'next/server'

import { runnerJson } from '@/lib/api/server/runner'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

export async function POST() {
  try {
    const { response, payload } = await runnerJson('/api/cityos/synthesize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }, 300_000)
    if (!response.ok || payload.ok !== true) {
      return NextResponse.json(
        { error: 'The verified workflow could not be prepared for smart-room access.' },
        { status: 502 },
      )
    }
    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: 'CityOS synthesis was unavailable.' }, { status: 502 })
  }
}
