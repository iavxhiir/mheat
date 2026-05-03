// Thin wrapper around fetch() for the MHEAT API.
//
// Request/response shapes re-use the auto-generated ``paths`` types from
// ``src/api/generated.ts`` (run ``npm run gen:api`` to refresh after a
// backend OpenAPI change). We fall back to local ``./types`` for the
// legacy ``MhwEventCollection`` alias the UI layer expects.

import type { paths } from './api/generated';
import type { MhwEventCollection, OverlayCollection, OverlayKind } from './types';

// Defaults to '/api' so the dev vite-proxy and the same-origin Docker
// deploy keep working untouched. In split deploys (Cloudflare Pages
// frontend + tunnelled FastAPI on a different subdomain) set
// VITE_API_BASE=https://api.example.com/api at build time.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '/api').replace(/\/+$/, '');

// Convenience aliases on the generated paths object.
type GetEventsQuery = NonNullable<
  paths['/api/events']['get']['parameters']['query']
>;
type GetHealth = paths['/api/health']['get']['responses']['200']['content']['application/json'];
type GetSeries = paths['/api/events/{event_id}/series']['get']['responses']['200']['content']['application/json'];

/** Structured backend error: a JSON body with a ``status`` discriminator. */
export class ApiError extends Error {
  status: number;
  /** Discriminator from the response body, e.g. ``climatology_missing``. */
  code: string | null;
  detail: string | null;
  body: Record<string, unknown> | null;
  constructor(status: number, code: string | null, detail: string | null, body: Record<string, unknown> | null) {
    super(detail ?? `${status} ${code ?? 'error'}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.body = body;
  }
}

async function json<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    // Try to parse a structured ``{status, detail, ...}`` body so callers
    // can switch on ``error.code`` instead of regex-matching strings.
    let parsed: Record<string, unknown> | null = null;
    try {
      parsed = text ? (JSON.parse(text) as Record<string, unknown>) : null;
    } catch {
      parsed = null;
    }
    if (parsed && typeof parsed.status === 'string') {
      throw new ApiError(
        r.status,
        parsed.status,
        typeof parsed.detail === 'string' ? parsed.detail : null,
        parsed,
      );
    }
    throw new Error(`${r.status} ${r.statusText}: ${text.slice(0, 200)}`);
  }
  return r.json() as Promise<T>;
}

export interface EventsQuery {
  start?: string;
  end?: string;
  bbox?: [number, number, number, number];
  minCategory?: number;
}

export async function fetchEvents(q: EventsQuery = {}): Promise<MhwEventCollection> {
  const params = new URLSearchParams();
  if (q.start) params.set('start', q.start);
  if (q.end) params.set('end', q.end);
  if (q.bbox) params.set('bbox', q.bbox.join(','));
  if (q.minCategory) params.set('min_category', String(q.minCategory));
  // Typecheck against the generated query shape to catch spelling drift.
  const _typecheck: GetEventsQuery | undefined = q.start
    ? { start: q.start as unknown as GetEventsQuery['start'] }
    : undefined;
  void _typecheck;
  const qs = params.toString();
  return json<MhwEventCollection>(`${API_BASE}/events${qs ? `?${qs}` : ''}`);
}

export async function fetchOverlay(kind: OverlayKind): Promise<OverlayCollection> {
  return json<OverlayCollection>(`${API_BASE}/overlays/${kind}`);
}

export async function fetchHealth(): Promise<GetHealth> {
  return json<GetHealth>(`${API_BASE}/health`);
}

/**
 * /api/readyz — the generated OpenAPI schema may lag behind the backend, so
 * we declare a local intersection covering the fields the UI actually reads.
 */
export type ReadyzResponse = paths['/api/readyz']['get']['responses']['200']['content']['application/json'] & {
  climatology_present?: boolean | null;
  sst_cache_present?: boolean | null;
};

export async function fetchReadyz(): Promise<ReadyzResponse> {
  return json<ReadyzResponse>(`${API_BASE}/readyz`);
}

/** /api/anomaly/extent — temporal extent + colour-scale range. */
export interface AnomalyExtent {
  start: string;
  end: string;
  vmin_degC: number;
  vmax_degC: number;
  n_days?: number;
}

export async function fetchAnomalyExtent(): Promise<AnomalyExtent> {
  return json<AnomalyExtent>(`${API_BASE}/anomaly/extent`);
}

export interface FreshnessResponse {
  cube_start: string | null;
  cube_end: string | null;
  bucket: 'fresh' | 'good' | 'stale' | 'very_stale' | 'unknown';
  last_pull: {
    in_progress: boolean;
    started_at: string | null;
    started_for_range: { start: string; end: string } | null;
    last_success_at: string | null;
    age_seconds: number | null;
    last_error_at: string | null;
    last_error: string | null;
  };
}

/** Snapshot of cache freshness — used by the live badge to render a colour-
    coded "updated X minutes ago" pill. Cheap call (no CMS hit). */
export async function fetchFreshness(): Promise<FreshnessResponse> {
  return json<FreshnessResponse>(`${API_BASE}/freshness`);
}

/** Fire-and-forget background prefetch for a date range. Returns 202.
    Wired to every range / preset click so the events query that follows
    lands on warm cache instead of waiting for an inline CMS pull. */
export async function triggerPrefetch(start: string, end: string): Promise<void> {
  try {
    await fetch(`${API_BASE}/prefetch?start=${start}&end=${end}`, {
      method: 'POST',
    });
  } catch {
    // Best-effort — never block the UI on prefetch failure. The
    // subsequent fetchEvents() will lazy-fill anyway.
  }
}

export type EventSeries = GetSeries;

export async function fetchEventSeries(
  eventId: string,
  lon: number,
  lat: number,
  dateStart?: string,
  dateEnd?: string
): Promise<EventSeries> {
  const params = new URLSearchParams();
  params.set('lon', String(lon));
  params.set('lat', String(lat));
  if (dateStart) params.set('start', dateStart);
  if (dateEnd) params.set('end', dateEnd);
  return json<EventSeries>(
    `${API_BASE}/events/${encodeURIComponent(eventId)}/series?${params.toString()}`
  );
}
