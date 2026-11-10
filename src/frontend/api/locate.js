// Vercel Edge Function: GET /api/locate
//
// Vercel knows each visitor's approximate location and adds it as request
// headers, but those headers are not forwarded to the Render API through the
// /api rewrite. This function answers /api/locate on Vercel itself (functions
// take priority over rewrites) with the same JSON shape as the Python API.
//
// City-level only, rounded to ~1 km, never stored or logged. It is used to rank
// place-search suggestions and to centre the first map view.

export const config = { runtime: 'edge' }

const num = (v) => {
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

const decode = (v) => {
  if (!v) return null
  try {
    return decodeURIComponent(v)
  } catch {
    return v
  }
}

export default function handler(request) {
  const h = request.headers
  const lat = num(h.get('x-vercel-ip-latitude'))
  const lng = num(h.get('x-vercel-ip-longitude'))
  const valid = lat != null && lng != null && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 && !(lat === 0 && lng === 0)

  const body = valid
    ? {
        located: true,
        lat: Math.round(lat * 100) / 100,
        lng: Math.round(lng * 100) / 100,
        city: decode(h.get('x-vercel-ip-city')),
        country_code: h.get('x-vercel-ip-country') || null,
        source: 'vercel',
      }
    : { located: false }

  return new Response(JSON.stringify(body), {
    headers: {
      'content-type': 'application/json',
      // Per-visitor answer: never cache in shared caches.
      'cache-control': 'private, no-store',
      'x-robots-tag': 'noindex, nofollow',
    },
  })
}
