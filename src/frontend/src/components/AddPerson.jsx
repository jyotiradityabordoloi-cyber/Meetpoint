import { useState } from 'react'
import { ApiError } from '../api.js'
import { MODES, modeLabel } from '../format.js'
import Icon, { ModeIcon } from './Icon.jsx'
import PlaceSearch from './PlaceSearch.jsx'

/**
 * Add one person: name, how they travel, and where they start.
 * The starting point (`draft`) lives in App so a map click can set it too.
 */
export default function AddPerson({
  groupMode,
  searchAvailable,
  bias,
  biasLabel,
  draft,
  onDraft,
  pickMode,
  onPickMode,
  onAdd,
  isFirst,
}) {
  const [name, setName] = useState('')
  const [mode, setMode] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  const locate = () => {
    setMsg(null)
    onPickMode(false)
    if (!('geolocation' in navigator)) {
      setMsg("This browser can't share your location. Search for a place or pick on the map.")
      return
    }
    setBusy(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setBusy(false)
        onDraft({ lat: pos.coords.latitude, lng: pos.coords.longitude, label: 'My current location', source: 'gps' })
      },
      (err) => {
        setBusy(false)
        setMsg(
          err.code === err.PERMISSION_DENIED
            ? 'Location permission was denied. Search for a place or pick on the map instead.'
            : "We couldn't get your location. Search for a place or pick on the map instead.",
        )
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    )
  }

  const submit = async (e) => {
    e.preventDefault()
    if (!name.trim()) {
      setMsg('Add a name so everyone knows who is who.')
      return
    }
    if (!draft) {
      setMsg('Choose a starting point: search, use your location, or pick on the map.')
      return
    }
    setBusy(true)
    setMsg(null)
    try {
      await onAdd(
        {
          name: name.trim(),
          lat: draft.lat,
          lng: draft.lng,
          address: draft.label?.slice(0, 300) || null,
          travel_mode: mode || null,
        },
        draft.source || 'search',
      )
      setName('')
      setMode('')
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : 'Could not add this person. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="add-person" onSubmit={submit} noValidate>
      <h3>{isFirst ? 'Add yourself' : 'Add a person'}</h3>

      <div className="name-row">
        <label className="field grow">
          <span className="sr-only">Name</span>
          <input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. Sam" />
        </label>
        <label className="field mode-pick" title="How this person travels">
          <span className="sr-only">Travels by</span>
          <span className="mode-pick-icon" aria-hidden="true">
            <ModeIcon mode={mode || groupMode} size={17} />
          </span>
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">
              Same as group ({modeLabel(groupMode)})
            </option>
            {MODES.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {draft ? (
        <div className="chosen" role="status">
          <Icon name={draft.source === 'gps' ? 'target' : 'pin'} size={18} />
          <span className="chosen-label">{draft.label}</span>
          <button type="button" className="link small" onClick={() => onDraft(null)}>
            Change
          </button>
        </div>
      ) : (
        <>
          {searchAvailable ? (
            <PlaceSearch bias={bias} biasLabel={biasLabel} onPick={(loc) => onDraft({ ...loc, source: 'search' })} />
          ) : (
            <p className="hint">Place search isn't set up on this server. Use your location or pick on the map.</p>
          )}
          <div className="alt-actions">
            <button type="button" className="btn ghost small" onClick={locate} disabled={busy}>
              <Icon name="target" size={16} /> Use my location
            </button>
            <button
              type="button"
              className={pickMode ? 'btn ghost small on' : 'btn ghost small'}
              aria-pressed={pickMode}
              onClick={() => onPickMode(!pickMode)}
            >
              <Icon name="map" size={16} /> {pickMode ? 'Tap the map…' : 'Pick on map'}
            </button>
          </div>
        </>
      )}

      {msg && (
        <p className="hint warn" role="status">
          {msg}
        </p>
      )}

      <button className="btn secondary wide" type="submit" disabled={busy}>
        {busy ? 'Adding…' : isFirst ? 'Add me' : 'Add person'}
      </button>
    </form>
  )
}
