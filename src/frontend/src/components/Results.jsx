import { useRef, useState } from 'react'
import { photoUrl, track } from '../api.js'
import {
  formatDistance,
  formatMeetTime,
  formatMinutes,
  googleMapsSearchUrl,
  modeLabel,
  regionHeading,
  venueTypeLabel,
} from '../format.js'
import Icon, { ModeIcon } from './Icon.jsx'

/** Swipeable photo glimpses of a venue, or a drawn placeholder when there are none. */
function Glimpse({ name, photos, rank, fairness, label }) {
  const [index, setIndex] = useState(0)
  const [failed, setFailed] = useState({})
  const ref = useRef(null)
  const usable = (photos || []).filter((p) => !failed[p.name])

  const onScroll = () => {
    const el = ref.current
    if (el) setIndex(Math.round(el.scrollLeft / Math.max(1, el.clientWidth)))
  }

  return (
    <div className="glimpse">
      {usable.length > 0 ? (
        <>
          <div className="glimpse-track" ref={ref} onScroll={onScroll}>
            {usable.map((p, i) => (
              <img
                key={p.name}
                src={photoUrl(p.name, 640)}
                alt={i === 0 ? `Photo of ${name}` : ''}
                loading="lazy"
                decoding="async"
                draggable={false}
                onError={() => setFailed((f) => ({ ...f, [p.name]: true }))}
              />
            ))}
          </div>
          {usable.length > 1 && (
            <div className="glimpse-dots" aria-hidden="true">
              {usable.map((p, i) => (
                <span key={p.name} className={i === index ? 'on' : ''} />
              ))}
            </div>
          )}
          {usable[index]?.attribution && (
            <span className="glimpse-credit">Photo: {usable[index].attribution}</span>
          )}
        </>
      ) : (
        <div className="glimpse-placeholder" aria-hidden="true">
          <span className="glimpse-letter">{label || (name || '?').trim()[0]}</span>
        </div>
      )}
      <span className="rank-badge">{rank}</span>
      <span className="fair-badge" title="1.00 means everyone travels equally long">
        Fairness {fairness.toFixed(2)}
      </span>
    </div>
  )
}

function TravelStrips({ travel, scaleMax, tags, units }) {
  const longest = Math.max(...travel.map((t) => t.minutes))
  return (
    <ul className="strips">
      {travel.map((t) => {
        const pct = Math.max(4, Math.min(100, (t.minutes / scaleMax) * 100))
        const isLongest = travel.length > 1 && t.minutes === longest
        return (
          <li key={t.member_id} className={isLongest ? 'longest' : ''}>
            <span className="strip-name" title={t.name}>
              <span className="avatar sm" aria-hidden="true">
                {tags[t.member_id] || '?'}
              </span>
              <span className="strip-person">{t.name}</span>
            </span>
            <span className="strip-track">
              <span className="strip-bar" style={{ width: `${pct}%` }} />
            </span>
            <span className="strip-label">
              <strong>{formatMinutes(t.minutes)}</strong>
              {t.meters != null && <span className="dim"> · {formatDistance(t.meters, units)}</span>}
              {t.mode && (
                <span className="mode-mini" title={modeLabel(t.mode)}>
                  <ModeIcon mode={t.mode} size={14} />
                </span>
              )}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export default function Results({ result, tags, selected, onSelect, meetingName, journeys }) {
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

  const cardProps = (i) => ({
    className: i === selected ? 'venue on' : 'venue',
    onClick: () => onSelect(i),
    onKeyDown: (e) => (e.key === 'Enter' || e.key === ' ') && onSelect(i),
    tabIndex: 0,
    'aria-current': i === selected,
  })

  return (
    <section className="results" aria-live="polite">
      <div className="results-head">
        <p className="eyebrow">
          {venues.length ? `${venues.length} fair places to meet` : `${zones.length} fair areas to meet`}
        </p>
        <h2>{heading ? `Around ${heading}` : 'For your group'}</h2>
        {meetTime && (
          <p className="muted small">
            <Icon name="clock" size={14} /> For {meetTime} (your local time)
          </p>
        )}
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
              <article {...cardProps(i)}>
                <Glimpse name={v.name} photos={v.photos} rank={i + 1} fairness={v.fairness} />
                <div className="venue-body">
                  <h3>{v.name}</h3>
                  <p className="venue-meta">
                    <span>{venueTypeLabel(v.type)}</span>
                    {v.rating != null && (
                      <span className="rating">
                        <Icon name="star" size={13} /> {v.rating.toFixed(1)}
                        <span className="dim"> ({v.ratings.toLocaleString()})</span>
                      </span>
                    )}
                    {v.open_now === true && <span>Open now</span>}
                    {v.open_now === false && <span className="closed">Closed now</span>}
                  </p>
                  {v.address && (
                    <p className="venue-address">
                      <Icon name="pin" size={13} /> {v.address}
                    </p>
                  )}
                  <p className="summary">
                    Longest trip <strong>{formatMinutes(v.max_minutes)}</strong>
                    <span className="dim"> · average {formatMinutes(v.mean_minutes)}</span>
                  </p>
                  <TravelStrips travel={v.travel} scaleMax={scaleMax} tags={tags} units={units} />
                  {i === selected && journeys?.status === 'loading' && (
                    <p className="journey-note">Drawing everyone's route…</p>
                  )}
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
                      <Icon name="directions" size={16} /> Open in Google Maps
                    </a>
                    <button
                      className="btn on-dark small"
                      onClick={(e) => {
                        e.stopPropagation()
                        share(v)
                      }}
                    >
                      <Icon name="share" size={15} /> Share
                    </button>
                  </div>
                </div>
              </article>
            </li>
          ))}
        </ol>
      ) : (
        <>
          <p className="muted small">These are the fairest areas to meet. Pick a spot inside one of them.</p>
          <ol className="venues">
            {zones.map((z, i) => (
              <li key={i}>
                <article {...cardProps(i)}>
                  <Glimpse name={`Area ${i + 1}`} label={String(i + 1)} photos={[]} rank={i + 1} fairness={z.fairness} />
                  <div className="venue-body">
                    <h3>Fair area {i + 1}</h3>
                    <p className="venue-meta">
                      <span>
                        {z.lat.toFixed(4)}, {z.lng.toFixed(4)}
                      </span>
                    </p>
                    <TravelStrips
                      travel={Object.entries(z.minutes).map(([id, minutes]) => ({
                        member_id: id,
                        name: memberName[id] || 'Someone',
                        minutes,
                        mode: memberMode[id],
                      }))}
                      scaleMax={scaleMax}
                      tags={tags}
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
                        <Icon name="directions" size={16} /> Open in Google Maps
                      </a>
                    </div>
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
