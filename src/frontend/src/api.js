// Thin fetch wrapper. Every API error has the shape
// {"detail": {"code": "...", "message": "..."}}; we surface both.

const BASE = import.meta.env.VITE_API_BASE || ''

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request(method, path, body, signal) {
  let res
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    })
  } catch (e) {
    if (e?.name === 'AbortError') throw e
    throw new ApiError(0, 'NETWORK', "Can't reach the MeetPoint server. Check your connection and try again.")
  }
  if (res.status === 204) return null
  let data = null
  try {
    data = await res.json()
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) {
    const d = data && data.detail
    if (d && typeof d === 'object' && d.code) throw new ApiError(res.status, d.code, d.message)
    throw new ApiError(res.status, 'HTTP_' + res.status, 'Something went wrong. Please try again.')
  }
  return data
}

export const api = {
  health: () => request('GET', '/api/health'),
  createGroup: (g) => request('POST', '/api/groups', g),
  getGroup: (id) => request('GET', `/api/groups/${encodeURIComponent(id)}`),
  patchGroup: (id, patch) => request('PATCH', `/api/groups/${encodeURIComponent(id)}`, patch),
  addMember: (id, m) => request('POST', `/api/groups/${encodeURIComponent(id)}/members`, m),
  removeMember: (id, mid) =>
    request('DELETE', `/api/groups/${encodeURIComponent(id)}/members/${encodeURIComponent(mid)}`),
  meetingPoint: (id) => request('POST', `/api/groups/${encodeURIComponent(id)}/meeting-point`),
  recommendations: (id, limit = 1) =>
    request('GET', `/api/groups/${encodeURIComponent(id)}/recommendations?limit=${limit}`),
  geocode: (q) => request('GET', `/api/geocode?q=${encodeURIComponent(q)}`),
  autocomplete: (q, { bias, session, lang } = {}, signal) => {
    const p = new URLSearchParams({ q })
    if (bias) {
      p.set('lat', bias.lat.toFixed(4))
      p.set('lng', bias.lng.toFixed(4))
    }
    if (session) p.set('session', session)
    if (lang) p.set('lang', lang)
    return request('GET', `/api/autocomplete?${p}`, undefined, signal)
  },
  place: (id, session) =>
    request('GET', `/api/places/${encodeURIComponent(id)}${session ? `?session=${encodeURIComponent(session)}` : ''}`),
  locate: () => request('GET', '/api/locate'),
  journeys: (id, to) => request('POST', `/api/groups/${encodeURIComponent(id)}/journeys`, to),
}

// Venue photo through the API proxy (the Google key never reaches the browser).
export function photoUrl(name, width = 480) {
  return `${BASE}/api/photos?name=${encodeURIComponent(name)}&w=${width}`
}

// Minimal client-side event hook for the PRD metrics. Pushes to
// window.dataLayer if an analytics tag is present; otherwise a no-op.
export function track(event, props = {}) {
  try {
    if (Array.isArray(window.dataLayer)) window.dataLayer.push({ event, ...props, ts: Date.now() })
  } catch {
    /* ignore */
  }
}
