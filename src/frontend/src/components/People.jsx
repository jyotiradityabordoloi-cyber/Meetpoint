import { modeLabel } from '../format.js'
import Icon, { ModeIcon } from './Icon.jsx'

export default function People({ members, tags, styles = {}, groupMode, onRemove }) {
  if (members.length === 0) {
    return <p className="empty">No one yet. Add yourself, then share the invite link so friends can add themselves.</p>
  }
  return (
    <ul className="people">
      {members.map((m) => {
        const mode = m.travel_mode || groupMode
        return (
          <li key={m.id}>
            <span className={`avatar ${styles[m.id]?.className || ''}`} aria-hidden="true">
              {tags[m.id]}
            </span>
            <div className="person">
              <strong>{m.name}</strong>
              <span className="muted small">{m.address || `${m.lat.toFixed(4)}, ${m.lng.toFixed(4)}`}</span>
            </div>
            <span className={m.travel_mode ? 'mode-tag override' : 'mode-tag'} title={modeLabel(mode)}>
              <ModeIcon mode={mode} size={18} />
              <span className="sr-only">{modeLabel(mode)}</span>
            </span>
            <button className="icon-btn" onClick={() => onRemove(m.id)} aria-label={`Remove ${m.name}`}>
              <Icon name="close" size={16} />
            </button>
          </li>
        )
      })}
    </ul>
  )
}
