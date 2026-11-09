import { useId } from 'react'
import { MODES, VENUE_GROUPS, formatMeetTime, isoToLocalInput, localInputToIso, modeIcon, modeLabel } from '../format.js'

function summary(settings) {
  const types = new Set(settings.venue_types || [])
  const places = VENUE_GROUPS.filter((g) => g.types.some((t) => types.has(t))).map((g) => g.label)
  const parts = [`${modeIcon(settings.travel_mode)} ${modeLabel(settings.travel_mode)}`]
  const when = formatMeetTime(settings.meet_at)
  if (when) parts.push(when)
  parts.push(places.length ? places.join(', ') : 'No place types')
  return parts.join(' · ')
}

/**
 * Meetup name, group travel mode, optional time and venue types.
 * Collapses to a one-line summary once people have been added.
 */
export default function GroupSettings({ settings, onChange, collapsed, onToggle }) {
  const types = new Set(settings.venue_types || [])
  const bodyId = useId()

  const toggleGroup = (g) => {
    const on = g.types.every((t) => types.has(t))
    const next = new Set(types)
    g.types.forEach((t) => (on ? next.delete(t) : next.add(t)))
    onChange({ venue_types: [...next] })
  }

  if (collapsed) {
    return (
      <div className="card settings-summary">
        <div className="summary-text">
          <h2>{settings.name?.trim() || 'Our meetup'}</h2>
          <p className="muted small">{summary(settings)}</p>
        </div>
        <button type="button" className="btn ghost small" onClick={onToggle} aria-expanded="false" aria-controls={bodyId}>
          Edit
        </button>
      </div>
    )
  }

  return (
    <div className="card" id={bodyId}>
      <div className="card-head">
        <h2>Meetup</h2>
        {onToggle && (
          <button type="button" className="link small" onClick={onToggle} aria-expanded="true">
            Done
          </button>
        )}
      </div>

      <label className="field">
        <span>Name</span>
        <input
          value={settings.name}
          maxLength={120}
          onChange={(e) => onChange({ name: e.target.value })}
          placeholder="Friday dinner"
        />
      </label>

      <fieldset className="field">
        <legend>How most people will travel</legend>
        <div className="mode-grid" role="radiogroup">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={settings.travel_mode === m.id}
              aria-label={m.label}
              title={m.label}
              className={settings.travel_mode === m.id ? 'mode-btn on' : 'mode-btn'}
              onClick={() => onChange({ travel_mode: m.id })}
            >
              <span className="mode-icon" aria-hidden="true">
                {m.icon}
              </span>
              <span className="mode-text">{m.short}</span>
            </button>
          ))}
        </div>
      </fieldset>

      <div className="field">
        <label htmlFor={`${bodyId}-time`}>
          Meeting time <em className="muted">(optional)</em>
        </label>
        <div className="row">
          <input
            id={`${bodyId}-time`}
            type="datetime-local"
            value={isoToLocalInput(settings.meet_at)}
            onChange={(e) => onChange({ meet_at: localInputToIso(e.target.value) })}
          />
          {settings.meet_at && (
            <button type="button" className="btn ghost small" onClick={() => onChange({ meet_at: null })}>
              Clear
            </button>
          )}
        </div>
        <p className="hint">Uses expected traffic and timetables for that time.</p>
      </div>

      <fieldset className="field">
        <legend>Places to suggest</legend>
        <div className="chips">
          {VENUE_GROUPS.map((g) => {
            const on = g.types.every((t) => types.has(t))
            return (
              <button
                key={g.id}
                type="button"
                aria-pressed={on}
                className={on ? 'chip on' : 'chip'}
                onClick={() => toggleGroup(g)}
              >
                {on && <span aria-hidden="true">✓ </span>}
                {g.label}
              </button>
            )
          })}
        </div>
        {types.size === 0 && <p className="hint warn">Pick at least one kind of place to get venue suggestions.</p>}
      </fieldset>
    </div>
  )
}
