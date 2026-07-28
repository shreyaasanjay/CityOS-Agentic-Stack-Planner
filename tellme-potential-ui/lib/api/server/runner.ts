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

export async function runnerJson(
  path: string,
  init: RequestInit = {},
  timeoutMs = 180_000,
): Promise<{ response: Response; payload: JsonObject }> {
  const response = await fetch(`${RUNNER_URL}${path}`, {
    ...init,
    cache: 'no-store',
    signal: AbortSignal.timeout(timeoutMs),
  })
  const payload = await response.json() as JsonObject
  return { response, payload }
}