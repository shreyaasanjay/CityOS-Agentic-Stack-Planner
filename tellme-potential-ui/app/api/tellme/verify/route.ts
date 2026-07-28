import { NextResponse } from 'next/server'

import { asObject, asString, runnerJson } from '@/lib/api/server/runner'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

interface VerifyRequest {
  provider?: 'openai' | 'anthropic' | 'openrouter' | 'local'
  model?: string
  apiKey?: string
}

export async function POST(request: Request) {
  let body: VerifyRequest
  try {
    body = await request.json() as VerifyRequest
  } catch {
    return NextResponse.json({ error: 'The verification request must be valid JSON.' }, { status: 400 })
  }

  const uiProvider = body.provider || 'openrouter'
  if (!['openai', 'anthropic', 'openrouter', 'local'].includes(uiProvider)) {
    return NextResponse.json({ error: 'Choose a supported TraceFix provider.' }, { status: 400 })
  }
  const apiKey = body.apiKey?.trim() || ''
  if (uiProvider !== 'local' && !apiKey) {
    return NextResponse.json(
      { error: 'Add a TraceFix API key in Connection setup before running verification.' },
      { status: 400 },
    )
  }

  const provider = uiProvider === 'local' ? 'ollama' : uiProvider
  const providerKey = provider === 'openai'
    ? { openaiKey: apiKey }
    : provider === 'anthropic'
      ? { anthropicKey: apiKey }
      : provider === 'openrouter'
        ? { openrouterKey: apiKey }
        : {}

  try {
    const { response, payload } = await runnerJson('/api/tracefix/from-tellme', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider,
        model: body.model?.trim() || (provider === 'ollama' ? 'llama3.2:3b' : provider === 'openrouter' ? 'z-ai/glm-5.2' : 'gpt-5-mini'),
        ...providerKey,
        timeout: 1800,
        verbose: false,
      }),
    })
    const data = asObject(payload.data)
    const runId = asString(data.id) || asString(payload.run_id)
    if (!response.ok || payload.ok !== true || !runId) {
      return NextResponse.json({ error: 'TraceFix verification could not be started.' }, { status: 502 })
    }
    return NextResponse.json({ runId }, { status: 201 })
  } catch {
    return NextResponse.json({ error: 'The local TraceFix service is unavailable.' }, { status: 502 })
  }
}
