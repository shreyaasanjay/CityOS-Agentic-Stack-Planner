export interface ApiKeyOriginPolicy {
  canUseApiKeys: boolean
  hostname: string
  protocol: string
  label: string
  message: string
}

interface OriginLike {
  hostname: string
  protocol: string
}

const LOCAL_HOSTNAMES = new Set(['localhost', '127.0.0.1', '::1'])

/** Central security policy for browser-supplied provider credentials. */
export function getApiKeyOriginPolicy(origin: OriginLike): ApiKeyOriginPolicy {
  const hostname = origin.hostname.toLowerCase()
  const protocol = origin.protocol.toLowerCase()
  const isLocalhost = LOCAL_HOSTNAMES.has(hostname)
  const isHttps = protocol === 'https:'
  const canUseApiKeys = isLocalhost || isHttps

  return {
    canUseApiKeys,
    hostname,
    protocol,
    label: isLocalhost ? 'Localhost' : isHttps ? 'Secure HTTPS' : 'Blocked origin',
    message: canUseApiKeys
      ? isLocalhost
        ? 'Provider keys are allowed for local development and remain in memory only.'
        : 'Provider keys are allowed on this encrypted origin and remain in memory only.'
      : 'API keys are blocked on non-local HTTP. Use HTTPS or open the app on localhost.',
  }
}

export function getRequestApiKeyOriginPolicy(request: Request): ApiKeyOriginPolicy {
  const originHeader = request.headers.get('origin')?.trim()
  if (originHeader) {
    try {
      return getApiKeyOriginPolicy(new URL(originHeader))
    } catch {
      return getApiKeyOriginPolicy({ hostname: '', protocol: '' })
    }
  }

  const forwardedProtocol = request.headers.get('x-forwarded-proto')?.split(',')[0].trim()
  const forwardedHost = request.headers.get('x-forwarded-host')?.split(',')[0].trim()
    || request.headers.get('host')?.split(',')[0].trim()
    || ''
  const hostname = forwardedHost.startsWith('[')
    ? forwardedHost.slice(1, forwardedHost.indexOf(']'))
    : forwardedHost.split(':')[0]

  return getApiKeyOriginPolicy({
    hostname,
    protocol: forwardedProtocol ? `${forwardedProtocol.replace(/:$/, '')}:` : new URL(request.url).protocol,
  })
}
