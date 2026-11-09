import { MODES, VENUE_GROUPS, isoToLocalInput, localInputToIso } from '../format.js'

export default function GroupSettings({ settings, onChange }) {
  const types = new Set(settings.venue_types || [])

  const toggleGroup = (g) => {
    const on = g.types.every((t) => types.has(t))
    const next = new Set(types)
    g.types.forEach((t) => (on ? next.delete(t) : next.add(t)))
    onChange({ venue_types: [...next] })
  }

  return (
    <div className="card">
      <div className="card-head">
        <h2>Meetup</h2>
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
        <div className="segmented" role="radiogroup">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={settings.travel_mode === m.id}
              className={settings.travel_mode === m.id ? 'seg on' : 'seg'}
              onClick={() => onChange({ travel_mode: m.id })}
            >
              <span aria-hidden="true">{m.icon}</span> {m.label}
            </button>
          ))}
        </div>
      </fieldset>

      <label className="field">
        <span>
          Meeting time <em className="muted">(optional — uses traffic and timetables for that time)</em>
        </span>
        <div className="row">
          <input
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
      </label>

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
                {g.label}
              </button>
            )
          })}
        </div>
        {types.size === 0 && <p className="hint">Pick at least one kind of place to get venue suggestions.</p>}
      </fieldset>
    </div>
  )
}
