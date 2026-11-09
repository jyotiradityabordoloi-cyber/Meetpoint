import { useEffect, useMemo } from 'react'
import L from 'leaflet'
import { Circle, CircleMarker, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'

// No default city: the map opens on the whole world.
const WORLD = { center: [20, 0], zoom: 2 }

const TILE_URL = import.meta.env.VITE_TILE_URL || 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const TILE_ATTRIBUTION =
  import.meta.env.VITE_TILE_ATTRIBUTION || '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

function venueIcon(n, active) {
  return L.divIcon({
    className: '',
    html: `<div class="venue-marker${active ? ' on' : ''}">${n}</div>`,
    iconSize: active ? [34, 34] : [28, 28],
    iconAnchor: active ? [17, 17] : [14, 14],
  })
}

const pinIcon = L.divIcon({
  className: '',
  html: '<div class="pin-marker">📍</div>',
  iconSize: [28, 28],
  iconAnchor: [14, 26],
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
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 15, animate: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])
  return null
}

function FlyToSelected({ target }) {
  const map = useMap()
  useEffect(() => {
    if (target && !map.getBounds().pad(-0.1).contains(target)) map.panTo(target, { animate: true })
  }, [target, map])
  return null
}

export default function MapView({ members, colours, result, selected, onSelect, pin, onMapClick }) {
  const venues = result?.venues || []
  const zones = result?.zones || []
  const showZonesAsTargets = venues.length === 0
  const zoneRadius = Math.max(300, (result?.radius_km || 1) * 1000 * 0.2)

  const points = useMemo(() => {
    const p = members.map((m) => [m.lat, m.lng])
    venues.forEach((v) => p.push([v.lat, v.lng]))
    if (showZonesAsTargets) zones.forEach((z) => p.push([z.lat, z.lng]))
    return p
  }, [members, venues, zones, showZonesAsTargets])

  const signature = `${members.map((m) => m.id).join(',')}|${result?.id || ''}`

  const target = showZonesAsTargets ? zones[selected] : venues[selected]
  const targetLatLng = target ? [target.lat, target.lng] : null

  return (
    <MapContainer center={WORLD.center} zoom={WORLD.zoom} minZoom={2} worldCopyJump className="map" zoomControl>
      <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} maxZoom={19} />
      <ClickHandler onClick={onMapClick} />
      <FitBounds points={points} signature={signature} />
      <FlyToSelected target={targetLatLng} />

      {zones.map((z, i) => (
        <Circle
          key={`z${i}`}
          center={[z.lat, z.lng]}
          radius={zoneRadius}
          pathOptions={{
            color: '#0f766e',
            weight: showZonesAsTargets && i === selected ? 3 : 2,
            dashArray: '6 6',
            fillColor: '#14b8a6',
            fillOpacity: showZonesAsTargets && i === selected ? 0.22 : 0.1,
          }}
          eventHandlers={showZonesAsTargets ? { click: () => onSelect(i) } : undefined}
        >
          <Tooltip>
            Fair area {i + 1} · fairness {z.fairness.toFixed(2)}
          </Tooltip>
        </Circle>
      ))}

      {targetLatLng &&
        members.map((m) => (
          <Polyline
            key={`l${m.id}`}
            positions={[[m.lat, m.lng], targetLatLng]}
            pathOptions={{ color: colours[m.id], weight: 2.5, opacity: 0.7, dashArray: '2 6' }}
            interactive={false}
          />
        ))}

      {members.map((m) => (
        <CircleMarker
          key={m.id}
          center={[m.lat, m.lng]}
          radius={9}
          pathOptions={{ color: '#ffffff', weight: 3, fillColor: colours[m.id], fillOpacity: 1 }}
        >
          <Tooltip direction="top" offset={[0, -8]}>
            {m.name}
          </Tooltip>
        </CircleMarker>
      ))}

      {venues.map((v, i) => (
        <Marker
          key={v.id}
          position={[v.lat, v.lng]}
          icon={venueIcon(i + 1, i === selected)}
          zIndexOffset={i === selected ? 1000 : 0}
          eventHandlers={{ click: () => onSelect(i) }}
        >
          <Tooltip direction="top" offset={[0, -14]}>
            {i + 1}. {v.name}
          </Tooltip>
        </Marker>
      ))}

      {pin && <Marker position={[pin.lat, pin.lng]} icon={pinIcon} />}
    </MapContainer>
  )
}
