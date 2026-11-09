import { useEffect, useId, useRef, useState } from 'react'
import { api, ApiError } from '../api.js'
import Icon from './Icon.jsx'

const MIN_CHARS = 2
const DEBOUNCE_MS = 220

function newSession() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

/**
 * Search-as-you-type for a place or address (accessible combobox).
 *
 * Suggestions are ranked near `bias` ({lat, lng}): the group's other members,
 * or the viewer's approximate location. Calls onPick({lat, lng, label}).
 */
export default function PlaceSearch({ bias, biasLabel, onPick, disabled, autoFocus }) {
  const [query, setQuery] = useState('')
  const [items, setItems] = useState([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState(null)
  const session = useRef(newSession())
  const abort = useRef(null)
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const listId = useId()

  // Fetch suggestions (debounced, previous request cancelled).
  useEffect(() => {
    const q = query.trim()
    if (q.length < MIN_CHARS) {
      setItems([])
      setLoading(false)
      setMsg(null)
      return
    }
    setLoading(true)
    const t = setTimeout(async () => {
      abort.current?.abort()
      const ctrl = new AbortController()
      abort.current = ctrl
      try {
        const res = await api.autocomplete(
          q,
          { bias, session: session.current, lang: navigator.language },
          ctrl.signal,
        )
        setItems(res.suggestions)
        setActive(res.suggestions.length ? 0 : -1)
        setOpen(true)
        setMsg(res.suggestions.length ? null : `No places match "${q}". Try adding the area or city, or pick on the map.`)
      } catch (e) {
        if (e?.name === 'AbortError') return
        setItems([])
        setMsg(e instanceof ApiError ? e.message : 'Place search failed. Pick on the map instead.')
      } finally {
        if (abort.current === ctrl) setLoading(false)
      }
    }, DEBOUNCE_MS)
    return () => clearTimeout(t)
    // bias is an object; key on its rounded value to avoid refetch loops
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, bias?.lat?.toFixed(2), bias?.lng?.toFixed(2)])

  useEffect(() => () => abort.current?.abort(), [])

  const pick = async (s) => {
    setOpen(false)
    setMsg(null)
    try {
      let loc
      if (s.lat != null && s.lng != null) {
        loc = { lat: s.lat, lng: s.lng, label: [s.main, s.secondary].filter(Boolean).join(', ') }
      } else {
        setLoading(true)
        const p = await api.place(s.id, session.current)
        loc = { lat: p.lat, lng: p.lng, label: p.label || [s.main, s.secondary].filter(Boolean).join(', ') }
      }
      onPick(loc)
      setQuery('')
      setItems([])
      session.current = newSession() // one billing session per pick
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Couldn't load that place. Try another one.")
    } finally {
      setLoading(false)
    }
  }

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown' && items.length) {
      e.preventDefault()
      setOpen(true)
      setActive((i) => (i + 1) % items.length)
    } else if (e.key === 'ArrowUp' && items.length) {
      e.preventDefault()
      setActive((i) => (i <= 0 ? items.length - 1 : i - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault() // never submit the surrounding form from here
      if (open && items[active]) pick(items[active])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  const showList = open && items.length > 0

  // Keep the open list on screen (the panel may need to scroll a little).
  useEffect(() => {
    if (showList) listRef.current?.scrollIntoView({ block: 'nearest' })
  }, [showList, items])

  return (
    <div className={showList ? 'place-search open' : 'place-search'}>
      <div className={`search-box${loading ? ' loading' : ''}`}>
        <span className="search-icon" aria-hidden="true">
          <Icon name="search" size={17} />
        </span>
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showList && active >= 0 ? `${listId}-${active}` : undefined}
          aria-label="Search for a starting point"
          autoComplete="off"
          spellCheck={false}
          autoFocus={autoFocus}
          disabled={disabled}
          value={query}
          placeholder="Search a place, street or area…"
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
          }}
          onKeyDown={onKeyDown}
          onFocus={() => items.length > 0 && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
        />
        {query && (
          <button
            type="button"
            className="clear-btn"
            aria-label="Clear search"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setQuery('')
              setItems([])
              inputRef.current?.focus()
            }}
          >
            <Icon name="close" size={14} />
          </button>
        )}
      </div>

      {showList && (
        <ul className="suggestions" role="listbox" id={listId} ref={listRef}>
          {items.map((s, i) => (
            <li
              key={`${s.id}-${i}`}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'suggestion on' : 'suggestion'}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(s)}
            >
              <span className="pin-glyph" aria-hidden="true">
                <Icon name="pin" size={16} />
              </span>
              <span className="suggestion-text">
                <strong>{s.main}</strong>
                {s.secondary && <span className="muted">{s.secondary}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}

      {msg && <p className="hint warn">{msg}</p>}
      {!msg && !query && biasLabel && <p className="hint">Suggestions near {biasLabel} first.</p>}
    </div>
  )
}
