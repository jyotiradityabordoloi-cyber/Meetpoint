// Labels, colours and unit formatting. Units come from the result's region
// (km or miles); numbers use the viewer's locale.

// People are told apart by initials, not colours: the UI uses only three
// colours (cream, black, burnt orange).
export function initials(name) {
  const words = String(name || '?').trim().split(/\s+/).filter(Boolean)
  if (!words.length) return '?'
  const a = words[0][0] || '?'
  const b = words.length > 1 ? words[words.length - 1][0] : words[0][1] || ''
  return (a + b).toUpperCase()
}

// Unique tags for a member list: "SA", "SA2" when two people share initials.
export function memberTags(members) {
  const seen = {}
  const tags = {}
  for (const m of members) {
    const base = initials(m.name)
    seen[base] = (seen[base] || 0) + 1
    tags[m.id] = seen[base] > 1 ? `${base}${seen[base]}` : base
  }
  return tags
}

export const MODES = [
  { id: 'DRIVE', label: 'Car', short: 'Car' },
  { id: 'TWO_WHEELER', label: 'Two-wheeler', short: '2-wheeler' },
  { id: 'TRANSIT', label: 'Public transport', short: 'Transit' },
  { id: 'WALK', label: 'Walk', short: 'Walk' },
  { id: 'BICYCLE', label: 'Cycle', short: 'Cycle' },
]

export function modeLabel(id) {
  return MODES.find((m) => m.id === id)?.label ?? id
}

// Each chip maps to one or more Places (New) type ids.
export const VENUE_GROUPS = [
  { id: 'cafes', label: 'Cafés', types: ['cafe', 'coffee_shop'] },
  { id: 'restaurants', label: 'Restaurants', types: ['restaurant'] },
  { id: 'bars', label: 'Bars & pubs', types: ['bar', 'pub'] },
  { id: 'parks', label: 'Parks', types: ['park'] },
  { id: 'malls', label: 'Malls', types: ['shopping_mall'] },
]

const TYPE_LABELS = {
  cafe: 'Café',
  coffee_shop: 'Coffee shop',
  restaurant: 'Restaurant',
  bar: 'Bar',
  pub: 'Pub',
  park: 'Park',
  shopping_mall: 'Mall',
  bakery: 'Bakery',
}

export function venueTypeLabel(t) {
  if (!t) return 'Place'
  return TYPE_LABELS[t] ?? t.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}

export function formatMinutes(min) {
  if (min == null || Number.isNaN(min)) return '–'
  const m = Math.max(1, Math.round(min))
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const r = m % 60
  return r ? `${h} h ${String(r).padStart(2, '0')} min` : `${h} h`
}

export function formatDistance(meters, units = 'metric', locale) {
  if (meters == null) return ''
  const nf = (digits) => new Intl.NumberFormat(locale, { maximumFractionDigits: digits })
  if (units === 'imperial') {
    const mi = meters / 1609.344
    return `${nf(mi < 10 ? 1 : 0).format(mi)} mi`
  }
  if (meters < 1000) return `${nf(0).format(Math.round(meters / 10) * 10)} m`
  const km = meters / 1000
  return `${nf(km < 10 ? 1 : 0).format(km)} km`
}

export function regionHeading(region) {
  if (!region) return null
  const parts = [region.area, region.locality].filter(Boolean)
  return parts.length ? parts.join(', ') : null
}

// <input type="datetime-local"> works in the viewer's local time with no
// offset. We store ISO-8601 *with* the viewer's UTC offset so any time zone
// can read it back correctly.
export function localInputToIso(value) {
  if (!value) return null
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return null
  const off = -d.getTimezoneOffset()
  const sign = off >= 0 ? '+' : '-'
  const abs = Math.abs(off)
  const pad = (n) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  )
}

export function isoToLocalInput(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function formatMeetTime(iso, locale) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(d)
}

export function googleMapsSearchUrl(lat, lng) {
  return `https://www.google.com/maps/search/?api=1&query=${lat.toFixed(6)},${lng.toFixed(6)}`
}

// Simple mean of points; fine for biasing search within a city-sized group.
export function centreOf(points) {
  if (!points.length) return null
  const lat = points.reduce((a, p) => a + p.lat, 0) / points.length
  const lng = points.reduce((a, p) => a + p.lng, 0) / points.length
  return { lat, lng }
}

// Great-circle distance in km (used to decide if the IP city still applies).
export function distanceKm(a, b) {
  const r = Math.PI / 180
  const dLat = (b.lat - a.lat) * r
  const dLng = (b.lng - a.lng) * r
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLng / 2) ** 2
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(h)))
}

// The exact location a visitor chose to share is kept only in this browser
// tab (sessionStorage), never sent to the server except as a rounded
// search-ranking hint.
const EXACT_KEY = 'meetpoint.exactLocation'
export function loadExactLocation() {
  try {
    const v = JSON.parse(sessionStorage.getItem(EXACT_KEY) || 'null')
    return v && Number.isFinite(v.lat) && Number.isFinite(v.lng) ? v : null
  } catch {
    return null
  }
}
export function saveExactLocation(loc) {
  try {
    sessionStorage.setItem(EXACT_KEY, JSON.stringify({ lat: loc.lat, lng: loc.lng }))
  } catch {
    /* storage blocked: fine, it just won't be remembered */
  }
}
