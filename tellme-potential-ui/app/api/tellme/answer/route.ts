import { NextResponse } from 'next/server'

import type { Agent, EvidenceItem, QueryResult } from '@/lib/api/types'
import { getRequestApiKeyOriginPolicy } from '@/lib/security/api-key-origin'
import type { LanguageMode } from '@/lib/i18n'
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
  model?: string
  timestamp?: string
  recordingOverride?: { recordingId?: string; day?: string; rec?: string }
  language?: LanguageMode
}

function normalizedRecordingOverride(value: AnswerRequest['recordingOverride']) {
  if (!value) return undefined
  const recordingId = value.recordingId?.trim() || ''
  const parts = recordingId.split('/').map((part) => part.trim()).filter(Boolean)
  return {
    recordingId,
    day: value.day?.trim() || (parts.length >= 2 ? parts.at(-2) : ''),
    rec: value.rec?.trim() || (parts.length >= 2 ? parts.at(-1) : ''),
  }
}

const ANSWER_COPY = {
  en: {
    occupancyPeak: (peak: number) =>
      `The approved occupancy summary shows up to ${peak} ${peak === 1 ? 'person' : 'people'}.`,
    occupancyLatest: (peak: number, latest: number) =>
      `The approved occupancy summary shows up to ${peak} ${peak === 1 ? 'person' : 'people'}, with ${latest} in the latest aggregate reading.`,
    activityOne: (label: string, count: number) =>
      `${label.charAt(0).toUpperCase()}${label.slice(1)} appeared in ${count} approved activity ${count === 1 ? 'record' : 'records'}.`,
    activityMany: (summary: string) =>
      `The approved sensor summary found these activity records: ${summary}. These aggregate counts do not identify people or establish that separate activities involved the same person.`,
    unavailable: 'The smart-room data was processed, but no privacy-safe aggregate answer was available for this request.',
    evidenceTitle: 'Approved private sensor summary',
    tracefixName: 'TraceFix verifier',
    tracefixType: 'Verification service',
    tracefixRole: 'Verified the workflow before smart-room access.',
    verified: 'Verified',
    cityosName: 'Approved smart-room service',
    cityosType: 'Sensor service',
    cityosRole: 'Returned an aggregate result without exposing raw captures.',
    complete: 'Complete',
    keyPoints: [
      'TraceFix verification completed before smart-room access.',
      'Only an aggregate result is shown.',
      'Raw captures, identities, source names, timestamps, and file paths were withheld.',
    ],
  },
  es: {
    occupancyPeak: (peak: number) =>
      `El resumen de ocupación aprobado muestra hasta ${peak} ${peak === 1 ? 'persona' : 'personas'}.`,
    occupancyLatest: (peak: number, latest: number) =>
      `El resumen de ocupación aprobado muestra hasta ${peak} ${peak === 1 ? 'persona' : 'personas'}, con ${latest} en la lectura agregada más reciente.`,
    activityOne: (label: string, count: number) =>
      `${label.charAt(0).toUpperCase()}${label.slice(1)} apareció en ${count} ${count === 1 ? 'registro de actividad aprobado' : 'registros de actividad aprobados'}.`,
    activityMany: (summary: string) =>
      `El resumen de sensores aprobado encontró estos registros de actividad: ${summary}. Estos conteos agregados no identifican personas ni demuestran que actividades distintas correspondan a la misma persona.`,
    unavailable: 'Los datos de la sala inteligente fueron procesados, pero no hubo una respuesta agregada que protegiera la privacidad.',
    evidenceTitle: 'Resumen privado de sensores aprobado',
    tracefixName: 'Verificador TraceFix',
    tracefixType: 'Servicio de verificación',
    tracefixRole: 'Verificó el flujo antes de acceder a la sala inteligente.',
    verified: 'Verificado',
    cityosName: 'Servicio de sala inteligente aprobado',
    cityosType: 'Servicio de sensores',
    cityosRole: 'Devolvió un resultado agregado sin exponer capturas originales.',
    complete: 'Completo',
    keyPoints: [
      'TraceFix completó la verificación antes de acceder a la sala inteligente.',
      'Solo se muestra un resultado agregado.',
      'Se ocultaron capturas, identidades, nombres de fuentes, horas y rutas de archivos.',
    ],
  },
  hi: {
    occupancyPeak: (peak: number) =>
      `स्वीकृत उपस्थिति सारांश में अधिकतम ${peak} ${peak === 1 ? 'व्यक्ति' : 'लोग'} दिखे।`,
    occupancyLatest: (peak: number, latest: number) =>
      `स्वीकृत उपस्थिति सारांश में अधिकतम ${peak} ${peak === 1 ? 'व्यक्ति' : 'लोग'} दिखे और नवीनतम समेकित रीडिंग में ${latest} थे।`,
    activityOne: (label: string, count: number) =>
      `${label} ${count} स्वीकृत गतिविधि रिकॉर्ड में दिखाई दिया।`,
    activityMany: (summary: string) =>
      `स्वीकृत सेंसर सारांश में ये गतिविधि रिकॉर्ड मिले: ${summary}। ये समेकित गिनतियां लोगों की पहचान नहीं करतीं और यह साबित नहीं करतीं कि अलग गतिविधियां एक ही व्यक्ति की थीं।`,
    unavailable: 'स्मार्ट रूम डेटा संसाधित हुआ, लेकिन इस अनुरोध के लिए गोपनीयता-सुरक्षित समेकित जवाब उपलब्ध नहीं था।',
    evidenceTitle: 'स्वीकृत निजी सेंसर सारांश',
    tracefixName: 'TraceFix सत्यापक',
    tracefixType: 'सत्यापन सेवा',
    tracefixRole: 'स्मार्ट रूम पहुंच से पहले कार्यप्रवाह सत्यापित किया।',
    verified: 'सत्यापित',
    cityosName: 'स्वीकृत स्मार्ट रूम सेवा',
    cityosType: 'सेंसर सेवा',
    cityosRole: 'कच्ची रिकॉर्डिंग दिखाए बिना समेकित परिणाम दिया।',
    complete: 'पूर्ण',
    keyPoints: [
      'स्मार्ट रूम पहुंच से पहले TraceFix सत्यापन पूरा हुआ।',
      'केवल समेकित परिणाम दिखाया गया है।',
      'कच्ची रिकॉर्डिंग, पहचान, स्रोत नाम, टाइमस्टैम्प और फाइल पथ छिपाए गए हैं।',
    ],
  },
}

function normalizedLanguage(language: LanguageMode | undefined): LanguageMode {
  return language === 'es' || language === 'hi' ? language : 'en'
}

function safeLabel(value: string) {
  return value.replace(/[^a-z0-9 _-]/gi, '').replace(/\s+/g, ' ').trim().slice(0, 40)
}

function safeRecordingTimestampLabel(value: string, maxLength: number) {
  const text = value.replace(/\s+/g, ' ').trim()
  if (!text || text.length > maxLength) return undefined
  if (/https?:\/\/|[a-z]:\\|\/users\/|source_data|framepath|localpath|rec_\d|day_\d/i.test(text)) {
    return undefined
  }
  return text
}

function safeAnswerText(value: string) {
  const text = value.replace(/\s+/g, ' ').trim()
  if (!text) return ''
  if (/[a-z]:\\|\/users\/|source_data|framepath|localpath/i.test(text)) return ''
  return text.slice(0, 600)
}

function aggregateAnswer(
  query: string,
  answer: JsonObject,
  language: LanguageMode,
): string {
  const copy = ANSWER_COPY[language]
  // TeLLMe presents the generated TraceFix agent's answer; it must not silently
  // replace that answer with a second, UI-authored interpretation of the data.
  const agentAnswer = safeAnswerText(
    asString(answer.answer) || asString(answer.chat_answer) || asString(answer.chatAnswer),
  )
  if (agentAnswer) return agentAnswer
  const cameras = asArray(answer.cameras).map(asObject)
  const isOccupancyQuestion =
    /\b(how many people|occupancy|occupied)\b/i.test(query)
    || /\b(cuántas personas|cuantas personas|ocupación|ocupacion|ocupado|ocupada)\b/i.test(query)
    || /(कितने लोग|कितने व्यक्ति|उपस्थिति|लोगों की संख्या)/i.test(query)
  if (isOccupancyQuestion && cameras.length) {
    const latest = cameras.map((camera) => asNumber(camera.lastPeople)).filter((value): value is number => value !== null)
    if (latest.length) {
      const latestValue = Math.max(...latest)
      return `The latest available reading in the selected recording showed ${latestValue} ${latestValue === 1 ? 'person' : 'people'}.`
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
    return copy.activityOne(label, count)
  }
  if (entries.length > 1) {
    const summary = entries.map(([label, count]) => `${label}: ${count}`).join('; ')
    return copy.activityMany(summary)
  }

  const backendAnswer = safeAnswerText(
    asString(answer.answer) || asString(answer.chat_answer) || asString(answer.chatAnswer),
  )
  if (backendAnswer) return backendAnswer

  const backendText = cameras.length === 0 ? safeAnswerText(asString(answer.text)) : ''
  if (backendText) return backendText

  return copy.unavailable
}

function webRunFailure(webRun: JsonObject): string {
  const upstreamError = asString(webRun.error)
  if (upstreamError) return upstreamError
  const direct = asArray(webRun.errors).map(asString).find(Boolean)
  if (direct) return direct
  for (const run of asArray(webRun.runs).map(asObject)) {
    const error = asString(run.error)
    if (error) return `Generated ${asString(asObject(run.app).name) || 'agent'} failed: ${error}`
  }
  return 'The generated smart-room agent did not produce an answer.'
}
function selectionResult(webRun: JsonObject, model: string, language: LanguageMode): QueryResult {
  const answer = asObject(webRun.answer)
  const fallbackPrompt = language === 'es'
    ? 'Elige la grabación que mejor coincida con tu solicitud.'
    : language === 'hi'
      ? 'वह रिकॉर्डिंग चुनें जो आपके अनुरोध से सबसे अच्छी तरह मेल खाती है।'
      : 'Choose the recording that best matches your request.'
  const fallbackKeyPoint = language === 'es'
    ? 'Elige una grabación para continuar con la pregunta original.'
    : language === 'hi'
      ? 'मूल प्रश्न जारी रखने के लिए एक रिकॉर्डिंग चुनें।'
      : 'Choose one recording to continue with the original question.'
  const fallbackLabel = language === 'es'
    ? 'Grabación disponible'
    : language === 'hi'
      ? 'उपलब्ध रिकॉर्डिंग'
      : 'Available recording'
  const candidates = asArray(answer.clarificationCandidates)
    .map(asObject)
    .map((candidate, index) => ({
      recordingId: asString(candidate.recordingId) || [asString(candidate.day), asString(candidate.rec)].filter(Boolean).join('/'),
      label: `${fallbackLabel} ${index + 1}`,
      dateLabel: safeRecordingTimestampLabel(asString(candidate.dateLabel), 48),
      timeLabel: safeRecordingTimestampLabel(asString(candidate.timeLabel), 24),
    }))
    .filter((candidate) => Boolean(candidate.recordingId))
  return {
    id: `tellme_selection_${Date.now()}`,
    answer: fallbackPrompt,
    keyPoints: [fallbackKeyPoint],
    confidence: null,
    agents: [],
    evidence: [],
    guidelines: [],
    model: model || undefined,
    workflow: { requiresVerification: false },
    recordingSelection: {
      prompt: fallbackPrompt,
      candidates,
    },
    createdAt: new Date().toISOString(),
  }
}

function safeFinalResult(
  envelope: JsonObject,
  query: string,
  model: string,
  language: LanguageMode,
): QueryResult {
  const copy = ANSWER_COPY[language]
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
    title: copy.evidenceTitle,
    sourceId: '',
    capturedAt: '',
    summary: '',
    confidence: 0,
  }))
  const agents: Agent[] = [
    {
      id: 'tracefix',
      name: copy.tracefixName,
      type: copy.tracefixType,
      role: copy.tracefixRole,
      status: copy.verified,
    },
    {
      id: 'cityos',
      name: safeLabel(asString(answer.producer_agent)) || 'TraceFix answer agent',
      type: copy.cityosType,
      role: copy.cityosRole,
      status: copy.complete,
    },
  ]
  const reportedConfidence = asNumber(answer.confidence)

  return {
    id: asString(envelope.run_id) || asString(data.query_id) || `tellme_${Date.now()}`,
    answer: aggregateAnswer(query, answer, language),
    keyPoints: copy.keyPoints,
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
  const language = normalizedLanguage(body.language)
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
    if (
      sourceUrl.hostname === '172.16.60.239'
      && sourceUrl.port === '3000'
      && sourceUrl.pathname.replace(/\/+$/, '') === '/api'
    ) {
      sourceUrl.pathname = '/api/v1'
    }
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
        sourceMode: 'smartroom',
        timeoutSeconds: 30,
        question: query,
        agentProvider,
        agentModel,
        agentApiKey: agentApiKey || undefined,
        timestamp: body.timestamp?.trim() || undefined,
        recordingOverride: normalizedRecordingOverride(body.recordingOverride),
      }),
    }, 180_000)
    if (!webRun.response.ok || webRun.payload.ok === false) {
      return NextResponse.json({ error: webRunFailure(webRun.payload) }, { status: 502 })
    }
    if (asObject(webRun.payload.answer).needsClarification === true) {
      return NextResponse.json(
        selectionResult(webRun.payload, body.model?.trim() || '', language),
      )
    }

    const currentTellme = await runnerJson('/api/tellme/current')
    if (!currentTellme.response.ok || currentTellme.payload.ok !== true) {
      return NextResponse.json({ error: 'The final answer was not available.' }, { status: 502 })
    }
    return NextResponse.json(
      safeFinalResult(currentTellme.payload, query, agentModel || body.model?.trim() || '', language),
    )
  } catch {
    return NextResponse.json({ error: 'The smart-room answer service is unavailable.' }, { status: 502 })
  }
}
