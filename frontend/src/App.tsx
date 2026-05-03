import React, { useEffect, useRef, useState } from 'react';
// MapView swap: WebGL renderer via MapLibre-GL (open-source, no API key)
// for smoother zoom/pan + crisp anomaly raster + no canvas pixelation.
// Legacy Leaflet implementation lives at components/MapView.tsx as a
// fallback should we need to revert before submission.
import { MapViewGL as MapView } from './components/MapViewGL';
import { Timeline } from './components/Timeline';
import { Legend } from './components/Legend';
import { LayerControl } from './components/LayerControl';
import { EventPanel } from './components/EventPanel';
import { EventChart } from './components/EventChart';
import { AboutPage } from './components/AboutPage';
import {
  PlainModeToggle,
  CbSafeToggle,
  usePlainMode,
  categoryDisplay,
  setPlainMode,
} from './components/PlainMode';
import { CoachMarks } from './components/CoachMarks';
import { EventListA11y, buildSelectionAnnouncement } from './components/EventListA11y';
import type maplibregl from 'maplibre-gl';
import {
  fetchEvents,
  fetchOverlay,
  fetchHealth,
  fetchReadyz,
  fetchAnomalyExtent,
  fetchFreshness,
  triggerPrefetch,
  ApiError,
  type AnomalyExtent,
  type FreshnessResponse,
} from './api';
import { useT, useTOptional, SUPPORTED_LOCALES, type Locale } from './i18n';
import type {
  MhwEventCollection,
  MhwEventFeature,
  OverlayCollection,
  LayerToggles,
} from './types';

// --- URL state helpers ---------------------------------------------------
// Default toggle set — only `anomaly` is on. Used by the layers= URL parser
// when the param is missing (legacy share links + fresh visits) and as the
// reference for serialisation (which layers go on the URL vs. omitted).
const DEFAULT_TOGGLES: LayerToggles = {
  anomaly: true,
  aquaculture: false,
  mpa: false,
  seagrass: false,
};
// Keep a stable iteration order for layers= serialisation so two URLs that
// represent the same toggle state byte-compare equal — important for the
// "copy share link" → "paste in another tab" round-trip.
const LAYER_KEYS: (keyof LayerToggles)[] = ['anomaly', 'aquaculture', 'mpa', 'seagrass'];
const VALID_BASEMAPS = new Set(['light', 'dark']);

interface UrlState {
  start?: string;
  end?: string;
  anomalyDate?: string;
  category?: number;
  bbox?: string;
  layers?: LayerToggles;
  view?: 'map-only';
  plain?: boolean;
  basemap?: 'light' | 'dark';
}

/** Parse `?layers=anomaly,mpa` → toggle map. Unknown layer names ignored.
    A *missing* param falls back to {@link DEFAULT_TOGGLES}; an *empty* param
    (`?layers=`) means "all layers off". */
function parseLayers(raw: string | null): LayerToggles | undefined {
  if (raw === null) return undefined;
  const out: LayerToggles = {
    anomaly: false, aquaculture: false, mpa: false, seagrass: false,
  };
  for (const part of raw.split(',').map((p) => p.trim()).filter(Boolean)) {
    if (part in out) out[part as keyof LayerToggles] = true;
  }
  return out;
}

/** Inverse of {@link parseLayers}. Returns `undefined` when toggles match
    the default (so the URL stays clean) and `''` when *all* layers are off. */
function serialiseLayers(t: LayerToggles): string | undefined {
  const sameAsDefault = LAYER_KEYS.every((k) => t[k] === DEFAULT_TOGGLES[k]);
  if (sameAsDefault) return undefined;
  return LAYER_KEYS.filter((k) => t[k]).join(',');
}

function readUrlState(): UrlState {
  try {
    const q = new URLSearchParams(window.location.search);
    const out: UrlState = {};
    const s = q.get('start');
    const e = q.get('end');
    const a = q.get('anomaly');
    const c = q.get('category');
    const b = q.get('bbox');
    if (s) out.start = s;
    if (e) out.end = e;
    if (a) out.anomalyDate = a;
    if (c) out.category = Math.max(1, Math.min(5, Number(c) || 1));
    if (b) out.bbox = b;
    const layers = parseLayers(q.get('layers'));
    if (layers) out.layers = layers;
    const view = q.get('view');
    if (view === 'map-only') out.view = 'map-only';
    const plain = q.get('plain');
    if (plain === 'true' || plain === '1') out.plain = true;
    else if (plain === 'false' || plain === '0') out.plain = false;
    const bm = q.get('basemap');
    if (bm && VALID_BASEMAPS.has(bm)) out.basemap = bm as 'light' | 'dark';
    return out;
  } catch {
    return {};
  }
}

function writeUrlState(state: UrlState) {
  try {
    // Preserve the existing query so we don't clobber params we don't manage
    // (some integrations append e.g. ?utm_source=… on share links).
    const q = new URLSearchParams(window.location.search);
    const setOrDelete = (key: string, val: string | undefined) => {
      if (val === undefined || val === '' && key !== 'layers') q.delete(key);
      else if (val === '' && key === 'layers') q.set(key, ''); // explicit "all off"
      else q.set(key, val);
    };
    setOrDelete('start', state.start);
    setOrDelete('end', state.end);
    setOrDelete('anomaly', state.anomalyDate);
    setOrDelete('category', state.category && state.category > 1 ? String(state.category) : undefined);
    setOrDelete('bbox', state.bbox);
    if (state.layers !== undefined) {
      const enc = serialiseLayers(state.layers);
      if (enc === undefined) q.delete('layers');
      else q.set('layers', enc);
    }
    if (state.view === 'map-only') q.set('view', 'map-only');
    else q.delete('view');
    if (state.plain === true) q.set('plain', 'true');
    else if (state.plain === false) q.delete('plain'); // false is the default
    if (state.basemap === 'light') q.set('basemap', 'light');
    else if (state.basemap === 'dark') q.delete('basemap'); // dark is default
    const qs = q.toString();
    const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
    window.history.replaceState(null, '', url);
  } catch {
    /* noop */
  }
}

const URL_STATE = readUrlState();

/** Compact human age — 47s / 5m / 3h / 2d. Used in the live-badge to
    show "updated 5 min ago" without the verbose Intl.RelativeTimeFormat
    overhead and without leaking locale-formatting into a dense pill. */
function formatAge(secs: number): string {
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

/* Hero stat banner — sits at the top of the sidebar so a reviewer
   reads "X marine heatwaves detected" before scanning the rest of
   the controls. Counts current events; per-category chips for the
   Cat-IV+ severe end (the policy-relevant ones). */
const CAT_COLORS_HERO: Record<number, string> = {
  1: 'var(--cat-1)', 2: 'var(--cat-2)', 3: 'var(--cat-3)',
  4: 'var(--cat-4)', 5: 'var(--cat-5)',
};

/* Animate an integer from a previous value up to `target` over `duration` ms.
   Uses an easing curve so the count slows as it approaches the final figure
   (ease-out cubic). Honours `prefers-reduced-motion: reduce` by snapping to
   the target. Returns the current frame's display number. */
function useCountUp(target: number, duration = 900) {
  const [display, setDisplay] = useState(target);
  const fromRef = useRef(target);
  useEffect(() => {
    const reduced = typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (reduced || target === fromRef.current) {
      setDisplay(target);
      fromRef.current = target;
      return;
    }
    const from = fromRef.current;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      setDisplay(Math.round(from + (target - from) * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
      else fromRef.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return display;
}

export function HeroStat(
  { events, start, end, loading }:
  { events: MhwEventCollection | null; start: string; end: string; loading: boolean },
) {
  // useTOptional so the widget keeps rendering when tests instantiate it
  // without an I18nProvider (existing HeroStat suite). Inside the app
  // proper, the provider supplies real translations.
  const { t } = useTOptional();
  const [plain] = usePlainMode();
  const features = events?.features ?? [];
  const total = features.length;
  const breakdown = features.reduce<Record<number, number>>((acc, f) => {
    const c = (f.properties?.category as number | undefined) ?? 0;
    if (c >= 1 && c <= 5) acc[c] = (acc[c] ?? 0) + 1;
    return acc;
  }, {});
  const severe = (breakdown[3] ?? 0) + (breakdown[4] ?? 0) + (breakdown[5] ?? 0);
  // Animated count-up. While loading, freeze on the previous value
  // (passing the current target keeps the hook stable; the "…" overlay
  // is rendered when loading anyway).
  const animatedTotal = useCountUp(total);
  // Severe-tally caption — Roman numeral default keeps the existing
  // wording reviewers expect; plain mode swaps to "Severe+" so the
  // headline reads as plain English.
  const severeCaption = plain
    ? `${severe} ${t('category.plain3').toLowerCase()}+`
    : `${severe} severe (≥III)`;
  return (
    <div className="hero-stat" role="status" aria-live="polite">
      <div className={`hero-stat-num${total === 0 ? ' is-zero' : ''}`}>
        {loading ? '…' : animatedTotal}
      </div>
      <div className="hero-stat-body">
        <div className="hero-stat-label">
          {total === 1 ? 'marine heatwave' : 'marine heatwaves'}
        </div>
        <div className="hero-stat-meta">
          {start} → {end}
          {!loading && severe > 0 ? ` · ${severeCaption}` : ''}
        </div>
        {!loading && total > 0 && (
          <div className="hero-stat-chips">
            {[5, 4, 3, 2, 1].filter((c) => breakdown[c]).map((c) => (
              <span
                key={c}
                className="hero-stat-chip"
                title={
                  plain
                    ? categoryDisplay(c, true, t, 'long')
                    : `Category ${c}`
                }
              >
                <span
                  className="hero-stat-chip-dot"
                  style={{ background: CAT_COLORS_HERO[c] }}
                />
                <span>{breakdown[c]}</span>
                <span style={{ opacity: 0.7 }}>·</span>
                <span style={{ opacity: 0.85 }}>
                  {plain
                    ? categoryDisplay(c, true, t, 'short')
                    : ['', 'I', 'II', 'III', 'IV', 'V'][c]}
                </span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Tiny pathname-based router so we can ship the /about page without
 * pulling in react-router. Tracks `window.location.pathname` and
 * re-renders on `popstate` so back/forward navigation works.
 */
function useRoute(): { path: string; navigate: (to: string) => void } {
  const [path, setPath] = useState<string>(() =>
    typeof window !== 'undefined' ? window.location.pathname : '/',
  );
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  const navigate = (to: string) => {
    if (typeof window === 'undefined') return;
    window.history.pushState(null, '', to);
    setPath(to);
  };
  return { path, navigate };
}

/**
 * Top-level App — picks between the dashboard and the /about route.
 * Splitting the dashboard into its own component lets us early-return
 * for the about page without violating rules-of-hooks (the dashboard's
 * many useState/useEffect calls only run when we render <Dashboard />).
 */
export default function App() {
  const { path, navigate } = useRoute();
  if (path === '/about' || path.startsWith('/about/')) {
    return <AboutPage onBack={() => navigate('/')} />;
  }
  return <Dashboard navigate={navigate} />;
}

interface DashboardProps {
  navigate: (to: string) => void;
}

function Dashboard({ navigate }: DashboardProps) {
  const { t, locale, setLocale } = useT();
  // Defaults — pre-extent we use a "recent enough" placeholder that the
  // extent-clamp effect (further down) snaps into the live cube range
  // as soon as /api/anomaly/extent resolves. The placeholder is just
  // "last 60 days from today"; the real extent overrides immediately.
  const _today = new Date();
  const _todayIso = _today.toISOString().slice(0, 10);
  const _earlier = new Date(_today.getTime() - 60 * 86_400_000)
    .toISOString().slice(0, 10);
  const [start, setStart] = useState(URL_STATE.start ?? _earlier);
  const [end, setEnd] = useState(URL_STATE.end ?? _todayIso);
  const [anomalyDate, setAnomalyDate] = useState(URL_STATE.anomalyDate ?? _todayIso);
  const [minCategory, setMinCategory] = useState<number>(URL_STATE.category ?? 1);
  const [bbox, setBbox] = useState<[number, number, number, number] | null>(() => {
    if (!URL_STATE.bbox) return null;
    const parts = URL_STATE.bbox.split(',').map(Number);
    if (parts.length !== 4 || parts.some(Number.isNaN)) return null;
    return [parts[0], parts[1], parts[2], parts[3]];
  });
  const [events, setEvents] = useState<MhwEventCollection | null>(null);
  const [selected, setSelected] = useState<MhwEventFeature | null>(null);
  const [version, setVersion] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [eventsErrorCode, setEventsErrorCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingOverlays, setLoadingOverlays] = useState(true);
  const [loadingExtent, setLoadingExtent] = useState(true);
  const [toast, setToast] = useState<string | null>(null);
  const [offlineSince, setOfflineSince] = useState<string | null>(null);
  const [climatologyPresent, setClimatologyPresent] = useState<boolean | null>(null);
  const [extent, setExtent] = useState<AnomalyExtent | null>(null);
  const [freshness, setFreshness] = useState<FreshnessResponse | null>(null);
  const toastTimeout = useRef<number | null>(null);

  // Poll /api/freshness once a minute so the live-badge stays accurate
  // even if the user leaves the tab open for hours. Cheap call (no CMS hit).
  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      fetchFreshness()
        .then((f) => { if (!cancelled) setFreshness(f); })
        .catch(() => { /* keep last value on transient failures */ });
    tick();
    const id = window.setInterval(tick, 60_000);
    return () => { cancelled = true; window.clearInterval(id); };
  }, []);

  // Default to a clean view: SST anomaly raster + the clustered MHW event
  // polygons. Sectoral overlays (aquaculture / MPA / seagrass) are heavy
  // visual noise on first paint and most reviewers want to see them only
  // when they're investigating impact for a specific event — they're one
  // click away in the Map Layers card. URL `?layers=` (UI cross-cut #4)
  // can override the default toggle set on first paint.
  const [toggles, setToggles] = useState<LayerToggles>(URL_STATE.layers ?? DEFAULT_TOGGLES);

  const [overlays, setOverlays] = useState<{
    aquaculture: OverlayCollection | null;
    mpa: OverlayCollection | null;
    seagrass: OverlayCollection | null;
  }>({ aquaculture: null, mpa: null, seagrass: null });

  // Per-layer in-flight indicator → surfaces a tiny spinner inside the
  // layer-toggle row while a lazy fetch is resolving. Keyed by layer name
  // so multiple parallel toggles render independently.
  const [overlayLoading, setOverlayLoading] = useState<Partial<Record<keyof LayerToggles, boolean>>>({});

  // `?view=map-only` deep-link mode — kiosk + multi-monitor users hide all
  // chrome and let the map fill the viewport. Read once on mount; reload to
  // exit (intentionally non-interactive — see UI cross-cut #4 spec).
  const mapOnly = URL_STATE.view === 'map-only';

  // Cross-cut #6 — share the underlying maplibre map handle with the hidden
  // EventListA11y so its <button>s can flyTo() the event centroid on Enter.
  // We use a mutable ref (not state) so a new map handle does not trigger a
  // re-render loop; the EventListA11y reads `.current` only inside an
  // onClick handler.
  const mapRef = useRef<maplibregl.Map | null>(null);
  const handleMapReady = (m: maplibregl.Map) => {
    mapRef.current = m;
  };

  // Toast helper — defined up-front so initial-mount effects (e.g. overlay
  // load) can surface a transient hint when an upstream service is unreachable.
  const showToast = (msg: string) => {
    setToast(msg);
    if (toastTimeout.current) window.clearTimeout(toastTimeout.current);
    toastTimeout.current = window.setTimeout(() => setToast(null), 2500);
  };

  // Bootstrap plain-mode from URL on first mount when `?plain=` is present.
  // Without a URL value we leave the localStorage-backed default intact.
  useEffect(() => {
    if (URL_STATE.plain !== undefined) setPlainMode(URL_STATE.plain);
    // Run once on mount; URL is the source-of-truth for this single boot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Initial health probe + extent fetch. Sectoral overlays are now lazy
  // (UI cross-cut #4): they are fetched on first toggle-on instead of at
  // boot, saving ~21 MB of upfront network on the default-anomaly view.
  useEffect(() => {
    fetchHealth()
      .then((h) => setVersion(h.version))
      .catch(() => setVersion(''));

    // Readyz tells us if the climatology zarr is present.
    fetchReadyz()
      .then((r) =>
        setClimatologyPresent(
          typeof r.climatology_present === 'boolean' ? r.climatology_present : null,
        ),
      )
      .catch(() => setClimatologyPresent(null));

    // Extent → date-picker bounds.
    setLoadingExtent(true);
    fetchAnomalyExtent()
      .then(setExtent)
      .catch(() => setExtent(null))
      .finally(() => setLoadingExtent(false));

    // No more eager overlay fetches — the per-toggle effect below handles
    // them on demand. Keep loadingOverlays at false from the outset so the
    // header "loading…" pill reflects only the still-needed extent fetch.
    setLoadingOverlays(false);
    // showToast / t are stable for the lifetime of the component; the empty
    // dep array intentionally runs this once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Lazy overlay fetch: whenever a sectoral toggle goes ON for the first
  // time AND we haven't cached its overlay yet, fetch it. Subsequent
  // off→on toggles are instant (cache hit). A per-overlay loading flag
  // surfaces a tiny spinner inside the layer-toggle row.
  useEffect(() => {
    type SectoralKey = 'aquaculture' | 'mpa' | 'seagrass';
    const SECTORAL: SectoralKey[] = ['aquaculture', 'mpa', 'seagrass'];
    for (const k of SECTORAL) {
      if (!toggles[k]) continue;
      if (overlays[k] !== null) continue;
      if (overlayLoading[k]) continue;
      setOverlayLoading((s) => ({ ...s, [k]: true }));
      fetchOverlay(k)
        .then((data) => {
          setOverlays((prev) => ({ ...prev, [k]: data }));
        })
        .catch((err) => {
          console.error('Overlay load failed', err);
          showToast(t('toast.overlaysUnavailable'));
        })
        .finally(() => {
          setOverlayLoading((s) => {
            const { [k]: _drop, ...rest } = s;
            void _drop;
            return rest;
          });
        });
    }
    // overlays/overlayLoading are read inside but we only want to react to
    // toggle changes — guards above handle the no-op case for repeat fires.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toggles.aquaculture, toggles.mpa, toggles.seagrass]);

  // Online/offline banner
  useEffect(() => {
    const onOffline = () => setOfflineSince(new Date().toISOString().slice(0, 16).replace('T', ' '));
    const onOnline = () => setOfflineSince(null);
    window.addEventListener('offline', onOffline);
    window.addEventListener('online', onOnline);
    return () => {
      window.removeEventListener('offline', onOffline);
      window.removeEventListener('online', onOnline);
    };
  }, []);

  // Layer-toggle keyboard shortcuts: 1=anomaly, 2=MPA, 3=seagrass, 4=aqua.
  // Mirrors the hidden affordances in MapView (h=home, b=bbox, Space=play
  // on the time slider) so a reviewer can drive the whole demo from the
  // keyboard. Bail out if focus is in a form field so the shortcuts don't
  // fight typed input.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const t = ev.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(t.tagName)) return;
      const map: Record<string, keyof LayerToggles> = {
        '1': 'anomaly',
        '2': 'mpa',
        '3': 'seagrass',
        '4': 'aquaculture',
      };
      const layer = map[ev.key];
      if (layer) {
        ev.preventDefault();
        setToggles((prev) => ({ ...prev, [layer]: !prev[layer] }));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Re-fetch events whenever the date window, min-category, or bbox changes.
  // Skip while the extent is still loading: the hard-coded 2022 defaults
  // would 503 against the live 2026 cache, which a reviewer would see as a
  // brief error flash before the extent-clamp effect snaps the dates back
  // into range. Waiting one tick avoids the spurious request.
  //
  // Smart-live behaviour: every range change fires a non-blocking
  // /api/prefetch BEFORE the events query so the backend has already
  // started pulling new data from Copernicus by the time the events
  // request lands. The events lazy-fill then hits warmer cache and feels
  // dramatically more responsive on a fresh range.
  useEffect(() => {
    if (loadingExtent) return;
    triggerPrefetch(start, end);              // fire-and-forget
    setLoading(true);
    setError(null);
    setEventsErrorCode(null);
    fetchEvents({ start, end, minCategory, bbox: bbox ?? undefined })
      .then(setEvents)
      .catch((e) => {
        if (e instanceof ApiError) {
          setEventsErrorCode(e.code);
          setError(e.detail ?? e.message);
          if (e.code === 'cms_unavailable' || e.code === 'sst_cache_missing') {
            showToast(t('toast.cmsUnavailable'));
          }
        } else {
          setError(String(e));
        }
      })
      .finally(() => {
        setLoading(false);
        // Refresh the live-badge after the query so the user sees the new
        // freshness state without waiting for the next poll tick.
        fetchFreshness().then(setFreshness).catch(() => {});
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [start, end, minCategory, bbox, loadingExtent]);

  // Plain-mode subscriber so URL stays in sync when the user toggles it.
  // We re-use usePlainMode() (already a context-free subscribe pattern) so
  // both the toggle widget and the URL writer see the same value.
  const [plainMode] = usePlainMode();

  // Cross-cut #6 — polite live-region announcement when the selection
  // changes from any source (map click, EventListA11y keyboard activation,
  // side-panel re-select). One re-rendered string per change so AT
  // engines emit exactly one announcement.
  const [selectionAnnouncement, setSelectionAnnouncement] = useState('');
  useEffect(() => {
    setSelectionAnnouncement(
      buildSelectionAnnouncement(selected, plainMode, t),
    );
    // We re-run when the selected event id OR the plain-mode flag OR the
    // locale changes. The `t` function identity changes with locale.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, plainMode, locale]);

  // Basemap state lifted from MapViewGL so URL `?basemap=` round-trips
  // across reloads + share links. URL wins over localStorage on first
  // boot; subsequent user changes flow back into both.
  const [basemap, setBasemap] = useState<'light' | 'dark'>(() => {
    if (URL_STATE.basemap) return URL_STATE.basemap;
    try {
      const v = window.localStorage?.getItem('mheat-basemap');
      return v === 'light' || v === 'dark' ? v : 'dark';
    } catch {
      return 'dark';
    }
  });
  useEffect(() => {
    try { window.localStorage?.setItem('mheat-basemap', basemap); }
    catch { /* noop */ }
  }, [basemap]);

  // Keep the URL in sync with current filters so "copy link" is honest.
  useEffect(() => {
    writeUrlState({
      start,
      end,
      anomalyDate,
      category: minCategory,
      bbox: bbox ? bbox.join(',') : undefined,
      layers: toggles,
      view: mapOnly ? 'map-only' : undefined,
      plain: plainMode ? true : false,
      basemap,
    });
  }, [start, end, anomalyDate, minCategory, bbox, toggles, mapOnly, plainMode, basemap]);

  // Once the cube extent is known, snap defaults into range. The hard-coded
  // 2022 strings below are only ever seen in the brief moment before the
  // extent fetch resolves.
  useEffect(() => {
    if (!extent) return;
    const clamp = (v: string) => (v < extent.start ? extent.start : v > extent.end ? extent.end : v);
    setStart((s) => clamp(s));
    setEnd((e) => clamp(e));
    setAnomalyDate((d) => clamp(d));
    // We only run this once the extent is known; subsequent extent changes
    // (e.g. midnight rollover) are intentionally ignored.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extent?.start, extent?.end]);

  const handleShare = async () => {
    const url = window.location.href;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(url);
      } else {
        const ta = document.createElement('textarea');
        ta.value = url;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      showToast(t('toast.linkCopied'));
    } catch {
      showToast(t('toast.copyFailed'));
    }
  };

  const handleCsvDownload = () => {
    const q = new URLSearchParams();
    q.set('start', start);
    q.set('end', end);
    q.set('min_category', String(minCategory));
    window.open(`/api/events.csv?${q.toString()}`, '_blank');
  };

  // Compute UI signals once per render.
  const showClimatologyBanner = climatologyPresent === false;
  const dateMin = extent?.start;
  const dateMax = extent?.end;
  const initialLoading = loadingExtent || loadingOverlays;

  return (
    <div className={`app${mapOnly ? ' app--map-only' : ''}`}>
      {showClimatologyBanner && !mapOnly && (
        <div
          className="climatology-banner"
          role="alert"
          data-testid="climatology-missing-banner"
        >
          <strong>{t('header.climatologyMissingTitle')}</strong>{' '}
          {t('header.climatologyMissingHint')}
        </div>
      )}
      {offlineSince && !mapOnly && (
        <div className="offline-banner" role="status" aria-live="polite">
          {t('status.offline', { ts: offlineSince })}
        </div>
      )}
      {!mapOnly && (
        <a href="#main" className="skip-to-content">Skip to main content</a>
      )}
      {!mapOnly && (
      <header className="header">
        <div className="brand">
          <span className="brand-mark" aria-label="MHEAT">MHEAT</span>
          <span className="brand-sub">{t('brand.subtitle')}</span>
          <span className="brand-trl" title="Technology Readiness Level 7 — system prototype demonstration in operational environment">TRL&nbsp;7</span>
        </div>
        <div className="header-meta">
          {version && <span className="pill">v{version}</span>}
          {(loading || initialLoading) && (
            <span className="pill pill-loading" role="status" data-testid="loading-pill">
              {t('header.loading')}
            </span>
          )}
          {bbox && (
            <span
              className="pill pill-bbox"
              title={t('header.selectedRegion')}
            >
              {t('header.selectedRegion')}: {bbox[0].toFixed(1)}E-{bbox[2].toFixed(1)}E,{' '}
              {bbox[1].toFixed(1)}N-{bbox[3].toFixed(1)}N
            </span>
          )}
          <label className="lang-switch" aria-label={t('header.language')}>
            <select
              value={locale}
              onChange={(e) => setLocale(e.target.value as Locale)}
              className="select lang-select"
              aria-label={t('header.language')}
            >
              {SUPPORTED_LOCALES.map((l) => (
                <option key={l} value={l}>
                  {l.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="header-btn share-btn"
            onClick={handleShare}
            aria-label={t('header.shareAria')}
            title={t('header.shareTitle')}
          >
            {t('header.share')}
          </button>
          <button
            type="button"
            className="header-btn"
            onClick={handleCsvDownload}
            aria-label={t('header.downloadCsvAria')}
            title={t('header.downloadCsvTitle')}
          >
            {t('header.downloadCsv')}
          </button>
          <a
            className="header-btn"
            href="/api/docs"
            target="_blank"
            rel="noreferrer"
            aria-label={t('header.apiDocsAria')}
          >
            {t('header.apiDocs')}
          </a>
        </div>
      </header>
      )}

      <div className="layout">
        {!mapOnly && (
        <aside className="sidebar" aria-label="Controls">
          <HeroStat events={events} start={start} end={end} loading={loading} />
          <Timeline
            start={start}
            end={end}
            onChange={(s, e) => {
              setStart(s);
              setEnd(e);
            }}
            min={dateMin}
            max={dateMax}
          />
          <div
            className={`live-badge live-badge-${freshness?.bucket ?? 'unknown'}`}
            data-testid="live-badge"
            title={
              freshness?.last_pull?.last_success_at
                ? `Last live pull: ${freshness.last_pull.last_success_at}` +
                  (freshness.last_pull.in_progress ? ' · pull in progress' : '')
                : t('header.liveModeTitle')
            }
          >
            <span className="live-dot" aria-hidden="true" />
            <span className="live-text">{t('header.liveMode')}</span>
            {freshness?.last_pull?.age_seconds != null && (
              <span className="live-age">
                · {formatAge(freshness.last_pull.age_seconds)}
              </span>
            )}
            {freshness?.last_pull?.in_progress && (
              <span className="live-spinner" aria-label="Pulling fresh data" />
            )}
          </div>
          <div className="block">
            <div className="block-title">{t('anomalyDate.title')}</div>
            <input
              type="date"
              className="select"
              value={anomalyDate}
              // In live mode, prefer the global extent over the user-chosen
              // start/end (which may be a sub-range). In demo mode, keep the
              // existing pattern of clamping to the timeline window.
              min={dateMin ?? start}
              max={dateMax ?? end}
              onChange={(e) => setAnomalyDate(e.target.value)}
              aria-label={t('anomalyDate.aria')}
            />
          </div>
          <div className="block">
            <div className="block-title">{t('category.title')}</div>
            <select
              value={minCategory}
              onChange={(e) => setMinCategory(Number(e.target.value))}
              className="select"
              aria-label={t('category.aria')}
            >
              <option value={1}>{t('category.cat1')}</option>
              <option value={2}>{t('category.cat2')}</option>
              <option value={3}>{t('category.cat3')}</option>
              <option value={4}>{t('category.cat4')}</option>
              <option value={5}>{t('category.cat5')}</option>
            </select>
          </div>
          <LayerControl value={toggles} onChange={setToggles} loading={overlayLoading} />
          <div className="toggle-row">
            <PlainModeToggle />
            <CbSafeToggle />
          </div>
          <Legend
            events={events}
            minCategory={minCategory}
            onCategoryClick={(c) => setMinCategory(c)}
          />
          <EventPanel event={selected} />
          {error && (
            <div className="error" role="alert">
              {eventsErrorCode === 'dates_required'
                ? t('status.datesRequired')
                : `${t('status.error')}: ${error}`}
            </div>
          )}
          {events && !error && (
            <div className="stats" role="status" aria-live="polite">
              <div>
                <strong>{events.features.length}</strong>
                <div className="stats-label">
                  {events.features.length === 1
                    ? t('stats.eventsSingular')
                    : t('stats.eventsPlural')}
                </div>
              </div>
              <span className="stats-icon" aria-hidden="true" />
            </div>
          )}
        </aside>
        )}

        <main id="main" className="map-wrap">
          <MapView
            events={events}
            overlays={overlays}
            toggles={toggles}
            onSelect={setSelected}
            selectedId={selected?.id ?? null}
            anomalyDate={anomalyDate}
            bbox={bbox}
            onBboxDraw={setBbox}
            climatologyMissing={climatologyPresent === false}
            climatologyMissingMessage={t('status.climatologyMissing')}
            // Scope the on-map time scrubber to the user's selected
            // time window (start/end in the sidebar), not the full
            // 30-year cube extent. Otherwise pressing play loops
            // through three decades and most days have no events
            // visible — confusing reviewers. The user can widen the
            // sidebar window to widen the scrubber.
            anomalyMinDate={start}
            anomalyMaxDate={end}
            onAnomalyDateChange={setAnomalyDate}
            anomalyVmin={extent?.vmin_degC ?? -5}
            anomalyVmax={extent?.vmax_degC ?? 5}
            basemap={basemap}
            onBasemapChange={setBasemap}
            mapOnly={mapOnly}
            onMapReady={handleMapReady}
            canvasAriaLabel={t('map.canvasAria')}
          />
          {/* Cross-cut #6 — hidden tabbable event list. Sits AFTER the
              <MapView> in DOM order so Tab through the map → next stop is
              the first event. The list is visually-hidden via CSS clip-path
              but remains in the accessibility tree + tab order. */}
          <EventListA11y
            events={events}
            onSelect={setSelected}
            mapRef={mapRef}
          />
          {!mapOnly && <EventChart events={events} />}
        </main>
      </div>

      {!mapOnly && (
      <footer className="footer">
        <span className="footer-group">
          <span className="footer-label">{t('footer.data')}</span>
          Copernicus Marine, EMODnet, EEA Natura 2000
        </span>
        <span className="footer-group">
          <span className="footer-label">{t('footer.method')}</span>
          Hobday et al. 2016
        </span>
        <span className="footer-group">
          <span className="footer-label">{t('footer.standards')}</span>
          <a href="/api/ogcapi" target="_blank" rel="noreferrer">OGC&nbsp;API</a>
          <a href="/api/stac/collections" target="_blank" rel="noreferrer">STAC</a>
        </span>
        <span className="footer-group">
          <span className="footer-label">{t('footer.docs')}</span>
          <a href="/api/docs" target="_blank" rel="noreferrer">API</a>
        </span>
        <span className="footer-group">
          <a
            href="/about"
            onClick={(e) => {
              e.preventDefault();
              navigate('/about');
            }}
            aria-label={t('footer.aboutAria')}
          >
            {t('footer.about')}
          </a>
        </span>
      </footer>
      )}

      {toast && (
        <div className="toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}
      {/* Cross-cut #6 — single shared selection announcer. Placed at the
          end of the layout so it's late in the DOM (announcers should not
          steal Tab focus) and visually hidden via CSS. Updated on every
          selection change (map click, hidden-list keyboard activation,
          side-panel re-select). */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-live-region"
        data-testid="selection-announcer"
      >
        {selectionAnnouncement}
      </div>
      {/* CoachMarks deferred-mount so its 600ms timeout never fires while
          the App test suite is in flight (would race the test teardown
          and trigger a transitive MapView import in the still-mocked
          module graph). Keep it always-on in production via this hook.
          Suppressed in map-only mode (kiosk) since coach marks expect
          chrome to point at. */}
      {!mapOnly && <DeferredCoachMarks />}
    </div>
  );
}

/** Wraps <CoachMarks /> behind a mount-after-window flag so the coach
    overlay timer doesn't fire during the synchronous test-render phase
    of App tests. The visible behaviour in production is identical. */
function DeferredCoachMarks() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setReady(true), 50);
    return () => window.clearTimeout(id);
  }, []);
  if (!ready) return null;
  return <CoachMarks />;
}
