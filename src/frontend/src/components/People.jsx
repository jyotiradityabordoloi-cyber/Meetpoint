import { modeIcon, modeLabel } from '../format.js'

export default function People({ members, colours, groupMode, onRemove }) {
  if (members.length === 0) {
    return <p className="empty">No one yet. Add yourself, then share the invite link so friends can add themselves.</p>
  }
  return (
    <ul className="people">
      {members.map((m) => {
        const mode = m.travel_mode || groupMode
        return (
          <li key={m.id}>
            <span className="dot" style={{ background: colours[m.id] }} aria-hidden="true" />
            <div className="person">
              <strong>{m.name}</strong>
              <span className="muted small">{m.address || `${m.lat.toFixed(4)}, ${m.lng.toFixed(4)}`}</span>
            </div>
            <span className={m.travel_mode ? 'mode-tag override' : 'mode-tag'} title={modeLabel(mode)}>
              {modeIcon(mode)}
              <span className="sr-only">{modeLabel(mode)}</span>
            </span>
            <button className="icon-btn" onClick={() => onRemove(m.id)} aria-label={`Remove ${m.name}`}>
              ×
            </button>
          </li>
        )
      })}
    </ul>
  )
}
