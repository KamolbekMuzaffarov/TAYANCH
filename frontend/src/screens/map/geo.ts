// Geometry is fetched and projected exactly once per page load; polls only change fills.
import { geoArea, geoMercator, geoPath } from 'd3-geo'
import type { Feature, FeatureCollection, MultiPolygon, Polygon, Position } from 'geojson'

export const W = 1000
export const H = 640
export type Box = [number, number, number, number] // x, y, w, h
export const FULL: Box = [0, 0, W, H]

export interface GeoDistrict { code: string; name: string; soato: string; d: string; cx: number; cy: number }
export interface GeoRegion { soato: string; name: string; short: string; d: string; cx: number; cy: number; box: Box }
export interface Geo {
  districts: GeoDistrict[]; regions: GeoRegion[]
  districtByCode: Map<string, GeoDistrict>; regionBySoato: Map<string, GeoRegion>
}

type Shape = Polygon | MultiPolygon
type F = Feature<Shape, Record<string, unknown>>

/** Normalise apostrophes to the correct Uzbek Latin letters: oʻ gʻ (U+02BB), tutuq belgisi ʼ (U+02BC). */
export function uz(s: string): string {
  return s.replace(/([oOgG])[‘’`']/g, '$1ʻ').replace(/[‘’`']/g, 'ʼ')
}

export function shortRegion(name: string): string {
  return name.replace(/ viloyati$/, '').replace(/ Respublikasi$/, '').replace(/ shahri$/, ' sh.')
}

/** d3-geo expects clockwise exterior rings on the sphere; RFC 7946 files are the opposite. */
function rewind(f: F): F {
  const fix = (rings: Position[][]): Position[][] =>
    geoArea({ type: 'Polygon', coordinates: rings }) > 2 * Math.PI ? rings.map((r) => r.slice().reverse()) : rings
  const g = f.geometry
  const geometry: Shape = g.type === 'Polygon'
    ? { type: 'Polygon', coordinates: fix(g.coordinates) }
    : { type: 'MultiPolygon', coordinates: g.coordinates.map(fix) }
  return { ...f, geometry }
}

async function getJson(url: string): Promise<FeatureCollection<Shape, Record<string, unknown>>> {
  try {
    const r = await fetch(url)
    if (!r.ok) throw new Error(String(r.status))
    return await r.json()
  } catch { throw new Error('Xarita yuklanmadi') }
}

let cache: Promise<Geo> | null = null

export function loadGeo(): Promise<Geo> {
  if (!cache) {
    cache = (async () => {
      const [dj, rj] = await Promise.all([getJson('/geo/districts.json'), getJson('/geo/regions.json')])
      const dFeats = dj.features.map(rewind)
      const rFeats = rj.features.map(rewind)
      const projection = geoMercator().fitExtent([[18, 18], [W - 18, H - 18]], { type: 'FeatureCollection', features: rFeats })
      const path = geoPath(projection)

      const districts: GeoDistrict[] = dFeats.map((f) => {
        const p = f.properties
        const c = Array.isArray(p.centroid) ? projection(p.centroid as [number, number]) : null
        const [cx, cy] = c ?? path.centroid(f)
        return { code: String(p.district_code), name: uz(String(p.name ?? '')), soato: String(p.region_soato), d: path(f) ?? '', cx, cy }
      })
      const regions: GeoRegion[] = rFeats.map((f) => {
        const p = f.properties
        const [[x0, y0], [x1, y1]] = path.bounds(f)
        const [cx, cy] = path.centroid(f)
        // pad the bounds and grow them to the canvas aspect so the zoom never distorts
        let w = (x1 - x0) * 1.3, h = (y1 - y0) * 1.3
        if (w / h > W / H) h = (w * H) / W; else w = (h * W) / H
        const name = uz(String(p.name_uz ?? p.name ?? ''))
        return {
          soato: String(p.soato), name, short: shortRegion(name), d: path(f) ?? '', cx, cy,
          box: [(x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - h / 2, w, h],
        }
      })
      return {
        districts, regions,
        districtByCode: new Map(districts.map((d) => [d.code, d])),
        regionBySoato: new Map(regions.map((r) => [r.soato, r])),
      }
    })()
    cache.catch(() => { cache = null })
  }
  return cache
}
