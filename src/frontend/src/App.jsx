import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, track } from './api.js'
import { colourFor } from './format.js'
import GroupSettings from './components/GroupSettings.jsx'
import People from './components/People.jsx'
import AddPerson from './components/AddPerson.jsx'
import Results from './components/Results.jsx'
import MapView from './components/MapView.jsx'

const DEFAULT_SETTINGS = {
  name: 'Our meetup',
  travel_mode: 'DRIVE',
  meet_at: null,
  venue_types: ['cafe', 'coffee_shop', 'restaurant'],
}

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
  const [pin, setPin] = useState(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [copied, setCopied] = useState(false)

  const creating = useRef(null)
  const settingsRef = useRef(settings)
  const patchTimer = useRef(null)
  const pendingPatch = useRef({})
  settingsRef.current = settings

  const invalidate = useCallback(() => {
    setResult(null)
    setSelected(0)
  }, [])

  // ---- boot: health + invite link ---------------------------------------
  useEffect(() => {
    track('app_open')
    api.health().then(setHealth).catch(() => setHealth({ ok: false, maps_configured: false }))
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
      setPin(null)
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
    setPin(null)
    setNotice(null)
    setError(null)
    invalidate()
  }, [invalidate])

  const colours = useMemo(() => {
    const map = {}
    members.forEach((m, i) => (map[m.id] = colourFor(i)))
    return map
  }, [members])

  const mapsConfigured = health?.maps_configured ?? true
  const canSearch = members.length >= 2 && !searching

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true" />
          <div>
            <h1>MeetPoint</h1>
            <p className="tagline">A meeting place that's fair for everyone to reach</p>
          </div>
        </div>
        <div className="topbar-actions">
          {groupId && (
            <button className="btn ghost" onClick={startOver} title="Start a new meetup">
              New meetup
            </button>
          )}
          <button className="btn secondary" onClick={copyInvite}>
            {copied ? 'Link copied ✓' : 'Copy invite link'}
          </button>
        </div>
      </header>

      <main className="layout">
        <section className="map-wrap" aria-label="Map">
          <MapView
            members={members}
            colours={colours}
            result={result}
            selected={selected}
            onSelect={setSelected}
            pin={pin}
            onMapClick={(p) => setPin(p)}
          />
        </section>

        <aside className="panel">
          {!mapsConfigured && (
            <div className="banner warn" role="status">
              This server has no Google Maps key: address search is off, travel times are estimates and no venues are
              suggested. Add people with your location or a map pin.
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
              <GroupSettings settings={settings} onChange={updateSettings} />

              <div className="card">
                <div className="card-head">
                  <h2>People</h2>
                  <span className="muted">{members.length} / 25</span>
                </div>
                <People members={members} colours={colours} groupMode={settings.travel_mode} onRemove={removeMember} />
                {members.length < 25 && (
                  <AddPerson
                    mapsConfigured={mapsConfigured}
                    groupMode={settings.travel_mode}
                    pin={pin}
                    onClearPin={() => setPin(null)}
                    onAdd={addMember}
                    isFirst={members.length === 0}
                  />
                )}
              </div>

              <button className="btn primary find" disabled={!canSearch} onClick={findMeetingPoint}>
                {searching ? 'Finding fair spots…' : members.length < 2 ? 'Add at least 2 people' : 'Find meeting point'}
              </button>

              {error && (
                <div className="banner error" role="alert">
                  {error.message}
                </div>
              )}

              {searching && <div className="card skeleton" aria-busy="true">Checking travel times for everyone…</div>}

              {result && !searching && (
                <Results
                  result={result}
                  colours={colours}
                  selected={selected}
                  onSelect={setSelected}
                  meetingName={settings.name}
                />
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
