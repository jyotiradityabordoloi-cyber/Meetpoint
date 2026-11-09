import { useEffect, useState } from 'react'
import { api, ApiError } from '../api.js'
import { MODES, modeLabel } from '../format.js'

const METHODS = [
  { id: 'search', label: 'Search address' },
  { id: 'gps', label: 'My location' },
  { id: 'pin', label: 'Pin on map' },
]

export default function AddPerson({ mapsConfigured, groupMode, pin, onClearPin, onAdd, isFirst }) {
  const [name, setName] = useState('')
  const [method, setMethod] = useState(mapsConfigured ? 'search' : 'gps')
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState(null)
  const [location, setLocation] = useState(null) // {lat, lng, label}
  const [mode, setMode] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  useEffect(() => {
    if (!mapsConfigured && method === 'search') setMethod('gps')
  }, [mapsConfigured, method])

  // A click on the map drops a pin and switches to pin mode.
  useEffect(() => {
    if (pin) {
      setMethod('pin')
      setLocation({ lat: pin.lat, lng: pin.lng, label: `Pin at ${pin.lat.toFixed(4)}, ${pin.lng.toFixed(4)}` })
      setMsg(null)
    }
  }, [pin])

  const choose = (m) => {
    setMethod(m)
    setMsg(null)
    setMatches(null)
    if (m !== 'pin') onClearPin()
    if (m === 'pin' && !pin) setLocation(null)
    if (m === 'gps') locate()
    if (m === 'search') setLocation(null)
  }

  const search = async (e) => {
    e?.preventDefault()
    if (query.trim().length < 2) return
    setBusy(true)
    setMsg(null)
    setMatches(null)
    setLocation(null)
    try {
      const res = await api.geocode(query.trim())
      setMatches(res)
      if (res.length === 0) {
        setMsg(`We couldn't find "${query.trim()}". Add the area and city, or drop a pin on the map.`)
      } else if (res.length === 1) {
        setLocation(res[0])
      }
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : 'Address search failed. Drop a pin on the map instead.')
    } finally {
      setBusy(false)
    }
  }

  function locate() {
    setLocation(null)
    if (!('geolocation' in navigator)) {
      setMsg("This browser can't share your location. Search for an address or drop a pin on the map.")
      return
    }
    setBusy(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setBusy(false)
        setLocation({ lat: pos.coords.latitude, lng: pos.coords.longitude, label: 'My current location' })
      },
      (err) => {
        setBusy(false)
        setMsg(
          err.code === err.PERMISSION_DENIED
            ? 'Location permission was denied. Search for an address or drop a pin on the map instead.'
            : "We couldn't get your location. Search for an address or drop a pin on the map instead.",
        )
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    )
  }

  const submit = async (e) => {
    e.preventDefault()
    if (!name.trim() || !location) return
    setBusy(true)
    setMsg(null)
    try {
      await onAdd(
        {
          name: name.trim(),
          lat: location.lat,
          lng: location.lng,
          address: location.label?.slice(0, 300) || null,
          travel_mode: mode || null,
        },
        method,
      )
      setName('')
      setQuery('')
      setMatches(null)
      setLocation(null)
      setMode('')
      if (method === 'pin') setMethod(mapsConfigured ? 'search' : 'gps')
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : 'Could not add this person. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="add-person" onSubmit={submit}>
      <h3>{isFirst ? 'Add yourself' : 'Add a person'}</h3>
      <label className="field">
        <span>Name</span>
        <input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="e.g. Sam" />
      </label>

      <div className="tabs" role="tablist" aria-label="Starting point">
        {METHODS.map((m) => {
          const disabled = m.id === 'search' && !mapsConfigured
          return (
            <button
              key={m.id}
              type="button"
              role="tab"
              aria-selected={method === m.id}
              disabled={disabled}
              title={disabled ? 'Address search needs a Google Maps key on the server' : undefined}
              className={method === m.id ? 'tab on' : 'tab'}
              onClick={() => choose(m.id)}
            >
              {m.label}
            </button>
          )
        })}
      </div>

      {method === 'search' && (
        <div className="row">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search(e)}
            placeholder="Street, area and city — any country"
            aria-label="Address"
          />
          <button type="button" className="btn secondary" onClick={search} disabled={busy || query.trim().length < 2}>
            Search
          </button>
        </div>
      )}
      {method === 'search' && matches && matches.length > 1 && (
        <ul className="matches" role="listbox" aria-label="Matching addresses">
          {matches.map((m, i) => (
            <li key={i}>
              <button
                type="button"
                role="option"
                aria-selected={location === m}
                className={location === m ? 'match on' : 'match'}
                onClick={() => setLocation(m)}
              >
                {m.label}
              </button>
            </li>
          ))}
          <li className="hint">Not listed? Click the map to drop a pin.</li>
        </ul>
      )}
      {method === 'pin' && !location && <p className="hint">Click or tap the map where this person will start.</p>}
      {method === 'gps' && busy && !location && <p className="hint">Finding your location…</p>}

      {location && (
        <p className="chosen">
          <span aria-hidden="true">📍</span> {location.label}
        </p>
      )}
      {msg && (
        <p className="hint warn" role="status">
          {msg}
        </p>
      )}

      <label className="field">
        <span>Travels by</span>
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="">Same as group ({modeLabel(groupMode)})</option>
          {MODES.map((m) => (
            <option key={m.id} value={m.id}>
              {m.icon} {m.label}
            </option>
          ))}
        </select>
      </label>

      <button className="btn secondary wide" type="submit" disabled={busy || !name.trim() || !location}>
        {isFirst ? 'Add me' : 'Add person'}
      </button>
    </form>
  )
}
