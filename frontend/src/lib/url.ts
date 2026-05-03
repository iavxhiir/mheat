/**
 * Pure helpers for the URL ↔ app-state contract.
 *
 * The map bounding box and the "share view" button round-trip through the
 * URL query string, so the parse / serialise logic is unit-tested in
 * isolation from the React tree. Keep this module dependency-free — it
 * runs in jsdom tests without importing Leaflet or React.
 */

export type BBox = [number, number, number, number];

export interface ViewState {
  bbox?: BBox;
  start?: string;
  end?: string;
  minCategory?: number;
}

function parseFloats(csv: string, n: number): number[] | null {
  const parts = csv.split(',').map((p) => p.trim());
  if (parts.length !== n) return null;
  const out: number[] = [];
  for (const p of parts) {
    const v = Number(p);
    if (!Number.isFinite(v)) return null;
    out.push(v);
  }
  return out;
}

/**
 * Parse a bbox query-param value.
 *
 * Accepts ``lon_min,lat_min,lon_max,lat_max``. Returns ``null`` when the
 * input is missing, malformed, or degenerate (min ≥ max on either axis).
 */
export function parseBbox(raw: string | null | undefined): BBox | null {
  if (!raw) return null;
  const nums = parseFloats(raw, 4);
  if (!nums) return null;
  const [lonMin, latMin, lonMax, latMax] = nums;
  if (lonMin >= lonMax || latMin >= latMax) return null;
  if (latMin < -90 || latMax > 90 || lonMin < -180 || lonMax > 180) return null;
  return [lonMin, latMin, lonMax, latMax];
}

/** Serialise a bbox back to the canonical URL param format. */
export function serialiseBbox(bbox: BBox | null | undefined): string | undefined {
  if (!bbox) return undefined;
  return bbox.map((n) => Number.isInteger(n) ? String(n) : n.toFixed(2)).join(',');
}

/** Build a URLSearchParams string for the current view — ordered, deterministic. */
export function serialiseView(view: ViewState): string {
  const params = new URLSearchParams();
  if (view.start) params.set('start', view.start);
  if (view.end) params.set('end', view.end);
  const bbox = serialiseBbox(view.bbox);
  if (bbox) params.set('bbox', bbox);
  if (view.minCategory && view.minCategory > 1) {
    params.set('min_category', String(view.minCategory));
  }
  return params.toString();
}

/** Inverse of :func:`serialiseView`. Unknown params are ignored. */
export function parseView(search: string): ViewState {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  const out: ViewState = {};
  const isValidIsoDate = (s: string | null): boolean => {
    if (!s || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
    // A syntactically valid ISO date like "2022-99-99" still parses to NaN.
    const [y, m, d] = s.split('-').map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d));
    return (
      dt.getUTCFullYear() === y &&
      dt.getUTCMonth() === m - 1 &&
      dt.getUTCDate() === d
    );
  };
  const start = params.get('start');
  const end = params.get('end');
  if (isValidIsoDate(start)) out.start = start!;
  if (isValidIsoDate(end)) out.end = end!;
  const bbox = parseBbox(params.get('bbox'));
  if (bbox) out.bbox = bbox;
  const minCat = Number(params.get('min_category'));
  if (Number.isInteger(minCat) && minCat >= 1 && minCat <= 5) out.minCategory = minCat;
  return out;
}

/** Human header string e.g. ``6.0E-14.0E, 38.0N-43.0N``. */
export function formatBboxHeader(bbox: BBox): string {
  const [lonMin, latMin, lonMax, latMax] = bbox;
  const lonLabel = (lon: number) => `${Math.abs(lon).toFixed(1)}${lon >= 0 ? 'E' : 'W'}`;
  const latLabel = (lat: number) => `${Math.abs(lat).toFixed(1)}${lat >= 0 ? 'N' : 'S'}`;
  return `${lonLabel(lonMin)}-${lonLabel(lonMax)}, ${latLabel(latMin)}-${latLabel(latMax)}`;
}
