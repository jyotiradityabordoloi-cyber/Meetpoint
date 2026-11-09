import { describe, expect, it } from 'vitest'
import {
  centreOf,
  colourFor,
  formatDistance,
  formatMinutes,
  isoToLocalInput,
  localInputToIso,
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

  it('builds region heading and colours', () => {
    expect(regionHeading({ area: 'Avenidas Novas', locality: 'Lisboa' })).toBe('Avenidas Novas, Lisboa')
    expect(regionHeading({ area: null, locality: null })).toBe(null)
    expect(colourFor(0)).not.toBe(colourFor(1))
    expect(colourFor(12)).toBe(colourFor(0))
  })

  it('finds the centre used to rank search suggestions', () => {
    expect(centreOf([])).toBe(null)
    expect(centreOf([{ lat: 10, lng: 20 }, { lat: 12, lng: 22 }])).toEqual({ lat: 11, lng: 21 })
  })
})
