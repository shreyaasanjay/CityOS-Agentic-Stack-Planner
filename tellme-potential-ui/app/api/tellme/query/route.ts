import { NextResponse } from 'next/server'

import { asObject, asString, runnerJson } from '@/lib/api/server/runner'
import type { Agent, QueryRequest, QueryResult } from '@/lib/api/types'
import type { LanguageMode } from '@/lib/i18n'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

type JsonObject = Record<string, unknown>

const PLANNER_COPY = {
  en: {
    blocked: 'This request could not proceed because it did not pass the privacy guardrail.',
    ready: 'Your request passed the privacy check and is ready for verification. A data-backed answer has not been generated yet.',
    processed: 'Your request was processed within the configured privacy boundary.',
    plannerName: 'TeLLMe planner',
    plannerType: 'Planning service',
    plannerRole: 'Scoped the request and applied privacy rules.',
    privacyPassed: 'Privacy check passed',
    reviewComplete: 'Review complete',
    tracefixName: 'TraceFix verifier',
    tracefixType: 'Verification service',
    tracefixRole: 'Will verify the generated task before a data-backed answer is returned.',
    verificationRequired: 'Verification required',
    passedPoint: 'The request passed the privacy guardrail.',
    blockedPoint: 'The privacy guardrail stopped this request.',
    checkedPoint: 'The request was checked against the privacy guardrail.',
    notGeneratedPoint: 'A verified smart-room answer has not been generated yet.',
    completedPoint: 'TeLLMe completed the available planning step.',
  },
  es: {
    blocked: 'Esta solicitud no pudo continuar porque no superó el control de privacidad.',
    ready: 'La solicitud superó el control de privacidad y está lista para verificación. Aún no se ha generado una respuesta basada en datos.',
    processed: 'La solicitud se procesó dentro del límite de privacidad configurado.',
    plannerName: 'Planificador TeLLMe',
    plannerType: 'Servicio de planificación',
    plannerRole: 'Limitó la solicitud y aplicó las reglas de privacidad.',
    privacyPassed: 'Control de privacidad aprobado',
    reviewComplete: 'Revisión completa',
    tracefixName: 'Verificador TraceFix',
    tracefixType: 'Servicio de verificación',
    tracefixRole: 'Verificará la tarea antes de devolver una respuesta basada en datos.',
    verificationRequired: 'Verificación necesaria',
    passedPoint: 'La solicitud superó el control de privacidad.',
    blockedPoint: 'El control de privacidad detuvo esta solicitud.',
    checkedPoint: 'La solicitud fue revisada según el control de privacidad.',
    notGeneratedPoint: 'Aún no se ha generado una respuesta verificada de la sala inteligente.',
    completedPoint: 'TeLLMe completó el paso de planificación disponible.',
  },
  hi: {
    blocked: 'यह अनुरोध गोपनीयता जांच पास नहीं कर सका, इसलिए आगे नहीं बढ़ा।',
    ready: 'अनुरोध ने गोपनीयता जांच पास कर ली है और सत्यापन के लिए तैयार है। डेटा-आधारित जवाब अभी नहीं बना है।',
    processed: 'अनुरोध को तय गोपनीयता सीमा के अंदर संसाधित किया गया।',
    plannerName: 'TeLLMe योजनाकार',
    plannerType: 'योजना सेवा',
    plannerRole: 'अनुरोध सीमित किया और गोपनीयता नियम लागू किए।',
    privacyPassed: 'गोपनीयता जांच पास',
    reviewComplete: 'समीक्षा पूरी',
    tracefixName: 'TraceFix सत्यापक',
    tracefixType: 'सत्यापन सेवा',
    tracefixRole: 'डेटा-आधारित जवाब से पहले बनाए गए कार्य को सत्यापित करेगा।',
    verificationRequired: 'सत्यापन आवश्यक',
    passedPoint: 'अनुरोध ने गोपनीयता जांच पास की।',
    blockedPoint: 'गोपनीयता जांच ने यह अनुरोध रोक दिया।',
    checkedPoint: 'अनुरोध की गोपनीयता नियमों के अनुसार जांच हुई।',
    notGeneratedPoint: 'सत्यापित स्मार्ट रूम जवाब अभी नहीं बना है।',
    completedPoint: 'TeLLMe ने उपलब्ध योजना चरण पूरा किया।',
  },
}

function normalizedLanguage(language: QueryRequest['language'] | undefined): LanguageMode {
  return language === 'es' || language === 'hi' ? language : 'en'
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
  const language = normalizedLanguage(body.language)

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
    return NextResponse.json(safeResult(envelope, mode, model, language), { status: 201 })
  } catch {
    return NextResponse.json(
      { error: 'The local TeLLMe service is unavailable.' },
      { status: 502 },
    )
  }
}
