import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, track } from './api.js'
import { centreOf, distanceKm, loadExactLocation, memberStyles, memberTags, saveExactLocation } from './format.js'
import GroupSettings from './components/GroupSettings.jsx'
import People from './components/People.jsx'
import AddPerson from './components/AddPerson.jsx'
import Results from './components/Results.jsx'
import MapView from './components/MapView.jsx'
import LogoMark from './components/LogoMark.jsx'

const DEFAULT_SETTINGS = {
  name: 'Our meetup',
  travel_mode: 'DRIVE',
  meet_at: null,
  venue_types: ['cafe', 'coffee_shop', 'restaurant'],
}

const DEFAULT_TITLE = document.title || 'MeetPoint – Find a fair place to meet, in any city'

function readGroupParam() {
  const g = new URLSearchParams(window.location.search).get('g')
  return g && /^[0-9a-f]{6,24}$/i.test(g) ? g : null
}

function writeGroupParam(id) {
  const url = new URL(window.location.href)
  if (id) url.searchParams.set('g', id)
  else url.searchParams.delete('g')
  window.history.replaceState(null, '', url)
}

function inviteUrl(id) {
  const url = new URL(window.location.href)
  url.search = ''
  url.hash = ''
  url.searchParams.set('g', id)
  return url.toString()
}

function toError(e) {
  if (e instanceof ApiError) return { code: e.code, message: e.message }
  return { code: 'UNKNOWN', message: 'Something went wrong. Please try again.' }
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [booting, setBooting] = useState(true)
  const [groupId, setGroupId] = useState(null)
  const [settings, setSettings] = useState(DEFAULT_SETTINGS)
  const [members, setMembers] = useState([])
  const [result, setResult] = useState(null)
  const [selected, setSelected] = useState(0)
  const [draft, setDraft] = useState(null) // starting point being added: {lat, lng, label, source}
  const [pickMode, setPickMode] = useState(false)
  const [approx, setApprox] = useState(null) // where the map starts: IP guess, or exact if the visitor shared it
  const [ipGuess, setIpGuess] = useState(null) // city-level guess from the IP address
  const [exactState, setExactState] = useState('idle') // idle | busy | denied | error
  const [settingsOpen, setSettingsOpen] = useState(null) // null = automatic
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [copied, setCopied] = useState(false)

  const creating = useRef(null)
  const settingsRef = useRef(settings)
  const patchTimer = useRef(null)
  const pendingPatch = useRef({})
  const resultsRef = useRef(null)
  settingsRef.current = settings

  const invalidate = useCallback(() => {
    setResult(null)
    setSelected(0)
  }, [])

  // ---- boot: health + invite link ---------------------------------------
  useEffect(() => {
    track('app_open')
    // Free hosting puts the API to sleep when idle; say so instead of looking frozen.
    const WAKING = 'Waking up the server. On free hosting this can take up to a minute the first time…'
    const slow = setTimeout(() => setNotice(WAKING), 3500)
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth({ ok: false, maps_configured: false }))
      .finally(() => {
        clearTimeout(slow)
        setNotice((n) => (n === WAKING ? null : n))
      })
    // City-level location from the IP address: ranks search suggestions and
    // centres the first map view. Never stored. If the visitor already shared
    // their exact location in this tab, that wins.
    const remembered = loadExactLocation()
    if (remembered) setApprox({ ...remembered, city: null, source: 'exact' })
    api
      .locate()
      .then((r) => {
        if (!r?.located) return
        setIpGuess(r)
        setApprox((cur) => {
          if (cur?.source !== 'exact') return r
          // keep the exact point, but borrow the IP city name if it's nearby
          return distanceKm(cur, r) < 40 ? { ...cur, city: r.city } : cur
        })
      })
      .catch(() => {})
    const g = readGroupParam()
    if (!g) {
      setBooting(false)
      return
    }
    ;(async () => {
      try {
        const group = await api.getGroup(g)
        setGroupId(group.id)
        setSettings({
          name: group.name,
          travel_mode: group.travel_mode,
          meet_at: group.meet_at,
          venue_types: group.venue_types,
        })
        setMembers(group.members)
        // Show the last saved result only if nothing changed since it was made.
        try {
          const [latest] = await api.recommendations(group.id, 1)
          if (latest && new Date(latest.created_at) >= new Date(group.updated_at)) setResult(latest)
        } catch {
          /* history is optional */
        }
      } catch (e) {
        if (e instanceof ApiError && e.code === 'GROUP_NOT_FOUND') {
          writeGroupParam(null)
          setNotice("This group doesn't exist or was deleted, so we've started a fresh meetup.")
        } else {
          setError(toError(e))
        }
      } finally {
        setBooting(false)
      }
    })()
  }, [])

  // ---- keep the people list fresh when friends join from the link --------
  useEffect(() => {
    if (!groupId) return
    const refresh = async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const g = await api.getGroup(groupId)
        setMembers((prev) => {
          const same =
            prev.length === g.members.length && prev.every((m, i) => m.id === g.members[i].id)
          if (!same) invalidate()
          return same ? prev : g.members
        })
      } catch {
        /* ignore background refresh errors */
      }
    }
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('focus', refresh)
    const t = setInterval(refresh, 20000)
    return () => {
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('focus', refresh)
      clearInterval(t)
    }
  }, [groupId, invalidate])

  // ---- group creation is lazy: first person or first invite -------------
  const ensureGroup = useCallback(async () => {
    if (groupId) return groupId
    if (!creating.current) {
      creating.current = api
        .createGroup({ ...settingsRef.current, name: settingsRef.current.name.trim() || 'Our meetup' })
        .then((g) => {
          setGroupId(g.id)
          writeGroupParam(g.id)
          track('group_created')
          return g.id
        })
        .finally(() => {
          creating.current = null
        })
    }
    return creating.current
  }, [groupId])

  const flushPatch = useCallback(
    async (id) => {
      const patch = pendingPatch.current
      pendingPatch.current = {}
      if (!id || Object.keys(patch).length === 0) return
      try {
        await api.patchGroup(id, patch)
      } catch (e) {
        setError(toError(e))
      }
    },
    [],
  )

  const updateSettings = useCallback(
    (patch) => {
      setSettings((s) => ({ ...s, ...patch }))
      invalidate()
      setError(null)
      if (!groupId) return // saved when the group is created
      const clean = { ...patch }
      if ('name' in clean && !clean.name.trim()) delete clean.name // don't save an empty name
      if (Object.keys(clean).length === 0) return
      pendingPatch.current = { ...pendingPatch.current, ...clean }
      clearTimeout(patchTimer.current)
      const delay = 'name' in patch ? 600 : 0
      patchTimer.current = setTimeout(() => flushPatch(groupId), delay)
    },
    [groupId, invalidate, flushPatch],
  )

  // ---- people ------------------------------------------------------------
  const addMember = useCallback(
    async (m, method) => {
      setError(null)
      const id = await ensureGroup()
      const created = await api.addMember(id, m)
      setMembers((prev) => [...prev, created])
      setDraft(null)
      setPickMode(false)
      invalidate()
      track('member_added', { method })
      return created
    },
    [ensureGroup, invalidate],
  )

  const removeMember = useCallback(
    async (mid) => {
      setError(null)
      try {
        await api.removeMember(groupId, mid)
      } catch (e) {
        if (!(e instanceof ApiError && e.code === 'MEMBER_NOT_FOUND')) {
          setError(toError(e))
          return
        }
      }
      setMembers((prev) => prev.filter((m) => m.id !== mid))
      invalidate()
    },
    [groupId, invalidate],
  )

  // ---- search ------------------------------------------------------------
  const findMeetingPoint = useCallback(async () => {
    if (!groupId || members.length < 2) return
    setError(null)
    setSearching(true)
    track('search_started', { members: members.length })
    const t0 = performance.now()
    try {
      clearTimeout(patchTimer.current)
      await flushPatch(groupId)
      const rec = await api.meetingPoint(groupId)
      setResult(rec)
      setSelected(0)
      requestAnimationFrame(() => resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
      track('search_succeeded', {
        venues: rec.venues.length,
        estimated: rec.estimated,
        ms: Math.round(performance.now() - t0),
      })
    } catch (e) {
      setError(toError(e))
      track('search_failed', { code: e.code })
    } finally {
      setSearching(false)
    }
  }, [groupId, members.length, flushPatch])

  const copyInvite = useCallback(async () => {
    setError(null)
    try {
      const id = await ensureGroup()
      const url = inviteUrl(id)
      if (navigator.share && window.matchMedia('(pointer: coarse)').matches) {
        await navigator.share({ title: settingsRef.current.name, text: 'Add where you are starting from:', url })
      } else {
        await navigator.clipboard.writeText(url)
        setCopied(true)
        setTimeout(() => setCopied(false), 2500)
      }
      track('invite_shared')
    } catch (e) {
      if (e?.name !== 'AbortError') setError(toError(e))
    }
  }, [ensureGroup])

  const startOver = useCallback(() => {
    writeGroupParam(null)
    setGroupId(null)
    setSettings(DEFAULT_SETTINGS)
    setMembers([])
    setDraft(null)
    setPickMode(false)
    setSettingsOpen(null)
    setNotice(null)
    setError(null)
    invalidate()
  }, [invalidate])

  const tags = useMemo(() => memberTags(members), [members])
  const styles = useMemo(() => memberStyles(members), [members])

  // Everyone's route to the selected venue (or fair area), for the map.
  const [journeys, setJourneys] = useState(null) // {key, status: 'loading'|'ready'|'error', data}
  const journeyCache = useRef(new Map())
  useEffect(() => {
    const target = result ? (result.venues?.length ? result.venues[selected] : result.zones?.[selected]) : null
    if (!groupId || !target) {
      setJourneys(null)
      return
    }
    const key = `${result.id}:${target.lat},${target.lng}`
    const hit = journeyCache.current.get(key)
    if (hit) {
      setJourneys({ key, status: 'ready', data: hit })
      return
    }
    let live = true
    setJourneys({ key, status: 'loading', data: null })
    api
      .journeys(groupId, { lat: target.lat, lng: target.lng })
      .then((data) => {
        journeyCache.current.set(key, data)
        if (live) setJourneys({ key, status: 'ready', data })
      })
      .catch(() => live && setJourneys({ key, status: 'error', data: null }))
    return () => {
      live = false
    }
  }, [groupId, result, selected])

  // Page title per meetup, and keep private meetup links (?g=...) out of
  // search results: they contain people's starting points.
  useEffect(() => {
    const name = settings.name?.trim()
    document.title = groupId && name ? `${name} · MeetPoint` : DEFAULT_TITLE
    let robots = document.querySelector('meta[name="robots"]')
    if (groupId) {
      if (!robots) {
        robots = document.createElement('meta')
        robots.name = 'robots'
        document.head.appendChild(robots)
      }
      robots.content = 'noindex, nofollow'
    } else if (robots) {
      robots.remove()
    }
  }, [groupId, settings.name])

  // "Use my exact location": fixes a wrong IP guess (common on mobile data,
  // VPNs and office networks). Asks the browser's permission only on tap.
  const useExactLocation = useCallback(() => {
    if (!('geolocation' in navigator)) {
      setExactState('error')
      return
    }
    setExactState('busy')
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const loc = { lat: pos.coords.latitude, lng: pos.coords.longitude }
        saveExactLocation(loc)
        const city = ipGuess && distanceKm(loc, ipGuess) < 40 ? ipGuess.city : null
        setApprox({ ...loc, city, source: 'exact' })
        setExactState('idle')
        track('exact_location_used')
      },
      (err) => setExactState(err.code === err.PERMISSION_DENIED ? 'denied' : 'error'),
      { enableHighAccuracy: false, timeout: 12000, maximumAge: 5 * 60 * 1000 },
    )
  }, [ipGuess])

  const mapsConfigured = health?.maps_configured ?? true
  const searchAvailable = health ? Boolean(health.search) : true
  const memberCentre = useMemo(() => centreOf(members), [members])
  const bias = memberCentre || (approx ? { lat: approx.lat, lng: approx.lng } : null)
  const biasLabel = members.length
    ? 'your group'
    : approx
      ? approx.source === 'exact'
        ? 'you'
        : approx.city || 'your area'
      : null
  const settingsCollapsed = settingsOpen === null ? members.length > 0 : !settingsOpen

  // On phones the map is above the form: bring it into view when picking.
  useEffect(() => {
    if (pickMode && window.matchMedia('(max-width: 820px)').matches) window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [pickMode])

  const onMapClick = useCallback(
    (p) => {
      if (!pickMode) return
      setDraft({ lat: p.lat, lng: p.lng, label: `Pin at ${p.lat.toFixed(4)}, ${p.lng.toFixed(4)}`, source: 'pin' })
      setPickMode(false)
    },
    [pickMode],
  )
  const canSearch = members.length >= 2 && !searching

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <LogoMark className="logo" />
          <div>
            <h1>MeetPoint</h1>
            <p className="tagline">A meeting place that's fair for everyone to reach</p>
          </div>
        </div>
        <div className="topbar-actions">
          {groupId && (
            <button className="btn ghost" onClick={startOver} title="Start a new meetup">
              <span className="lbl-long">New meetup</span>
              <span className="lbl-short" aria-hidden="true">New</span>
            </button>
          )}
          <button className="btn secondary" onClick={copyInvite} aria-label="Copy invite link">
            {copied ? (
              'Copied ✓'
            ) : (
              <>
                <span className="lbl-long">Copy invite link</span>
                <span className="lbl-short">Invite</span>
              </>
            )}
          </button>
        </div>
      </header>

      <main className="layout">
        <section className="map-wrap" aria-label="Map">
          <MapView
            members={members}
            tags={tags}
            styles={styles}
            result={result}
            selected={selected}
            onSelect={setSelected}
            draft={draft}
            approx={approx}
            exactState={exactState}
            onUseExactLocation={useExactLocation}
            pickMode={pickMode}
            onMapClick={onMapClick}
            journeys={journeys}
          />
        </section>

        <aside className="panel">
          {!mapsConfigured && (
            <div className="banner warn" role="status">
              {health?.venues === 'osm'
                ? 'Places come from OpenStreetMap (no ratings or photos yet) and travel times are estimated from distance.'
                : 'Running without a Google Maps key: travel times are estimates and the fairest areas are shown instead of venues.'}
            </div>
          )}
          {notice && (
            <div className="banner info" role="status">
              {notice}
              <button className="link" onClick={() => setNotice(null)}>
                Dismiss
              </button>
            </div>
          )}

          {booting ? (
            <div className="card skeleton" aria-busy="true">
              Loading meetup…
            </div>
          ) : (
            <>
              <GroupSettings
                settings={settings}
                onChange={updateSettings}
                collapsed={settingsCollapsed}
                onToggle={members.length ? () => setSettingsOpen(settingsCollapsed) : undefined}
              />

              <div className="card">
                <div className="card-head">
                  <h2>People</h2>
                  <span className="muted">{members.length} / 25</span>
                </div>
                <People members={members} tags={tags} styles={styles} groupMode={settings.travel_mode} onRemove={removeMember} />
                {members.length < 25 && (
                  <AddPerson
                    groupMode={settings.travel_mode}
                    searchAvailable={searchAvailable}
                    bias={bias}
                    biasLabel={biasLabel}
                    draft={draft}
                    onDraft={(d) => {
                      setDraft(d)
                      setPickMode(false)
                    }}
                    pickMode={pickMode}
                    onPickMode={setPickMode}
                    onAdd={addMember}
                    isFirst={members.length === 0}
                  />
                )}
              </div>

              <div className={canSearch || result ? 'panel-actions sticky' : 'panel-actions'}>
                {error && (
                  <div className="banner error" role="alert">
                    {error.message}
                  </div>
                )}
                <button className="btn primary find" disabled={!canSearch} onClick={findMeetingPoint}>
                  {searching
                    ? 'Finding fair spots…'
                    : members.length < 2
                      ? `Add at least 2 people (${members.length}/2)`
                      : result
                        ? 'Search again'
                        : `Find meeting point for ${members.length}`}
                </button>
              </div>

              {searching && <div className="card skeleton" aria-busy="true">Checking travel times for everyone…</div>}

              {result && !searching && (
                <div ref={resultsRef} className="results-anchor">
                <Results
                  result={result}
                  tags={tags}
                  styles={styles}
                  selected={selected}
                  journeys={journeys}
                  onSelect={setSelected}
                  meetingName={settings.name}
                />
                </div>
              )}
            </>
          )}
          <footer className="foot muted">
            Groups are deleted after 30 days. Anyone with the invite link can view and edit this meetup.
          </footer>
        </aside>
      </main>
    </div>
  )
}
