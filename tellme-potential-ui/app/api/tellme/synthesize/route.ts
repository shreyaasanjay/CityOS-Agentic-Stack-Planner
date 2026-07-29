import { NextRequest, NextResponse } from 'next/server'

import { runnerJson } from '@/lib/api/server/runner'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

interface SynthesisRequest { provider?: string; model?: string; apiKey?: string }

export async function POST(request: NextRequest) {
  let body: SynthesisRequest
  try {
    body = await request.json() as SynthesisRequest
  } catch {
    return NextResponse.json({ error: 'The synthesis request must be valid JSON.' }, { status: 400 })
  }
  const provider = body.provider === 'openrouter' ? 'openrouter' : body.provider
  const key = body.apiKey?.trim() || ''
  const providerKey = provider === 'openrouter' ? { openrouterKey: key }
    : provider === 'openai' ? { openaiKey: key }
    : provider === 'anthropic' ? { anthropicKey: key } : {}
  try {
    const { response, payload } = await runnerJson('/api/cityos/synthesize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, model: body.model?.trim() || 'z-ai/glm-5.2', ...providerKey }),
    }, 900_000)
    if (!response.ok || payload.ok !== true) {
      const details = Array.isArray(payload.errors)
        ? payload.errors.filter((item): item is string => typeof item === 'string').join(' ')
        : ''
      return NextResponse.json(
        { error: details || 'The verified workflow could not be prepared for smart-room access.' },
        { status: 502 },
      )
    }
    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: 'agent synthesis was unavailable.' }, { status: 502 })
  }
}
