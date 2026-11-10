// Shared by the map and the suggestion thumbnails.

// Map tiles: OpenStreetMap by default (fine for light use, with attribution);
// set VITE_TILE_URL to a commercial provider for heavy traffic.
export const TILE_URL = import.meta.env.VITE_TILE_URL || 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
export const TILE_ATTRIBUTION =
  import.meta.env.VITE_TILE_ATTRIBUTION || '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

export function tileUrl(z, x, y) {
  const n = 2 ** z
  const wx = ((x % n) + n) % n
  return TILE_URL.replace('{s}', 'abc'[(wx + y) % 3])
    .replace('{z}', String(z))
    .replace('{x}', String(wx))
    .replace('{y}', String(y))
    .replace('{r}', '')
}

/** Fractional Web-Mercator tile coordinates of a point at zoom z. */
export function tileXY(lat, lng, z) {
  const n = 2 ** z
  const rad = (lat * Math.PI) / 180
  return {
    x: ((lng + 180) / 360) * n,
    y: ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n,
  }
}

// Small single-colour glyphs for the kind of place (24x24 stroke paths).
export const KIND_GLYPH = {
  restaurant: '<path d="M7 3v8a2 2 0 0 0 2 2v8M5 3v5M9 3v5M16 21V3c-2 1-3 3.5-3 7 0 2 1 3 3 3"/>',
  cafe: '<path d="M4 9h12v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V9zM16 10h1.5a2.5 2.5 0 0 1 0 5H16M8 3v3M12 3v3"/>',
  bar: '<path d="M5 4h14l-7 8zM12 12v8M8 20h8"/>',
  park: '<path d="M12 21v-6M12 3l5 7h-3l4 5H6l4-5H7z"/>',
  mall: '<path d="M5 8h14l-1 12H6zM9 8V6a3 3 0 0 1 6 0v2"/>',
  cinema:
    '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M7.5 5v14M16.5 5v14M3.5 9.5h4M3.5 14.5h4M16.5 9.5h4M16.5 14.5h4"/>',
  place: '<circle cx="12" cy="10" r="3"/><path d="M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11z"/>',
}
const KIND_OF_TYPE = {
  cafe: 'cafe',
  coffee_shop: 'cafe',
  bakery: 'cafe',
  bar: 'bar',
  pub: 'bar',
  park: 'park',
  shopping_mall: 'mall',
  movie_theater: 'cinema',
}
export function kindGlyph(type) {
  if (!type) return KIND_GLYPH.place
  if (KIND_OF_TYPE[type]) return KIND_GLYPH[KIND_OF_TYPE[type]]
  if (type.includes('restaurant') || type.includes('food')) return KIND_GLYPH.restaurant
  if (type.includes('bar') || type.includes('pub')) return KIND_GLYPH.bar
  if (type.includes('cafe') || type.includes('coffee')) return KIND_GLYPH.cafe
  return KIND_GLYPH.place
}
