/* MapLibre-GL implementation of the dashboard map.
 *
 * Same props/test-ids as the legacy Leaflet MapView so App.tsx swaps
 * one import line and the existing test surface continues to work.
 *
 * What's preserved:
 *  - bubble-plot events (CircleMarker → Circle layer with intensity-scaled radius)
 *  - smoothed SST anomaly raster (image source via /api/anomaly?date=…)
 *  - sectoral overlays (GeoJSON sources + fill/circle layers)
 *  - layer toggles (visibility property)
 *  - on-map event counter pill (kept as DOM overlay)
 *  - on-map time scrubber (MapTimeSlider, unchanged)
 *  - keyboard help overlay
 *  - selection ring + bring-to-front for selected event
 *
 * What's still TODO (parity gaps with legacy MapView; can land later):
 *  - BBox draw (use @mapbox/mapbox-gl-draw or custom)
 *  - Home/Scale/Coordinate-readout custom controls
 *  - Light/Dark basemap toggle (currently dark only)
 */

import { useMemo, useRef, useEffect, useState, useCallback } from 'react';
import Map, { Source, Layer, NavigationControl, ScaleControl, AttributionControl, useControl } from 'react-map-gl/maplibre';
import maplibregl, { type MapLayerMouseEvent, type IControl } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
// @mapbox/mapbox-gl-draw works against MapLibre when given the shim — the
// Draw class accepts an IControl interface MapLibre also implements.
// We intentionally accept the missing-types from the (mapbox-targeted) lib
// because the runtime contract matches.
// @ts-expect-error — package ships its own .d.ts but mismatches MapLibre Map type
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import type { Feature } from 'geojson';
import type {
  MhwEventCollection,
  MhwEventFeature,
  OverlayCollection,
  LayerToggles,
} from '../types';
import { CATEGORY_COLORS, CATEGORY_SHORT } from './Legend';
import { MapTimeSlider } from './MapTimeSlider';
import { KeyboardHelp } from './KeyboardHelp';
import { useT } from '../i18n';
import { usePlainMode, categoryDisplay } from './PlainMode';
import { eventsAsBubblesGeoJson } from './MapView';
import { API_BASE } from '../api';

interface Props {
  events: MhwEventCollection | null;
  overlays: {
    aquaculture: OverlayCollection | null;
    mpa: OverlayCollection | null;
    seagrass: OverlayCollection | null;
  };
  toggles: LayerToggles;
  onSelect: (f: MhwEventFeature) => void;
  selectedId?: string | number | null;
  anomalyDate: string | null;
  anomalyBounds?: [[number, number], [number, number]];
  bbox?: [number, number, number, number] | null;
  onBboxDraw?: (bbox: [number, number, number, number] | null) => void;
  climatologyMissing?: boolean;
  climatologyMissingMessage?: string;
  anomalyMinDate?: string;
  anomalyMaxDate?: string;
  onAnomalyDateChange?: (date: string) => void;
  anomalyVmin?: number;
  anomalyVmax?: number;
  /**
   * Cross-cut #6 — surface the underlying maplibre map handle to the
   * parent so the hidden EventListA11y can call flyTo() and so the
   * canvas can be given a descriptive aria-label for keyboard users.
   */
  onMapReady?: (map: maplibregl.Map) => void;
  /**
   * Cross-cut #6 — text used as the aria-label on the .maplibregl-canvas
   * element so screen-readers announce the canvas as something other than
   * an unlabelled <canvas>. Localised by the parent (App.tsx).
   */
  canvasAriaLabel?: string;
  /**
   * Cross-cut #4 — controlled basemap value. When omitted, MapViewGL falls
   * back to its legacy localStorage-backed internal state. When supplied,
   * the parent (App.tsx) owns the value so URL `?basemap=` stays in sync.
   */
  basemap?: 'light' | 'dark';
  /** Cross-cut #4 — paired setter for {@link basemap}. */
  onBasemapChange?: (next: 'light' | 'dark') => void;
  /**
   * Cross-cut #4 — kiosk / multi-monitor "map fills viewport" mode. Hides
   * every on-map control except the navigation (zoom) widget and the time
   * scrubber so the map can deep-link as `?view=map-only`.
   */
  mapOnly?: boolean;
}

// Default view: all of Europe (Atlantic shelf → Baltic → Med). Same coords
// as the Leaflet MapView for visual parity across the migration.
const EU_CENTER: [number, number] = [12.0, 50.0]; // [lon, lat] (MapLibre order)
const EU_ZOOM = 3.5;

// Med default raster bounds — the real Copernicus Med MFC cube extent.
// The cube's lat/lon arrays store CELL CENTERS at lat 30.25..46 and
// lon -6..36.25 with 0.0625° spacing. We shift the painted bounds south
// by ~1 full cell (0.0625°) on top of the half-cell extension. Empirical
// fit on 2026-05-03: pure half-cell still read as "the raster sits north
// of the actual coastline" against the CARTO basemap; another half-cell
// south puts it visually in line with the basemap's coastline at zoom 4-7.
// Total south offset vs cell-centre baseline: 0.09375° ≈ 10 km.
const _HALF_CELL = 0.0625 / 2;
// Empirical fit on 2026-05-03: 70 km south was too far (raster overshot
// onto African coast), 20 km back north lands at ~50 km south total.
// Per-iteration constant, dialled by the user against the live basemap.
// Each 0.09° = 10 km at this latitude. Current = 0.4225 = 47 km.
const _SOUTH_NUDGE = 0.0625 + 0.09 + 0.45 - 0.18;  // ~50 km south total
const DEFAULT_ANOM_BOUNDS_GL: [[number, number], [number, number]] = [
  [-6.0 - _HALF_CELL, 30.25 - _HALF_CELL - _SOUTH_NUDGE],  // [west, south]
  [36.25 + _HALF_CELL, 46.0 + _HALF_CELL - _SOUTH_NUDGE],  // [east, north]
];

// CARTO Dark Matter / Voyager raster styles — no API key needed, same
// providers the Leaflet build used. Composed inline so MapLibre treats
// them as raster basemaps; vector-tile upgrade (Protomaps) is a future.
type BasemapStyle = 'dark' | 'light';
const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> '
  + '&middot; &copy; <a href="https://carto.com/attributions">CARTO</a> '
  + '&middot; SST <a href="https://marine.copernicus.eu">Copernicus Marine</a>';
function makeBasemapStyle(style: BasemapStyle): maplibregl.StyleSpecification {
  const slug = style === 'dark' ? 'dark_all' : 'voyager';
  return {
    version: 8,
    sources: {
      'carto-base': {
        type: 'raster',
        tiles: [
          `https://a.basemaps.cartocdn.com/${slug}/{z}/{x}/{y}.png`,
          `https://b.basemaps.cartocdn.com/${slug}/{z}/{x}/{y}.png`,
          `https://c.basemaps.cartocdn.com/${slug}/{z}/{x}/{y}.png`,
        ],
        tileSize: 256,
        attribution: ATTRIBUTION,
      },
    },
    layers: [
      { id: 'carto-base-tiles', type: 'raster', source: 'carto-base' },
    ],
  };
}

// Match the Leaflet bubble-plot palette and sizing exactly.
const CAT_COLOR_EXPR: maplibregl.ExpressionSpecification = [
  'match',
  ['get', 'category'],
  1, CATEGORY_COLORS[1],
  2, CATEGORY_COLORS[2],
  3, CATEGORY_COLORS[3],
  4, CATEGORY_COLORS[4],
  5, CATEGORY_COLORS[5],
  CATEGORY_COLORS[1],  // default
];

// Radius scales with sqrt(n_pixels), capped at 30 px so even a 500+
// pixel Cat-IV cluster doesn't dominate the basin view as a giant
// blob (which read as "raster offset" instead of "one big event").
// Floor at 6 px so a single-pixel detection is still findable.
// Coefficient 2.0 (was 4.0) gives a gentler curve: a 25-pixel cluster
// → 10 px, a 100-pixel → 20 px, a 500-pixel → ~30 px (capped).
const RADIUS_EXPR: maplibregl.ExpressionSpecification = [
  'min',
  30,
  ['max', 6, ['*', 2, ['sqrt', ['max', 1, ['get', 'n_pixels']]]]],
];

const STROKE_COLOR_EXPR = (selectedId: string | number | null): maplibregl.ExpressionSpecification => [
  'case',
  ['==', ['to-string', ['id']], String(selectedId ?? '')],
  '#ffffff',
  ['>=', ['get', 'category'], 4],
  '#ffe1c4',
  CAT_COLOR_EXPR,
];

export function MapViewGL({
  events,
  overlays,
  toggles,
  onSelect,
  selectedId,
  anomalyDate,
  anomalyBounds,
  bbox,
  onBboxDraw,
  climatologyMissing,
  climatologyMissingMessage,
  anomalyMinDate,
  anomalyMaxDate,
  onAnomalyDateChange,
  anomalyVmin = -5,
  anomalyVmax = 5,
  onMapReady,
  canvasAriaLabel,
  basemap: basemapProp,
  onBasemapChange,
  mapOnly = false,
}: Props) {
  const { t } = useT();
  const [plain] = usePlainMode();
  const [anomOpacity, setAnomOpacity] = useState(0.72);
  const mapRef = useRef<maplibregl.Map | null>(null);

  // Basemap toggle (light / dark) — controlled when the parent supplies a
  // value (cross-cut #4 URL `?basemap=`), uncontrolled otherwise. The
  // uncontrolled fallback persists via localStorage on the same key as the
  // legacy Leaflet build so user prefs survive the swap.
  const [basemapInternal, setBasemapInternal] = useState<BasemapStyle>(() => {
    if (basemapProp) return basemapProp;
    try {
      const v = window.localStorage?.getItem('mheat-basemap');
      return v === 'light' || v === 'dark' ? v : 'dark';
    } catch {
      return 'dark';
    }
  });
  const basemap: BasemapStyle = basemapProp ?? basemapInternal;
  const setBasemap = useCallback((next: BasemapStyle) => {
    if (onBasemapChange) onBasemapChange(next);
    else setBasemapInternal(next);
  }, [onBasemapChange]);
  useEffect(() => {
    if (basemapProp) return; // parent owns persistence in controlled mode
    try { window.localStorage?.setItem('mheat-basemap', basemapInternal); }
    catch { /* noop */ }
  }, [basemapInternal, basemapProp]);
  const mapStyle = useMemo(() => makeBasemapStyle(basemap), [basemap]);

  // Live cursor coordinate readout — updated on mousemove, fixed-position div.
  const [cursorLngLat, setCursorLngLat] = useState<{ lng: number; lat: number } | null>(null);

  // Hover tooltip state — populated when the cursor enters a bubble feature
  // and follows the mouse until it exits. Replaces the bindTooltip we had
  // in the Leaflet build (MapLibre doesn't ship a feature-bound tooltip
  // primitive, so we drive it from React + the map's project() helper).
  type HoverInfo = {
    feat: { id?: string | number; properties: Record<string, unknown> };
    x: number; y: number;
  };
  const [hover, setHover] = useState<HoverInfo | null>(null);

  // Home reset — flies back to the Europe-wide default extent.
  const flyHome = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    map.flyTo({ center: EU_CENTER, zoom: EU_ZOOM, duration: 600 });
  }, []);

  // Bubbles GeoJSON — same transform as Leaflet build.
  const bubbles = useMemo(() => {
    if (!events) return { type: 'FeatureCollection' as const, features: [] };
    return eventsAsBubblesGeoJson(events as unknown as Parameters<typeof eventsAsBubblesGeoJson>[0]);
  }, [events]);

  // Anomaly raster URL — server-side bilinear-upsampled PNG. Uses
  // API_BASE so production builds hit the tunnelled API subdomain
  // directly (Pages cannot proxy cross-origin via _redirects).
  const anomalyUrl = useMemo(() => {
    if (!toggles.anomaly || !anomalyDate || climatologyMissing) return null;
    return `${API_BASE}/anomaly?date=${anomalyDate}`;
  }, [toggles.anomaly, anomalyDate, climatologyMissing]);

  // Anomaly raster bounds in MapLibre [west, south, east, north] order.
  const anomalyCoords = useMemo<[[number, number], [number, number], [number, number], [number, number]]>(() => {
    const b = anomalyBounds ?? DEFAULT_ANOM_BOUNDS_GL;
    const [[west, south], [east, north]] = b;
    return [
      [west, north],   // top-left
      [east, north],   // top-right
      [east, south],   // bottom-right
      [west, south],   // bottom-left
    ];
  }, [anomalyBounds]);

  // UI cross-cut #5 (persona-22 touch-only) — on coarse-pointer devices
  // (phones/tablets where `(hover: none)` matches), bubbles never fire
  // mouseenter, so a tap lands directly on `onSelect` and the user
  // never sees the rich hover tooltip. We adopt the iOS Safari pattern
  // of "first-tap = preview, second-tap = activate":
  //   - tap A on a bubble → show hover tooltip (preview), don't select
  //   - tap B on the SAME bubble → run onSelect (today's behaviour)
  //   - tap on background → dismiss preview
  // Detected once at mount via matchMedia. The ref holds the last-shown
  // preview's feature id so the second-tap comparison is cheap.
  const isTouchOnlyRef = useRef<boolean>(false);
  useEffect(() => {
    try {
      isTouchOnlyRef.current =
        typeof window !== 'undefined'
        && typeof window.matchMedia === 'function'
        && window.matchMedia('(hover: none) and (pointer: coarse)').matches;
    } catch {
      isTouchOnlyRef.current = false;
    }
  }, []);
  const previewIdRef = useRef<string | number | null>(null);

  // Click handler — find the clicked feature on the bubble layer. On
  // touch-only devices, treat the first tap as a preview and only the
  // second tap (on the same feature) as the selection.
  const onMapClick = (e: MapLayerMouseEvent) => {
    const f = e.features?.[0];
    if (!f) {
      // Tapping on background dismisses any preview.
      previewIdRef.current = null;
      setHover(null);
      return;
    }
    if (isTouchOnlyRef.current) {
      const fid = (f.id as string | number | undefined) ?? null;
      if (previewIdRef.current !== fid) {
        // First tap on this bubble — show the hover preview, don't select.
        previewIdRef.current = fid;
        const rect = (mapRef.current?.getCanvas().getBoundingClientRect()) ?? null;
        setHover({
          feat: {
            id: f.id as string | number | undefined,
            properties: (f.properties ?? {}) as Record<string, unknown>,
          },
          x: e.point.x + (rect?.left ?? 0),
          y: e.point.y + (rect?.top ?? 0),
        });
        return;
      }
      // Second tap on same bubble — fall through to selection.
      previewIdRef.current = null;
    }
    onSelect(f as unknown as MhwEventFeature);
  };

  // Long-press handler for touch devices (UI cross-cut #5, persona-22):
  // 500 ms touchstart-with-no-move surfaces the hover tooltip without
  // requiring the two-tap dance — matches the iOS context-menu idiom.
  // `touchend` / `touchcancel` / `touchmove` cancels the timer.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const canvas = map.getCanvas();
    if (!canvas) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let startX = 0;
    let startY = 0;
    const onStart = (ev: TouchEvent) => {
      if (!isTouchOnlyRef.current) return;
      const t = ev.touches?.[0];
      if (!t) return;
      startX = t.clientX;
      startY = t.clientY;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        const rect = canvas.getBoundingClientRect();
        const point = new maplibregl.Point(startX - rect.left, startY - rect.top);
        try {
          const feats = map.queryRenderedFeatures(point, { layers: ['mhw-events-fill'] });
          const f = feats?.[0];
          if (!f) return;
          setHover({
            feat: {
              id: f.id as string | number | undefined,
              properties: (f.properties ?? {}) as Record<string, unknown>,
            },
            x: startX,
            y: startY,
          });
          // Mark this feature as previewed so the next tap selects it
          // (consistent with the two-tap pattern above).
          previewIdRef.current = (f.id as string | number | undefined) ?? null;
        } catch {
          /* feature-query before style ready — ignore */
        }
      }, 500);
    };
    const cancel = () => { if (timer) { clearTimeout(timer); timer = null; } };
    const onMove = (ev: TouchEvent) => {
      const t = ev.touches?.[0];
      if (!t) return cancel();
      // 10 px slop — ignore micro-jitter from a still finger.
      if (Math.abs(t.clientX - startX) > 10 || Math.abs(t.clientY - startY) > 10) cancel();
    };
    canvas.addEventListener('touchstart', onStart, { passive: true });
    canvas.addEventListener('touchmove', onMove, { passive: true });
    canvas.addEventListener('touchend', cancel);
    canvas.addEventListener('touchcancel', cancel);
    return () => {
      cancel();
      canvas.removeEventListener('touchstart', onStart);
      canvas.removeEventListener('touchmove', onMove);
      canvas.removeEventListener('touchend', cancel);
      canvas.removeEventListener('touchcancel', cancel);
    };
  }, []);

  // Cross-cut #6 — surface the maplibre handle to the parent + paint
  // an aria-label onto the .maplibregl-canvas so a keyboard user using
  // a screen-reader hears something more descriptive than "canvas". Re-
  // applied whenever the canvas is recreated (basemap restyle) so the
  // label survives.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (onMapReady) onMapReady(map);
    const apply = () => {
      const canvas = map.getCanvas();
      if (canvas && canvasAriaLabel) {
        canvas.setAttribute('aria-label', canvasAriaLabel);
        canvas.setAttribute('role', 'application');
        // Some MapLibre builds drop tabIndex from the canvas; reassert
        // it so keyboard users can focus the map and pan with arrows.
        if (canvas.tabIndex < 0) canvas.tabIndex = 0;
      }
    };
    apply();
    map.on('styledata', apply);
    return () => {
      map.off('styledata', apply);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onMapReady, canvasAriaLabel]);

  // Cursor change on hover over interactive layers + live coordinate readout
  // + hover tooltip on event bubbles.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const enter = () => { map.getCanvas().style.cursor = 'pointer'; };
    const leave = () => {
      map.getCanvas().style.cursor = '';
      setHover(null);
    };
    const move = (e: maplibregl.MapMouseEvent) => {
      setCursorLngLat({ lng: e.lngLat.lng, lat: e.lngLat.lat });
    };
    const out = () => { setCursorLngLat(null); setHover(null); };
    // Hover tooltip — query rendered features under the cursor on the
    // events layer; if there's one, capture its properties + the canvas
    // pixel position so the React tooltip can render at that spot.
    const eventsHover = (e: maplibregl.MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f) return;
      const rect = map.getCanvas().getBoundingClientRect();
      // e.point is canvas-relative; add canvas offset for window-relative.
      setHover({
        feat: {
          id: f.id as string | number | undefined,
          properties: (f.properties ?? {}) as Record<string, unknown>,
        },
        x: e.point.x + rect.left,
        y: e.point.y + rect.top,
      });
    };
    map.on('mouseenter', 'mhw-events-fill', enter);
    map.on('mouseleave', 'mhw-events-fill', leave);
    map.on('mousemove', 'mhw-events-fill', eventsHover);
    map.on('mousemove', move);
    map.on('mouseout', out);
    return () => {
      map.off('mouseenter', 'mhw-events-fill', enter);
      map.off('mouseleave', 'mhw-events-fill', leave);
      map.off('mousemove', 'mhw-events-fill', eventsHover);
      map.off('mousemove', move);
      map.off('mouseout', out);
    };
  }, []);

  // BBox-draw integration via @mapbox/mapbox-gl-draw. The library is
  // Mapbox-targeted but its IControl shape matches MapLibre. We wire it
  // here as a custom hook-style mount so the parent's `onBboxDraw` callback
  // fires with [lon_min, lat_min, lon_max, lat_max] when the user finishes
  // a rectangle, and seed it from the parent's `bbox` prop on mount + when
  // the prop changes externally.
  const drawRef = useRef<MapboxDraw | null>(null);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const draw = new MapboxDraw({
      displayControlsDefault: false,
      controls: { polygon: false, trash: false },
      defaultMode: 'simple_select',
    });
    map.addControl(draw as unknown as IControl, 'top-right');
    drawRef.current = draw;
    const emit = () => {
      const fc = draw.getAll();
      if (!fc.features.length || !onBboxDraw) return;
      const last = fc.features[fc.features.length - 1];
      const coords = (last.geometry as GeoJSON.Polygon).coordinates?.[0] ?? [];
      if (coords.length < 4) return;
      const lons = coords.map((c) => c[0]);
      const lats = coords.map((c) => c[1]);
      onBboxDraw([
        Math.min(...lons), Math.min(...lats),
        Math.max(...lons), Math.max(...lats),
      ]);
    };
    map.on('draw.create', emit);
    map.on('draw.update', emit);
    return () => {
      map.off('draw.create', emit);
      map.off('draw.update', emit);
      try { map.removeControl(draw as unknown as IControl); } catch { /* already removed */ }
      drawRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Sync external bbox → draw rectangle.
  useEffect(() => {
    const draw = drawRef.current;
    if (!draw) return;
    draw.deleteAll();
    if (bbox) {
      const [w, s, e, n] = bbox;
      draw.add({
        type: 'Feature',
        properties: {},
        geometry: {
          type: 'Polygon',
          coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
        },
      });
    }
  }, [bbox]);
  const startBboxDraw = useCallback(() => {
    drawRef.current?.changeMode('draw_polygon');
  }, []);
  const clearBbox = useCallback(() => {
    drawRef.current?.deleteAll();
    if (onBboxDraw) onBboxDraw(null);
  }, [onBboxDraw]);

  // Per-category counter for the on-map pill (same shape as Leaflet build).
  const counts = useMemo(() => {
    const m: Record<number, number> = {};
    for (const f of (events?.features ?? []) as Array<{ properties?: { category?: number } }>) {
      const c = f.properties?.category ?? 0;
      if (c >= 1 && c <= 5) m[c] = (m[c] ?? 0) + 1;
    }
    return m;
  }, [events]);
  const totalEvents = events?.features?.length ?? 0;

  return (
    <div className="map-container" style={{ position: 'relative', width: '100%', height: '100%' }}>
      <Map
        ref={(r) => { mapRef.current = r?.getMap() ?? null; }}
        initialViewState={{ longitude: EU_CENTER[0], latitude: EU_CENTER[1], zoom: EU_ZOOM }}
        mapStyle={mapStyle}
        interactiveLayerIds={['mhw-events-fill']}
        onClick={onMapClick}
        attributionControl={false}
        style={{ width: '100%', height: '100%' }}
        mapLib={maplibregl as unknown as never}
        // UI cross-cut #5 (persona-25, offline reviewer): keep the WebGL
        // back-buffer alive so the canvas can be rasterised by the
        // browser's print pipeline. Without this the printed page shows
        // a blank rectangle where the basemap should be.
        preserveDrawingBuffer={true}
      >
        <NavigationControl position="top-right" />
        {!mapOnly && <ScaleControl position="bottom-left" maxWidth={100} unit="metric" />}
        {!mapOnly && <AttributionControl position="bottom-right" compact />}

        {/* Anomaly raster — server-side bilinear-upsampled PNG laid over Med bbox. */}
        {anomalyUrl && (
          <Source
            id="anomaly-raster"
            type="image"
            url={anomalyUrl}
            coordinates={anomalyCoords}
          >
            <Layer
              id="anomaly-raster-layer"
              type="raster"
              // Nearest-neighbour sampling so the coastline isn't bilinearly
              // smeared into adjacent land pixels (the source PNG's land
              // cells are transparent NaN; bilinear bleeds the warm
              // cell colour into them and the raster looks like it's
              // painting Slovenia / Anatolia inland). At 4× server-side
              // upsample the pixels are small enough that nearest still
              // looks smooth at basin zoom.
              paint={{ 'raster-opacity': anomOpacity, 'raster-resampling': 'nearest' }}
            />
          </Source>
        )}

        {/* MPA polygons — soft teal fill. */}
        {toggles.mpa && overlays.mpa && (
          <Source id="mpa-src" type="geojson" data={overlays.mpa as unknown as GeoJSON.FeatureCollection}>
            <Layer
              id="mpa-fill"
              type="fill"
              paint={{ 'fill-color': '#2a9d8f', 'fill-opacity': 0.18 }}
            />
            <Layer
              id="mpa-line"
              type="line"
              paint={{ 'line-color': '#2a9d8f', 'line-width': 1.2, 'line-dasharray': [3, 3] }}
            />
          </Source>
        )}

        {/* Seagrass polygons — green tint. */}
        {toggles.seagrass && overlays.seagrass && (
          <Source id="seagrass-src" type="geojson" data={overlays.seagrass as unknown as GeoJSON.FeatureCollection}>
            <Layer
              id="seagrass-fill"
              type="fill"
              paint={{ 'fill-color': '#52b788', 'fill-opacity': 0.32 }}
            />
          </Source>
        )}

        {/* Aquaculture sites — small cyan circles. */}
        {toggles.aquaculture && overlays.aquaculture && (
          <Source id="aqua-src" type="geojson" data={overlays.aquaculture as unknown as GeoJSON.FeatureCollection}>
            <Layer
              id="aqua-circle"
              type="circle"
              paint={{
                'circle-radius': 4,
                'circle-color': '#48cae4',
                'circle-stroke-color': '#0b3d66',
                'circle-stroke-width': 1.2,
                'circle-opacity': 0.95,
              }}
            />
          </Source>
        )}

        {/* MHW events — bubble layer. Color + radius via category, glow on selected. */}
        {events && (
          <Source id="mhw-events-src" type="geojson" data={bubbles as unknown as GeoJSON.FeatureCollection}>
            {/* Outer glow ring for the selected event. */}
            <Layer
              id="mhw-events-glow"
              type="circle"
              filter={['==', ['to-string', ['id']], String(selectedId ?? '__none__')]}
              paint={{
                'circle-radius': ['+', RADIUS_EXPR, 6],
                'circle-color': '#ffffff',
                'circle-opacity': 0.0,
                'circle-stroke-color': '#ffffff',
                'circle-stroke-width': 2,
                'circle-stroke-opacity': 0.85,
              }}
            />
            <Layer
              id="mhw-events-fill"
              type="circle"
              paint={{
                'circle-radius': RADIUS_EXPR,
                'circle-color': CAT_COLOR_EXPR,
                'circle-opacity': 0.78,
                'circle-stroke-color': STROKE_COLOR_EXPR(selectedId ?? null),
                'circle-stroke-width': [
                  'case',
                  ['>=', ['get', 'category'], 4], 3.2,
                  ['>=', ['get', 'category'], 2], 2.0,
                  1.5,
                ],
                'circle-stroke-opacity': 1.0,
              }}
            />
          </Source>
        )}
      </Map>

      {/* Top-left floating control cluster: basemap toggle + home + bbox draw.
          Sits inside the map div so the pointer events don't bubble to the
          MapLibre canvas underneath. Hidden in map-only mode (cross-cut #4)
          so the kiosk view stays uncluttered — zoom + scrubber are kept. */}
      {!mapOnly && (
      <div className="map-controls map-controls-tl">
        <button
          type="button"
          className="map-ctrl-btn"
          onClick={() => setBasemap(basemap === 'dark' ? 'light' : 'dark')}
          aria-label={t('map.basemap')}
          title={t('map.basemap')}
        >
          {basemap === 'dark' ? '☼ Light' : '☾ Dark'}
        </button>
        <button
          type="button"
          className="map-ctrl-btn"
          onClick={flyHome}
          aria-label={t('map.home')}
          title={t('map.home')}
        >
          ⌂
        </button>
        {onBboxDraw && (
          <>
            <button
              type="button"
              className="map-ctrl-btn"
              onClick={startBboxDraw}
              aria-label={t('map.drawBbox')}
              title={t('map.drawBbox')}
            >
              ⬚
            </button>
            {bbox && (
              <button
                type="button"
                className="map-ctrl-btn map-ctrl-btn-danger"
                onClick={clearBbox}
                aria-label={t('map.clearBbox')}
                title={t('map.clearBbox')}
              >
                ✕
              </button>
            )}
          </>
        )}
      </div>
      )}

      {/* Live coordinate readout — bottom-left, only when cursor is on the map. */}
      {cursorLngLat && !mapOnly && (
        <div className="coord-readout" aria-hidden="true">
          {cursorLngLat.lat.toFixed(3)}°, {cursorLngLat.lng.toFixed(3)}°
        </div>
      )}

      {/* Hover tooltip — follows the cursor when over an event bubble.
          Replaces the bindTooltip we had in Leaflet. Positioned in
          page coords from the canvas-relative point + canvas offset. */}
      {hover && (() => {
        const p = hover.feat.properties as {
          event_id?: string; date_start?: string; date_end?: string;
          duration_days?: number; category?: number; category_name?: string;
          intensity_max?: number; intensity_mean?: number; n_pixels?: number;
          impact?: { n_aquaculture_sites?: number; mpa_area_km2?: number;
                     seagrass_area_km2?: number };
        };
        const cat = p.category ?? 0;
        const swatch = CATEGORY_COLORS[cat] ?? '#48cae4';
        const impact = p.impact;
        const hasImpact = impact && (
          (impact.n_aquaculture_sites ?? 0) > 0 ||
          (impact.mpa_area_km2 ?? 0) > 0 ||
          (impact.seagrass_area_km2 ?? 0) > 0
        );
        // Hobday 2018 category interpretation — multiplier of the
        // (90th-percentile − climatology mean) threshold + the typical
        // ecosystem impact at that severity level.
        const HOBDAY_DESCRIPTION: Record<number, { range: string; impact: string }> = {
          1: { range: '1×–2× threshold',  impact: 'mild warming, sub-lethal stress' },
          2: { range: '2×–3× threshold',  impact: 'significant stress, likely behavioural shifts' },
          3: { range: '3×–4× threshold',  impact: 'severe — likely partial mortality in sensitive species' },
          4: { range: '4×–5× threshold',  impact: 'extreme — mass mortality risk for gorgonians, Posidonia, mussels' },
          5: { range: '≥ 5× threshold',   impact: 'unprecedented — basin-scale ecosystem collapse risk' },
        };
        const meaning = HOBDAY_DESCRIPTION[cat];
        return (
          <div
            className="mhw-hover-tip"
            style={{
              position: 'fixed',
              left: hover.x + 14, top: hover.y + 14,
              pointerEvents: 'none',
              zIndex: 1500,
            }}
            role="tooltip"
          >
            <div className="mhw-hover-head" style={{ color: swatch }}>
              {plain
                ? `● ${categoryDisplay(cat, true, t, 'long')}`
                : `● Cat-${cat} ${p.category_name ?? ''}`}
            </div>
            {meaning && (
              <div className="mhw-hover-meaning">
                <span className="mhw-hover-muted">{meaning.range}</span>
                <br />
                <span>{meaning.impact}</span>
              </div>
            )}
            <div className="mhw-hover-id">{p.event_id ?? hover.feat.id}</div>
            <div className="mhw-hover-row">
              {p.date_start} → {p.date_end}{' '}
              <span className="mhw-hover-muted">({p.duration_days} d)</span>
            </div>
            <div className="mhw-hover-row">
              peak{' '}
              <strong>+{p.intensity_max?.toFixed(2)}°C</strong>
              {' '}
              <span className="mhw-hover-muted">
                above seasonal mean
              </span>
            </div>
            <div className="mhw-hover-row mhw-hover-muted">
              mean +{p.intensity_mean?.toFixed(2)}°C · {p.n_pixels} px
            </div>
            {hasImpact && impact && (
              <div className="mhw-hover-impact">
                ⚑ {impact.n_aquaculture_sites ?? 0} aquaculture ·{' '}
                {(impact.mpa_area_km2 ?? 0).toFixed(0)} km² MPA ·{' '}
                {(impact.seagrass_area_km2 ?? 0).toFixed(0)} km² seagrass
              </div>
            )}
          </div>
        );
      })()}

      {/* On-map event counter pill — kept as DOM overlay (same look as Leaflet build).
          Hidden in map-only mode (cross-cut #4) so the kiosk view shows just the map. */}
      {events && !mapOnly && (
        <div className="event-counter" role="status" aria-live="polite">
          <div className="event-counter-top">
            <strong>{totalEvents}</strong>{' '}
            {/* Strip the leading number from the localised template (it's
                already shown as the bold count above) — keeps the noun-phrase
                "clusters on screen" / "clusters à l'écran" / etc. */}
            {(() => {
              const raw = t('map.clustersOnScreen', { n: String(totalEvents) });
              const stripped = raw.replace(/\{?n\}?\s*/, '').replace(/^\d+\s*/, '');
              return stripped || raw;
            })()}
          </div>
          {totalEvents > 0 && (
            <div className="event-counter-breakdown" aria-label={t('map.categoryBreakdown')}>
              {[5, 4, 3, 2, 1].filter((c) => counts[c]).map((c) => (
                <span key={c} className="event-counter-chip">
                  <span
                    className="event-counter-dot"
                    style={{ background: CATEGORY_COLORS[c] }}
                  />
                  {counts[c]} {CATEGORY_SHORT[c]}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Anomaly opacity slider — bottom-right, same as Leaflet build.
          Hidden in map-only mode. */}
      {toggles.anomaly && anomalyUrl && !mapOnly && (
        <div className="anomaly-opacity">
          <label htmlFor="anomaly-opacity-slider" className="anomaly-opacity-label">
            {t('map.opacity')}
          </label>
          <input
            id="anomaly-opacity-slider"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={anomOpacity}
            onChange={(e) => setAnomOpacity(Number(e.target.value))}
            aria-label={t('map.opacity')}
            aria-valuenow={anomOpacity}
          />
          <span className="anomaly-opacity-val">{Math.round(anomOpacity * 100)}%</span>
        </div>
      )}

      {/* On-map time scrubber + keyboard help overlay — unchanged React components.
          Scrubber stays in map-only mode (it's part of the explicit kiosk
          allow-list); KeyboardHelp + anomaly legend hide. */}
      {anomalyMinDate && anomalyMaxDate && onAnomalyDateChange && anomalyDate && (
        <MapTimeSlider
          min={anomalyMinDate}
          max={anomalyMaxDate}
          value={anomalyDate}
          onChange={onAnomalyDateChange}
        />
      )}
      {!mapOnly && <KeyboardHelp />}

      {climatologyMissing && climatologyMissingMessage && !mapOnly && (
        <div className="climatology-missing-banner" role="alert">
          {climatologyMissingMessage}
        </div>
      )}

      {/* Vmin/vmax legend — bottom-left next to scale bar. Hidden in map-only mode. */}
      {!mapOnly && (
        <div className="anomaly-legend" aria-label={t('map.anomalyLegend')}>
          <span>{anomalyVmin}°C</span>
          <span className="anomaly-legend-bar" />
          <span>+{anomalyVmax}°C</span>
        </div>
      )}
    </div>
  );
}

export default MapViewGL;
