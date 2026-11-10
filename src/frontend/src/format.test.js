import { describe, expect, it } from 'vitest'
import {
  centreOf,
  distanceKm,
  formatDistance,
  formatMinutes,
  initials,
  isoToLocalInput,
  localInputToIso,
  memberTags,
  regionHeading,
} from './format.js'

describe('format', () => {
  it('formats minutes', () => {
    expect(formatMinutes(0.2)).toBe('1 min')
    expect(formatMinutes(14.4)).toBe('14 min')
    expect(formatMinutes(65)).toBe('1 h 05 min')
    expect(formatMinutes(120)).toBe('2 h')
  })

  it('formats distance in local units', () => {
    expect(formatDistance(3900, 'metric', 'en')).toBe('3.9 km')
    expect(formatDistance(420, 'metric', 'en')).toBe('420 m')
    expect(formatDistance(16093.44, 'imperial', 'en')).toBe('10 mi')
    expect(formatDistance(3900, 'imperial', 'en')).toBe('2.4 mi')
  })

  it('round-trips meeting time with offset', () => {
    const iso = localInputToIso('2030-05-01T19:30')
    expect(iso).toMatch(/^2030-05-01T19:30:00[+-]\d\d:\d\d$/)
    expect(isoToLocalInput(iso)).toBe('2030-05-01T19:30')
  })

  it('builds region heading', () => {
    expect(regionHeading({ area: 'Avenidas Novas', locality: 'Lisboa' })).toBe('Avenidas Novas, Lisboa')
    expect(regionHeading({ area: null, locality: null })).toBe(null)
  })

  it('finds the centre used to rank search suggestions', () => {
    expect(centreOf([])).toBe(null)
    expect(centreOf([{ lat: 10, lng: 20 }, { lat: 12, lng: 22 }])).toEqual({ lat: 11, lng: 21 })
  })

  it('tells people apart by initials', () => {
    expect(initials('Sam')).toBe('SA')
    expect(initials('Lerato Mokoena')).toBe('LM')
    expect(initials('  ')).toBe('?')
    const tags = memberTags([
      { id: 'a', name: 'Sam Ali' },
      { id: 'b', name: 'Sara Ahmed' },
      { id: 'c', name: 'Dev' },
    ])
    expect(tags).toEqual({ a: 'SA', b: 'SA2', c: 'DE' })
  })

  it('measures distance between cities', () => {
    const delhi = { lat: 28.61, lng: 77.21 }
    const blr = { lat: 12.97, lng: 77.59 }
    expect(distanceKm(delhi, blr)).toBeGreaterThan(1700)
    expect(distanceKm(delhi, blr)).toBeLessThan(1760)
    expect(distanceKm(delhi, delhi)).toBe(0)
  })
})
