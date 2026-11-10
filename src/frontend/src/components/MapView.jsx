import { useEffect, useMemo } from 'react'
import L from 'leaflet'
import { Circle, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import { formatMinutes } from '../format.js'

// No hard-coded city: the map opens on the whole world, or on the viewer's
// approximate area when the server can tell it from their IP address.
const WORLD = { center: [20, 0], zoom: 2 }

const TILE_URL = import.meta.env.VITE_TILE_URL || 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const TILE_ATTRIBUTION =
  import.meta.env.VITE_TILE_ATTRIBUTION || '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

// The three brand colours (also defined as CSS variables in styles.css).
const INK = '#111111'
const ORANGE = '#d9541e'
const CREAM = '#f3eee3'

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)

function venueIcon(n, active) {
  const size = active ? 40 : 32
  return L.divIcon({
    className: '',
    html: `<div class="venue-pin${active ? ' on' : ''}"><span>${n}</span></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size],
  })
}

function personIcon(tag) {
  return L.divIcon({
    className: '',
    html: `<div class="person-marker"><span>${escapeHtml(tag)}</span></div>`,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  })
}

const draftIcon = L.divIcon({
  className: '',
  html: '<div class="draft-pin"><span></span></div>',
  iconSize: [30, 30],
  iconAnchor: [15, 30],
})

const destIcon = L.divIcon({
  className: '',
  html: '<div class="dest-pulse"></div>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
})

function ClickHandler({ onClick }) {
  useMapEvents({ click: (e) => onClick({ lat: e.latlng.lat, lng: e.latlng.wrap().lng }) })
  return null
}

function FitBounds({ points, signature }) {
  const map = useMap()
  useEffect(() => {
    if (points.length === 0) return
    if (points.length === 1) {
      map.setView(points[0], Math.max(map.getZoom(), 13), { animate: true })
      return
    }
    map.fitBounds(L.latLngBounds(points), { padding: [48, 48], maxZoom: 15, animate: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])
  return null
}

// Leaflet must be told when its container changes size (panel resize,
// phone rotation, the side pane opening), or tiles and bounds go wrong.
function KeepSized({ signature }) {
  const map = useMap()
  useEffect(() => {
    const ro = new ResizeObserver(() => map.invalidateSize({ animate: false }))
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map])
  useEffect(() => {
    map.invalidateSize({ animate: false })
  }, [map, signature])
  return null
}

// Before anyone is added, show the viewer's area instead of the whole world.
function StartNear({ approx, hasPoints }) {
  const map = useMap()
  useEffect(() => {
    if (approx && !hasPoints && map.getZoom() <= 3) map.setView([approx.lat, approx.lng], 11, { animate: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approx?.lat, approx?.lng])
  return null
}

// Pan to a freshly chosen starting point so the person can check it.
function ShowDraft({ draft }) {
  const map = useMap()
  useEffect(() => {
    if (!draft) return
    const ll = [draft.lat, draft.lng]
    if (map.getZoom() < 12) map.setView(ll, 14, { animate: true })
    else if (!map.getBounds().pad(-0.15).contains(ll)) map.panTo(ll, { animate: true })
  }, [draft, map])
  return null
}

// When a venue is chosen, frame everyone's journey to it.
function FrameJourneys({ paths, signature }) {
  const map = useMap()
  useEffect(() => {
    const pts = paths.flat()
    if (pts.length > 1) map.fitBounds(L.latLngBounds(pts), { padding: [56, 56], maxZoom: 15, animate: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])
  return null
}

/** A gentle curve from a to b, used when no road/transit route is available. */
export function curve(a, b, i = 0) {
  const [lat1, lng1] = a
  const [lat2, lng2] = b
  const dx = lng2 - lng1
  const dy = lat2 - lat1
  const side = i % 2 === 0 ? 1 : -1
  const bend = 0.16 + (i % 3) * 0.04
  const cx = (lng1 + lng2) / 2 - dy * bend * side
  const cy = (lat1 + lat2) / 2 + dx * bend * side
  const out = []
  for (let k = 0; k <= 48; k++) {
    const t = k / 48
    const u = 1 - t
    out.push([u * u * lat1 + 2 * u * t * cy + t * t * lat2, u * u * lng1 + 2 * u * t * cx + t * t * lng2])
  }
  return out
}

// Give the freshly added SVG path a normalised length so CSS can "draw" it in,
// staggered per person.
function primePath(layer, i, kind) {
  const el = layer.getElement?.()
  if (!el) return
  if (kind === 'line') el.setAttribute('pathLength', '1')
  el.style.setProperty('--delay', `${i * 140}ms`)
}

/** Everyone's journey to the chosen venue: black casing, orange route, flowing cream beads. */
function Journeys({ journeys, to, names }) {
  const lines = useMemo(
    () =>
      journeys.map((j, i) => ({
        ...j,
        positions: j.path && j.path.length > 1 ? j.path : curve([j.from.lat, j.from.lng], [to.lat, to.lng], i),
        curved: !(j.path && j.path.length > 1),
      })),
    [journeys, to],
  )
  return (
    <>
      {lines.map((j, i) => (
        <Polyline
          key={`c-${j.member_id}-${to.lat}-${to.lng}`}
          positions={j.positions}
          // className must be a direct prop: Leaflet only applies it when the path
          // is created, and react-leaflet applies pathOptions later via setStyle().
          className="journey-casing"
          pathOptions={{ color: INK, weight: 9, opacity: 0.85, lineCap: 'round', lineJoin: 'round' }}
          eventHandlers={{ add: (e) => primePath(e.target, i, 'casing') }}
        >
          <Tooltip sticky>
            {names[j.member_id] || 'Someone'}
            {j.minutes != null ? ` · ${formatMinutes(j.minutes)}` : ''}
            {j.curved ? ' (straight-line sketch)' : ''}
          </Tooltip>
        </Polyline>
      ))}
      {lines.map((j, i) => (
        <Polyline
          key={`l-${j.member_id}-${to.lat}-${to.lng}`}
          positions={j.positions}
          interactive={false}
          className={j.curved ? 'journey-line curved' : 'journey-line'}
          pathOptions={{ color: ORANGE, weight: 5, lineCap: 'round', lineJoin: 'round' }}
          eventHandlers={{ add: (e) => primePath(e.target, i, 'line') }}
        />
      ))}
      {lines.map((j, i) => (
        <Polyline
          key={`f-${j.member_id}-${to.lat}-${to.lng}`}
          positions={j.positions}
          interactive={false}
          className="journey-flow"
          pathOptions={{ color: CREAM, weight: 3, lineCap: 'round' }}
          eventHandlers={{ add: (e) => primePath(e.target, i, 'flow') }}
        />
      ))}
      <Marker position={[to.lat, to.lng]} icon={destIcon} interactive={false} zIndexOffset={-100} />
    </>
  )
}

export default function MapView({ members, tags, result, selected, onSelect, draft, approx, pickMode, onMapClick, journeys }) {
  const venues = result?.venues || []
  const zones = result?.zones || []
  const showZonesAsTargets = venues.length === 0
  const zoneRadius = Math.max(300, (result?.radius_km || 1) * 1000 * 0.2)
  const names = useMemo(() => Object.fromEntries(members.map((m) => [m.id, m.name])), [members])

  const points = useMemo(() => {
    const p = members.map((m) => [m.lat, m.lng])
    venues.forEach((v) => p.push([v.lat, v.lng]))
    if (showZonesAsTargets) zones.forEach((z) => p.push([z.lat, z.lng]))
    return p
  }, [members, venues, zones, showZonesAsTargets])

  const signature = `${members.map((m) => m.id).join(',')}|${result?.id || ''}`
  const journeyData = journeys?.status === 'ready' ? journeys.data : null
  const journeyPaths = useMemo(
    () => (journeyData ? journeyData.journeys.filter((j) => j.path).map((j) => j.path) : []),
    [journeyData],
  )

  return (
    <div className={pickMode ? 'map-shell picking' : 'map-shell'}>
      <MapContainer center={WORLD.center} zoom={WORLD.zoom} minZoom={2} worldCopyJump className="map" zoomControl>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} maxZoom={19} />
        <ClickHandler onClick={onMapClick} />
        <KeepSized signature={signature} />
        <StartNear approx={approx} hasPoints={points.length > 0} />
        <ShowDraft draft={draft} />
        <FitBounds points={points} signature={signature} />
        {journeyData && journeyPaths.length > 0 && (
          <FrameJourneys paths={journeyPaths} signature={`${journeys.key}`} />
        )}

        {zones.map((z, i) => {
          const on = showZonesAsTargets && i === selected
          return (
            <Circle
              key={`z${i}`}
              center={[z.lat, z.lng]}
              radius={zoneRadius}
              pathOptions={{
                color: on ? ORANGE : INK,
                weight: on ? 3 : 1.5,
                opacity: on ? 1 : 0.55,
                dashArray: '6 7',
                fillColor: on ? ORANGE : INK,
                fillOpacity: on ? 0.16 : 0.05,
              }}
              eventHandlers={showZonesAsTargets ? { click: () => onSelect(i) } : undefined}
            >
              <Tooltip>
                Fair area {i + 1} · fairness {z.fairness.toFixed(2)}
              </Tooltip>
            </Circle>
          )
        })}

        {journeyData && <Journeys journeys={journeyData.journeys} to={journeyData.to} names={names} />}

        {venues.map((v, i) => (
          <Marker
            key={v.id}
            position={[v.lat, v.lng]}
            icon={venueIcon(i + 1, i === selected)}
            zIndexOffset={i === selected ? 1000 : 0}
            eventHandlers={{ click: () => onSelect(i) }}
          >
            <Tooltip direction="top" offset={[0, -34]}>
              {i + 1}. {v.name}
            </Tooltip>
          </Marker>
        ))}

        {members.map((m) => (
          <Marker key={m.id} position={[m.lat, m.lng]} icon={personIcon(tags[m.id] || '?')} zIndexOffset={1500}>
            <Tooltip direction="top" offset={[0, -16]}>
              {m.name}
            </Tooltip>
          </Marker>
        ))}

        {draft && (
          <Marker position={[draft.lat, draft.lng]} icon={draftIcon} zIndexOffset={2000}>
            <Tooltip direction="top" offset={[0, -30]}>
              New starting point
            </Tooltip>
          </Marker>
        )}
      </MapContainer>

      {pickMode && (
        <div className="map-hint strong" role="status">
          Tap the map where this person starts
        </div>
      )}
      {!pickMode && members.length === 0 && !draft && (
        <div className="map-hint" role="note">
          {approx?.city ? `Showing ${approx.city} (approximate). ` : ''}Add people and they'll appear here.
        </div>
      )}
      {journeys?.status === 'loading' && <div className="map-hint">Drawing everyone's route…</div>}
    </div>
  )
}
