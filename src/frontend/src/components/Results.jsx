import { track } from '../api.js'
import {
  colourFor,
  formatDistance,
  formatMeetTime,
  formatMinutes,
  googleMapsSearchUrl,
  modeIcon,
  modeLabel,
  regionHeading,
  venueTypeLabel,
} from '../format.js'

function TravelStrips({ travel, scaleMax, colours, units }) {
  return (
    <ul className="strips">
      {travel.map((t, i) => {
        const pct = Math.max(3, Math.min(100, (t.minutes / scaleMax) * 100))
        const colour = colours[t.member_id] || colourFor(i)
        return (
          <li key={t.member_id}>
            <span className="strip-name" title={t.name}>
              <span className="dot" style={{ background: colour }} aria-hidden="true" />
              {t.name}
            </span>
            <span className="strip-track">
              <span className="strip-bar" style={{ width: `${pct}%`, background: colour }} />
            </span>
            <span className="strip-label">
              {formatMinutes(t.minutes)}
              {t.meters != null && <span className="muted"> · {formatDistance(t.meters, units)}</span>}
              {t.mode && (
                <span className="mode-mini" title={modeLabel(t.mode)}>
                  {' '}
                  {modeIcon(t.mode)}
                </span>
              )}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function FairnessPill({ value }) {
  const level = value >= 0.8 ? 'good' : value >= 0.6 ? 'ok' : 'low'
  return (
    <span className={`pill ${level}`} title="1.0 means everyone travels equally long">
      Fairness {value.toFixed(2)}
    </span>
  )
}

export default function Results({ result, colours, selected, onSelect, meetingName }) {
  const units = result.region?.units || 'metric'
  const heading = regionHeading(result.region)
  const meetTime = formatMeetTime(result.meet_at)
  const memberName = Object.fromEntries((result.members || []).map((m) => [m.id, m.name]))
  const memberMode = Object.fromEntries((result.members || []).map((m) => [m.id, m.mode]))

  const venues = result.venues || []
  const zones = result.zones || []
  const scaleMax = Math.max(
    1,
    ...venues.flatMap((v) => v.travel.map((t) => t.minutes)),
    ...(venues.length ? [] : zones.flatMap((z) => Object.values(z.minutes))),
  )

  const share = async (v) => {
    const url = v.maps_url || googleMapsSearchUrl(v.lat, v.lng)
    const text = `${meetingName}: let's meet at ${v.name}. Longest trip ${formatMinutes(v.max_minutes)}.`
    try {
      if (navigator.share) await navigator.share({ title: v.name, text, url })
      else await navigator.clipboard.writeText(`${text} ${url}`)
      track('venue_shared', { rank: venues.indexOf(v) + 1 })
    } catch {
      /* cancelled */
    }
  }

  return (
    <section className="results" aria-live="polite">
      <div className="results-head">
        <h2>{heading ? `Fair spots near ${heading}` : 'Fair spots for your group'}</h2>
        {meetTime && <p className="muted small">For {meetTime} (your local time)</p>}
        {result.estimated && <span className="pill estimate">Estimated times</span>}
      </div>

      {result.warnings?.length > 0 && (
        <ul className="warnings">
          {result.warnings.map((w) => (
            <li key={w.code}>{w.message}</li>
          ))}
        </ul>
      )}

      {venues.length > 0 ? (
        <ol className="venues">
          {venues.map((v, i) => (
            <li key={v.id}>
              <article
                className={i === selected ? 'venue on' : 'venue'}
                onClick={() => onSelect(i)}
                onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect(i)}
                tabIndex={0}
                aria-current={i === selected}
              >
                <header>
                  <span className="rank">{i + 1}</span>
                  <div className="venue-title">
                    <h3>{v.name}</h3>
                    <p className="muted small">
                      {venueTypeLabel(v.type)}
                      {v.rating != null && (
                        <>
                          {' · '}★ {v.rating.toFixed(1)} <span>({v.ratings.toLocaleString()})</span>
                        </>
                      )}
                      {v.open_now === true && <span className="open"> · Open now</span>}
                      {v.open_now === false && <span className="closed"> · Closed now</span>}
                    </p>
                  </div>
                  <FairnessPill value={v.fairness} />
                </header>
                <p className="summary small">
                  Longest trip <strong>{formatMinutes(v.max_minutes)}</strong> · average {formatMinutes(v.mean_minutes)}
                </p>
                <TravelStrips travel={v.travel} scaleMax={scaleMax} colours={colours} units={units} />
                <div className="actions">
                  <a
                    className="btn primary small"
                    href={v.maps_url || googleMapsSearchUrl(v.lat, v.lng)}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => {
                      e.stopPropagation()
                      track('venue_opened_in_maps', { rank: i + 1 })
                    }}
                  >
                    Open in Google Maps
                  </a>
                  <button
                    className="btn ghost small"
                    onClick={(e) => {
                      e.stopPropagation()
                      share(v)
                    }}
                  >
                    Share
                  </button>
                </div>
              </article>
            </li>
          ))}
        </ol>
      ) : (
        <>
          <p className="muted">These are the fairest areas to meet. Pick a spot inside one of them.</p>
          <ol className="venues">
            {zones.map((z, i) => (
              <li key={i}>
                <article
                  className={i === selected ? 'venue on' : 'venue'}
                  onClick={() => onSelect(i)}
                  tabIndex={0}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect(i)}
                >
                  <header>
                    <span className="rank zone">{i + 1}</span>
                    <div className="venue-title">
                      <h3>Fair area {i + 1}</h3>
                      <p className="muted small">
                        {z.lat.toFixed(4)}, {z.lng.toFixed(4)}
                      </p>
                    </div>
                    <FairnessPill value={z.fairness} />
                  </header>
                  <TravelStrips
                    travel={Object.entries(z.minutes).map(([id, minutes]) => ({
                      member_id: id,
                      name: memberName[id] || 'Someone',
                      minutes,
                      mode: memberMode[id],
                    }))}
                    scaleMax={scaleMax}
                    colours={colours}
                    units={units}
                  />
                  <div className="actions">
                    <a
                      className="btn primary small"
                      href={googleMapsSearchUrl(z.lat, z.lng)}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Open in Google Maps
                    </a>
                  </div>
                </article>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  )
}
