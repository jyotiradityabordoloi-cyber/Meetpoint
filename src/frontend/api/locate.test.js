import { describe, expect, it } from 'vitest'
import handler from './locate.js'

const call = async (headers) => (await handler(new Request('https://x/api/locate', { headers }))).json()

describe('/api/locate on Vercel', () => {
  it('returns the visitor city from Vercel headers, rounded', async () => {
    expect(
      await call({
        'x-vercel-ip-latitude': '12.9716',
        'x-vercel-ip-longitude': '77.5946',
        'x-vercel-ip-city': 'Bengaluru',
        'x-vercel-ip-country': 'IN',
      }),
    ).toEqual({ located: true, lat: 12.97, lng: 77.59, city: 'Bengaluru', country_code: 'IN', source: 'vercel' })
  })

  it('decodes encoded city names', async () => {
    const r = await call({ 'x-vercel-ip-latitude': '-23.55', 'x-vercel-ip-longitude': '-46.63', 'x-vercel-ip-city': 'S%C3%A3o%20Paulo' })
    expect(r.city).toBe('São Paulo')
  })

  it('says not located without usable headers', async () => {
    expect(await call({})).toEqual({ located: false })
    expect(await call({ 'x-vercel-ip-latitude': 'abc', 'x-vercel-ip-longitude': '1' })).toEqual({ located: false })
    expect(await call({ 'x-vercel-ip-latitude': '0', 'x-vercel-ip-longitude': '0' })).toEqual({ located: false })
  })

  it('is never cached by shared caches', async () => {
    const res = await handler(new Request('https://x/api/locate'))
    expect(res.headers.get('cache-control')).toContain('no-store')
  })
})
