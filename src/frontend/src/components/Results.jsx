import { useRef, useState } from 'react'
import { photoUrl, track } from '../api.js'
import {
  formatDistance,
  formatMeetTime,
  formatMinutes,
  googleMapsSearchUrl,
  modeLabel,
  priceLabel,
  regionHeading,
  todayHoursIndex,
  venueTypeLabel,
} from '../format.js'
import Icon, { ModeIcon } from './Icon.jsx'

/** 0–5 stars with partial fill, plus the number and review count. */
export function Stars({ rating, count, size = 14 }) {
  if (rating == null) return null
  const pct = Math.max(0, Math.min(100, (rating / 5) * 100))
  return (
    <span className="stars" title={`${rating.toFixed(1)} out of 5 from ${count?.toLocaleString() ?? 0} reviews`}>
      <span className="stars-track" aria-hidden="true">
        <span className="stars-base">
          {[0, 1, 2, 3, 4].map((i) => (
            <Icon key={i} name="star" size={size} />
          ))}
        </span>
        <span className="stars-fill" style={{ width: `${pct}%` }}>
          {[0, 1, 2, 3, 4].map((i) => (
            <Icon key={i} name="star" size={size} />
          ))}
        </span>
      </span>
      <strong>{rating.toFixed(1)}</strong>
      {count != null && <span className="dim">({count.toLocaleString()})</span>}
    </span>
  )
}

/** Photo, or a drawn placeholder with the first letter. */
function Thumb({ name, photos, rank, label }) {
  const [failed, setFailed] = useState(false)
  const photo = !failed && photos?.[0]
  return (
    <span className="thumb">
      {photo ? (
        <img src={photoUrl(photo.name, 240)} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
      ) : (
        <span className="thumb-letter" aria-hidden="true">
          {label || (name || '?').trim()[0]}
        </span>
      )}
      <span className="thumb-rank">{rank}</span>
    </span>
  )
}

/** Swipeable photo glimpses in the detail panel. */
function Gallery({ name, photos }) {
  const [index, setIndex] = useState(0)
  const [failed, setFailed] = useState({})
  const ref = useRef(null)
  const usable = (photos || []).filter((p) => !failed[p.name])
  if (!usable.length) return null
  return (
    <div className="gallery">
      <div
        className="gallery-track"
        ref={ref}
        onScroll={() => {
          const el = ref.current
          if (el) setIndex(Math.round(el.scrollLeft / Math.max(1, el.clientWidth)))
        }}
      >
        {usable.map((p, i) => (
          <img
            key={p.name}
            src={photoUrl(p.name, 800)}
            alt={i === 0 ? `Photo of ${name}` : ''}
            loading="lazy"
            decoding="async"
            draggable={false}
            onError={() => setFailed((f) => ({ ...f, [p.name]: true }))}
          />
        ))}
      </div>
      {usable.length > 1 && (
        <div className="gallery-dots" aria-hidden="true">
          {usable.map((p, i) => (
            <span key={p.name} className={i === index ? 'on' : ''} />
          ))}
        </div>
      )}
      {usable[index]?.attribution && <span className="gallery-credit">Photo: {usable[index].attribution}</span>}
    </div>
  )
}

function TravelStrips({ travel, scaleMax, tags, styles, units }) {
  const longest = Math.max(...travel.map((t) => t.minutes))
  return (
    <ul className="strips">
      {travel.map((t) => {
        const pct = Math.max(4, Math.min(100, (t.minutes / scaleMax) * 100))
        const st = styles[t.member_id]
        const isLongest = travel.length > 1 && t.minutes === longest
        return (
          <li key={t.member_id}>
            <span className="strip-name" title={t.name}>
              <span className={`avatar sm ${st?.className || ''}`} aria-hidden="true">
                {tags[t.member_id] || '?'}
              </span>
              <span className="strip-person">{t.name}</span>
            </span>
            <span className="strip-track">
              <span className={`strip-bar ${st?.className || ''}`} style={{ width: `${pct}%` }} />
            </span>
            <span className="strip-label">
              <strong>{formatMinutes(t.minutes)}</strong>
              {t.meters != null && <span className="dim"> · {formatDistance(t.meters, units)}</span>}
              {t.mode && (
                <span className="mode-mini" title={modeLabel(t.mode)}>
                  <ModeIcon mode={t.mode} size={14} />
                </span>
              )}
              {isLongest && <span className="longest-tag">longest</span>}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function OpeningHours({ hours }) {
  if (!hours?.length) return null
  const today = todayHoursIndex()
  return (
    <details className="hours">
      <summary>
        <Icon name="clock" size={15} /> <span>{hours[today] || 'Opening hours'}</span>
        <Icon name="chevron" size={14} className="hours-chevron" />
      </summary>
      <ul>
        {hours.map((h, i) => (
          <li key={i} className={i === today ? 'today' : ''}>
            {h}
          </li>
        ))}
      </ul>
    </details>
  )
}

export default function Results({ result, tags, styles, selected, onSelect, meetingName, journeys }) {
  const units = result.region?.units || 'metric'
  const country = result.region?.country_code
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

  const items = venues.length
    ? venues.map((v) => ({ kind: 'venue', key: v.id, v }))
    : zones.map((z, i) => ({ kind: 'zone', key: `z${i}`, z }))

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

      <ol className="venue-list">
        {items.map((it, i) => {
          const on = i === selected
          if (it.kind === 'zone') {
            const z = it.z
            const travel = Object.entries(z.minutes).map(([id, minutes]) => ({
              member_id: id,
              name: memberName[id] || 'Someone',
              minutes,
              mode: memberMode[id],
            }))
            return (
              <li key={it.key} className={on ? 'venue-item on' : 'venue-item'}>
                <button type="button" className="venue-row" onClick={() => onSelect(i)} aria-expanded={on}>
                  <Thumb name={`Area ${i + 1}`} label={String(i + 1)} rank={i + 1} />
                  <span className="row-info">
                    <span className="row-name">Fair area {i + 1}</span>
                    <span className="row-meta">
                      Longest trip {formatMinutes(Math.max(...travel.map((t) => t.minutes)))} · fairness {z.fairness.toFixed(2)}
                    </span>
                  </span>
                  <Icon name="chevron" size={18} className="row-chevron" />
                </button>
                {on && (
                  <div className="venue-detail">
                    <TravelStrips travel={travel} scaleMax={scaleMax} tags={tags} styles={styles} units={units} />
                    <div className="actions">
                      <a className="btn primary small" href={googleMapsSearchUrl(z.lat, z.lng)} target="_blank" rel="noreferrer">
                        <Icon name="directions" size={16} /> Open in Google Maps
                      </a>
                    </div>
                  </div>
                )}
              </li>
            )
          }

          const v = it.v
          const price = priceLabel(v.price_level, country)
          const mapsHref = v.maps_url || googleMapsSearchUrl(v.lat, v.lng)
          return (
            <li key={it.key} className={on ? 'venue-item on' : 'venue-item'}>
              <button type="button" className="venue-row" onClick={() => onSelect(i)} aria-expanded={on}>
                <Thumb name={v.name} photos={v.photos} rank={i + 1} />
                <span className="row-info">
                  <span className="row-name">{v.name}</span>
                  {v.rating != null ? (
                    <Stars rating={v.rating} count={v.ratings} />
                  ) : (
                    <span className="row-type">
                      {v.type === 'movie_theater' && <Icon name="film" size={13} />} {venueTypeLabel(v.type)}
                    </span>
                  )}
                  <span className="row-meta">
                    {v.open_now === true && <span className="open-now">Open now</span>}
                    {v.open_now === false && <span className="closed">Closed now</span>}
                    {price && <span>{price}</span>}
                    {v.rating != null && <span>{venueTypeLabel(v.type)}</span>}
                    <span>
                      Longest <strong>{formatMinutes(v.max_minutes)}</strong>
                    </span>
                  </span>
                </span>
                <Icon name="chevron" size={18} className="row-chevron" />
              </button>

              {on && (
                <div className="venue-detail">
                  <Gallery name={v.name} photos={v.photos} />
                  <div className="detail-head">
                    <h3>{v.name}</h3>
                    <span className="fair-pill" title="1.00 means everyone travels equally long">
                      Fairness {v.fairness.toFixed(2)}
                    </span>
                  </div>
                  {(v.rating != null || price || v.open_now != null) && (
                    <p className="detail-meta">
                      <Stars rating={v.rating} count={v.ratings} size={15} />
                      {v.open_now === true && <span className="open-now">Open now</span>}
                      {v.open_now === false && <span className="closed">Closed now</span>}
                      {price && <span>{price}</span>}
                      <span>{venueTypeLabel(v.type)}</span>
                    </p>
                  )}
                  <ul className="info-rows">
                    {v.address && (
                      <li>
                        <Icon name="pin" size={16} /> <span>{v.address}</span>
                      </li>
                    )}
                    {v.phone && (
                      <li>
                        <Icon name="phone" size={16} />
                        <a href={`tel:${(v.phone_intl || v.phone).replace(/\s+/g, '')}`}>{v.phone}</a>
                      </li>
                    )}
                    {v.website && (
                      <li>
                        <Icon name="globe" size={16} />
                        <a href={v.website} target="_blank" rel="noreferrer">
                          {v.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}
                        </a>
                      </li>
                    )}
                  </ul>
                  <OpeningHours hours={v.hours} />

                  <p className="summary">
                    Longest trip <strong>{formatMinutes(v.max_minutes)}</strong>
                    <span className="dim"> · average {formatMinutes(v.mean_minutes)}</span>
                  </p>
                  <TravelStrips travel={v.travel} scaleMax={scaleMax} tags={tags} styles={styles} units={units} />
                  {journeys?.status === 'loading' && <p className="journey-note">Drawing everyone's route…</p>}

                  <div className="actions">
                    <a
                      className="btn primary small"
                      href={mapsHref}
                      target="_blank"
                      rel="noreferrer"
                      onClick={() => track('venue_opened_in_maps', { rank: i + 1 })}
                    >
                      <Icon name="directions" size={16} /> Open in Google Maps
                    </a>
                    {v.phone && (
                      <a className="btn ghost small" href={`tel:${(v.phone_intl || v.phone).replace(/\s+/g, '')}`}>
                        <Icon name="phone" size={15} /> Call
                      </a>
                    )}
                    <button className="btn ghost small" onClick={() => share(v)}>
                      <Icon name="share" size={15} /> Share
                    </button>
                  </div>
                  {v.rating == null && (
                    <p className="source-note">Ratings, photos and opening hours appear here once a Google Maps key is set.</p>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
