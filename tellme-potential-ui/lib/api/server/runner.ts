import { request as httpRequest } from 'node:http'
import { request as httpsRequest } from 'node:https'

export type JsonObject = Record<string, unknown>

const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8788'
const configuredRunnerUrl = (
  process.env.TELLME_BACKEND_URL?.trim()
  || process.env.TRACEFIX_RUNNER_URL?.trim()
)

export const RUNNER_URL = (configuredRunnerUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, '')

export function asObject(value: unknown): JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : {}
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

export function asString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

// Deliberately node:http rather than fetch. Synthesis and verification hold the
// connection open for many minutes before sending a single header, and undici's
// 300s headersTimeout kills those sockets no matter what AbortSignal we pass.
// Here timeoutMs is the only deadline.
function runnerRequest(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const url = new URL(`${RUNNER_URL}${path}`)
    const payload = typeof init.body === 'string' ? Buffer.from(init.body, 'utf8') : null
    const headers: Record<string, string> = { 'Cache-Control': 'no-store' }
    new Headers(init.headers).forEach((value, name) => { headers[name] = value })
    // The runner reads Content-Length and never chunked bodies, so the length
    // has to be explicit or it parses an empty request body.
    if (payload) headers['Content-Length'] = String(payload.byteLength)

    const send = url.protocol === 'https:' ? httpsRequest : httpRequest
    const request = send(url, { method: init.method || 'GET', headers }, (response) => {
      response.setEncoding('utf8')
      let body = ''
      response.on('data', (chunk: string) => { body += chunk })
      response.on('end', () => resolve({ status: response.statusCode ?? 502, body }))
      response.on('error', reject)
    })

    const deadline = setTimeout(() => {
      request.destroy(new Error(
        `TraceFix did not respond within ${Math.round(timeoutMs / 1000)}s for ${path}.`,
      ))
    }, timeoutMs)
    request.on('close', () => { clearTimeout(deadline) })
    request.on('error', reject)

    if (payload) request.write(payload)
    request.end()
  })
}

export async function runnerJson(
  path: string,
  init: RequestInit = {},
  timeoutMs = 180_000,
): Promise<{ response: Response; payload: JsonObject }> {
  const { status, body } = await runnerRequest(path, init, timeoutMs)
  const response = new Response(null, { status })
  if (!body.trim()) {
    throw new Error(`TraceFix returned an empty response for ${path}.`)
  }

  let payload: JsonObject
  try {
    const parsed: unknown = JSON.parse(body)
    if (!asObject(parsed)) {
      throw new Error("The response was not a JSON object.")
    }
    payload = parsed as JsonObject
  } catch {
    throw new Error(`TraceFix returned an invalid response for ${path}.`)
  }
  return { response, payload }
}