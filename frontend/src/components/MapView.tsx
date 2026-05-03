import React, { useMemo, useRef, useEffect, useState } from 'react';
import { MapContainer, TileLayer, GeoJSON, ImageOverlay, useMap } from 'react-leaflet';
import L, { Layer, LatLngBounds } from 'leaflet';
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
  /** When true, suppress the anomaly raster and show an inline note. */
  climatologyMissing?: boolean;
  /** Localised message for the inline climatology-missing note. */
  climatologyMissingMessage?: string;
  /** Bounds for the on-map time scrubber (YYYY-MM-DD); slider hides if absent. */
  anomalyMinDate?: string;
  anomalyMaxDate?: string;
  /** Callback to update the anomaly date as the user scrubs the slider. */
  onAnomalyDateChange?: (date: string) => void;
  /** Anomaly colour-scale extremes (°C). Defaults to ±5 to match the backend RdBu_r mapping. */
  anomalyVmin?: number;
  anomalyVmax?: number;
}

// Default view: all of Europe (Atlantic shelf → Baltic → Med). The MHW
// detection itself remains Mediterranean-scoped (proposal §1.7 + Track A
// roadmap covers Atlantic/Baltic in Call #2), but the broader pan/zoom
// frames the basin in its full European context — useful for reviewers
// who want to see "where this fits" before diving into the data.
const MED_CENTER: [number, number] = [50.0, 12.0];  // Central Europe centroid
const MED_ZOOM = 4;                                  // Atlantic + Med + Baltic visible

// Two basemap presets — dark for the default screenshots (high contrast
// against warm anomaly colours) and light for printed material / inline
// figures. Both are CARTO + OSM, attribution is identical.
type BasemapStyle = 'dark' | 'light';
const BASEMAPS: Record<BasemapStyle, { url: string; subdomains: string }> = {
  dark: {
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    subdomains: 'abcd',
  },
  light: {
    url: 'https://{s}.basemaps.cartocdn.com/voyager/{z}/{x}/{y}{r}.png',
    subdomains: 'abcd',
  },
};

// Discrete tick marks for the anomaly colour bar — read instantly versus the
// previous min/max-only labels and match the matplotlib RdBu_r palette
// rendered server-side at /api/anomaly.
const ANOMALY_TICKS: number[] = [-5, -2.5, 0, 2.5, 5];

// The demo cube covers lon 2..20, lat 36..46 (see build_synthetic_med_cube).
// Leaflet bounds: [[south, west], [north, east]].
const DEFAULT_ANOM_BOUNDS: [[number, number], [number, number]] = [
  [36.0, 2.0],
  [46.0, 20.0],
];

const OVERLAY_STYLES: Record<string, Record<string, unknown>> = {
  mpa: {
    color: '#2a9d8f',
    weight: 1.5,
    fillColor: '#2a9d8f',
    fillOpacity: 0.18,
    dashArray: '3,3',
  },
  seagrass: {
    color: '#52b788',
    weight: 1.2,
    fillColor: '#52b788',
    fillOpacity: 0.32,
  },
};

const AQUA_ICON_STYLE = {
  radius: 6,
  color: '#0b3d66',
  weight: 1.5,
  fillColor: '#48cae4',
  fillOpacity: 0.95,
  className: 'aqua-marker',
};

// Per-category stroke weight — Cat V events read "heavier" at a glance.
const CATEGORY_WEIGHT: Record<number, number> = {
  1: 1.5,
  2: 2,
  3: 2.5,
  4: 3.2,
  5: 4,
};

const CATEGORY_FILL_OPACITY: Record<number, number> = {
  1: 0.30,
  2: 0.38,
  3: 0.48,
  4: 0.58,
  5: 0.68,
};

/* Convert any event GeoJSON FeatureCollection — Polygon, MultiPolygon, or
   already-Point — into a flat collection of Point features at each event's
   centroid. The bubble-plot renderer needs a single point per event so it
   can size a CircleMarker, regardless of the source geometry shape. */
export function eventsAsBubblesGeoJson(
  events: { features: { id?: unknown; properties?: { centroid?: [number, number] }; geometry: { type: string; coordinates: unknown } }[] },
): { type: 'FeatureCollection'; features: unknown[] } {
  const ringCenter = (ring: number[][]): [number, number] => {
    let sx = 0, sy = 0, n = 0;
    for (const [x, y] of ring) { sx += x; sy += y; n += 1; }
    return n ? [sx / n, sy / n] : [0, 0];
  };
  const out = events.features.map((f) => {
    let coords: [number, number] | null = null;
    const c = f.properties?.centroid;
    if (Array.isArray(c) && c.length === 2) {
      coords = [c[0], c[1]];
    } else if (f.geometry.type === 'Point') {
      coords = f.geometry.coordinates as [number, number];
    } else if (f.geometry.type === 'Polygon') {
      const rings = f.geometry.coordinates as number[][][];
      coords = ringCenter(rings[0]);
    } else if (f.geometry.type === 'MultiPolygon') {
      // Centroid of the largest sub-polygon's outer ring.
      const polys = f.geometry.coordinates as number[][][][];
      let best: number[][] | null = null, bestN = 0;
      for (const p of polys) {
        if (p[0] && p[0].length > bestN) { best = p[0]; bestN = p[0].length; }
      }
      coords = best ? ringCenter(best) : [0, 0];
    }
    return {
      type: 'Feature' as const,
      id: f.id,
      geometry: { type: 'Point' as const, coordinates: coords ?? [0, 0] },
      properties: f.properties ?? {},
    };
  });
  return { type: 'FeatureCollection', features: out };
}

// Inline-SVG icons for the BBox control — sharper than HTML entities.
const BBOX_DRAW_SVG =
  '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">' +
  '<rect x="3" y="3" width="14" height="14" fill="none" ' +
  'stroke="currentColor" stroke-width="1.8" stroke-dasharray="3,2"/>' +
  '<circle cx="3" cy="3" r="1.6" fill="currentColor"/>' +
  '<circle cx="17" cy="17" r="1.6" fill="currentColor"/>' +
  '</svg>';

const BBOX_CLEAR_SVG =
  '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">' +
  '<path d="M5 5L15 15M15 5L5 15" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" fill="none"/>' +
  '</svg>';

const HOME_SVG =
  '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">' +
  '<path d="M10 3L3 9v8h4v-5h6v5h4V9z" fill="currentColor"/>' +
  '</svg>';

/** Custom Leaflet control that lets the user draw a rectangular bbox. */
function BBoxDrawControl({
  bbox,
  onDraw,
}: {
  bbox?: [number, number, number, number] | null;
  onDraw?: (bbox: [number, number, number, number] | null) => void;
}) {
  const map = useMap();
  const rectRef = useRef<L.Rectangle | null>(null);
  const drawingRef = useRef<{
    isDrawing: boolean;
    startLatLng: L.LatLng | null;
    tempRect: L.Rectangle | null;
  }>({ isDrawing: false, startLatLng: null, tempRect: null });

  // Sync external bbox prop -> visible rectangle
  useEffect(() => {
    if (rectRef.current) {
      rectRef.current.remove();
      rectRef.current = null;
    }
    if (bbox) {
      const [w, s, e, n] = bbox;
      const b = L.latLngBounds([s, w], [n, e]);
      rectRef.current = L.rectangle(b, {
        color: '#e63946',
        weight: 2,
        fill: false,
        dashArray: '6,4',
      }).addTo(map);
    }
  }, [bbox, map]);

  useEffect(() => {
    // Add a custom control button
    const Control = L.Control.extend({
      onAdd: () => {
        const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control bbox-draw-control');
        const drawBtn = L.DomUtil.create('a', 'bbox-draw-btn', container) as HTMLAnchorElement;
        drawBtn.href = '#';
        drawBtn.title = 'Draw bbox filter';
        drawBtn.setAttribute('role', 'button');
        drawBtn.setAttribute('aria-label', 'Draw a rectangular region to filter events');
        drawBtn.innerHTML = BBOX_DRAW_SVG;

        const clearBtn = L.DomUtil.create('a', 'bbox-clear-btn', container) as HTMLAnchorElement;
        clearBtn.href = '#';
        clearBtn.title = 'Clear bbox filter';
        clearBtn.setAttribute('role', 'button');
        clearBtn.setAttribute('aria-label', 'Clear the drawn bbox filter');
        clearBtn.innerHTML = BBOX_CLEAR_SVG;

        L.DomEvent.disableClickPropagation(container);

        L.DomEvent.on(drawBtn, 'click', (ev) => {
          L.DomEvent.preventDefault(ev);
          // Begin draw mode
          const c = map.getContainer();
          c.style.cursor = 'crosshair';
          map.dragging.disable();
          drawingRef.current.isDrawing = true;
          drawBtn.classList.add('is-drawing');
        });

        L.DomEvent.on(clearBtn, 'click', (ev) => {
          L.DomEvent.preventDefault(ev);
          if (rectRef.current) {
            rectRef.current.remove();
            rectRef.current = null;
          }
          onDraw?.(null);
        });

        return container;
      },
    });
    const ctrl = new Control({ position: 'topright' });
    ctrl.addTo(map);

    const onMouseDown = (ev: L.LeafletMouseEvent) => {
      if (!drawingRef.current.isDrawing) return;
      drawingRef.current.startLatLng = ev.latlng;
      drawingRef.current.tempRect = L.rectangle(
        L.latLngBounds(ev.latlng, ev.latlng),
        { color: '#e63946', weight: 2, fill: true, fillOpacity: 0.1, dashArray: '4,3' }
      ).addTo(map);
    };

    const onMouseMove = (ev: L.LeafletMouseEvent) => {
      if (!drawingRef.current.isDrawing || !drawingRef.current.startLatLng) return;
      const bounds = L.latLngBounds(drawingRef.current.startLatLng, ev.latlng);
      drawingRef.current.tempRect?.setBounds(bounds);
    };

    const onMouseUp = (ev: L.LeafletMouseEvent) => {
      if (!drawingRef.current.isDrawing || !drawingRef.current.startLatLng) return;
      const bounds: LatLngBounds = L.latLngBounds(drawingRef.current.startLatLng, ev.latlng);
      drawingRef.current.tempRect?.remove();
      drawingRef.current.tempRect = null;
      drawingRef.current.startLatLng = null;
      drawingRef.current.isDrawing = false;
      map.getContainer().style.cursor = '';
      map.dragging.enable();
      // Remove "drawing" visual state from the toolbar button.
      map.getContainer()
        .querySelector('.bbox-draw-btn')
        ?.classList.remove('is-drawing');
      const w = bounds.getWest();
      const s = bounds.getSouth();
      const e = bounds.getEast();
      const n = bounds.getNorth();
      if (Math.abs(e - w) < 0.01 || Math.abs(n - s) < 0.01) {
        // too small, treat as cancel
        return;
      }
      onDraw?.([
        Number(w.toFixed(3)),
        Number(s.toFixed(3)),
        Number(e.toFixed(3)),
        Number(n.toFixed(3)),
      ]);
    };

    const cancelDraw = () => {
      if (!drawingRef.current.isDrawing) return;
      drawingRef.current.tempRect?.remove();
      drawingRef.current.tempRect = null;
      drawingRef.current.startLatLng = null;
      drawingRef.current.isDrawing = false;
      map.getContainer().style.cursor = '';
      map.dragging.enable();
      map.getContainer()
        .querySelector('.bbox-draw-btn')
        ?.classList.remove('is-drawing');
    };

    const onKey = (ev: KeyboardEvent) => {
      const t = ev.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (ev.key === 'Escape') cancelDraw();
      // "b" starts a bbox draw, mirroring the toolbar button.
      if ((ev.key === 'b' || ev.key === 'B') && !drawingRef.current.isDrawing) {
        map.getContainer().style.cursor = 'crosshair';
        map.dragging.disable();
        drawingRef.current.isDrawing = true;
        map.getContainer()
          .querySelector('.bbox-draw-btn')
          ?.classList.add('is-drawing');
      }
    };

    map.on('mousedown', onMouseDown);
    map.on('mousemove', onMouseMove);
    map.on('mouseup', onMouseUp);
    window.addEventListener('keydown', onKey);

    return () => {
      ctrl.remove();
      map.off('mousedown', onMouseDown);
      map.off('mousemove', onMouseMove);
      map.off('mouseup', onMouseUp);
      window.removeEventListener('keydown', onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  return null;
}

/** Live coordinate readout, bottom-left (above the scale bar). */
function CoordinateReadout() {
  const map = useMap();
  const [coords, setCoords] = useState<{ lat: number; lon: number } | null>(null);
  useEffect(() => {
    const onMove = (ev: L.LeafletMouseEvent) => {
      setCoords({ lat: ev.latlng.lat, lon: ev.latlng.lng });
    };
    const onLeave = () => setCoords(null);
    map.on('mousemove', onMove);
    map.on('mouseout', onLeave);
    return () => {
      map.off('mousemove', onMove);
      map.off('mouseout', onLeave);
    };
  }, [map]);
  if (!coords) return null;
  const latLbl = `${Math.abs(coords.lat).toFixed(2)}° ${coords.lat >= 0 ? 'N' : 'S'}`;
  const lonLbl = `${Math.abs(coords.lon).toFixed(2)}° ${coords.lon >= 0 ? 'E' : 'W'}`;
  return (
    <div className="coord-readout" aria-hidden="true">
      {latLbl} &nbsp; {lonLbl}
    </div>
  );
}

/** Metric scale bar, bottom-left. */
function ScaleBar() {
  const map = useMap();
  useEffect(() => {
    const ctrl = L.control.scale({
      imperial: false,
      metric: true,
      position: 'bottomleft',
      maxWidth: 160,
    });
    ctrl.addTo(map);
    return () => {
      ctrl.remove();
    };
  }, [map]);
  return null;
}

/**
 * Auto-fly to the selected event's bounds when ``selectedId`` changes.
 * Lives inside <MapContainer> so it can grab a map handle via useMap().
 *
 * Side benefit: clicking an event in the EventPanel sidebar now centres
 * the map on it, instead of leaving the user hunting for a polygon at
 * basin scale.
 */
function FlyToSelected({
  events,
  selectedId,
}: {
  events: MhwEventCollection | null;
  selectedId?: string | number | null;
}) {
  const map = useMap();
  // Track the last id we flew to so toggling a layer (which re-renders the
  // map) doesn't repeat the flyTo and yank the camera mid-pan.
  const lastFlewRef = useRef<string | null>(null);
  useEffect(() => {
    if (!events || selectedId == null) {
      lastFlewRef.current = null;
      return;
    }
    const key = String(selectedId);
    if (lastFlewRef.current === key) return;
    const feat = events.features.find((f) => String(f.id) === key);
    if (!feat) return;
    try {
      const layer = L.geoJSON(feat as unknown as GeoJSON.GeoJsonObject);
      const bounds = layer.getBounds();
      if (bounds.isValid()) {
        map.flyToBounds(bounds, {
          padding: [60, 60],
          maxZoom: 8,
          duration: 0.7,
        });
        lastFlewRef.current = key;
      }
    } catch {
      // Geometry parse failure: fall back to no-op so we never crash the map.
    }
  }, [events, selectedId, map]);
  return null;
}

/** Reset-view button + "h" keyboard shortcut. */
function HomeControl() {
  const map = useMap();
  useEffect(() => {
    const Control = L.Control.extend({
      onAdd: () => {
        const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control home-control');
        const btn = L.DomUtil.create('a', '', container) as HTMLAnchorElement;
        btn.href = '#';
        btn.title = 'Reset view (shortcut: h)';
        btn.setAttribute('role', 'button');
        btn.setAttribute('aria-label', 'Reset map to Mediterranean default view');
        btn.innerHTML = HOME_SVG;
        L.DomEvent.disableClickPropagation(container);
        L.DomEvent.on(btn, 'click', (ev) => {
          L.DomEvent.preventDefault(ev);
          map.flyTo(MED_CENTER, MED_ZOOM, { duration: 0.6 });
        });
        return container;
      },
    });
    const ctrl = new Control({ position: 'topright' });
    ctrl.addTo(map);

    const onKey = (ev: KeyboardEvent) => {
      // Ignore if focus is in an input/textarea/select.
      const t = ev.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (ev.key === 'h' || ev.key === 'H') {
        map.flyTo(MED_CENTER, MED_ZOOM, { duration: 0.6 });
      }
    };
    window.addEventListener('keydown', onKey);

    return () => {
      ctrl.remove();
      window.removeEventListener('keydown', onKey);
    };
  }, [map]);
  return null;
}

export function MapView({
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
}: Props) {
  const { t } = useT();
  // Default 0.72 — strong enough that the heat signal reads instantly,
  // soft enough that the basemap coastline and event polygons stay visible.
  const [anomOpacity, setAnomOpacity] = useState(0.72);
  // Persist basemap choice across reloads — reviewers like to flip dark/light
  // for screenshots and shouldn't lose it on a session refresh.
  const [basemap, setBasemap] = useState<BasemapStyle>(() => {
    try {
      const v = window.localStorage?.getItem('mheat-basemap');
      return v === 'light' || v === 'dark' ? v : 'dark';
    } catch {
      return 'dark';
    }
  });
  useEffect(() => {
    try {
      window.localStorage?.setItem('mheat-basemap', basemap);
    } catch {
      /* noop */
    }
  }, [basemap]);

  // The slider is only useful when we have a real date window; demo mode
  // and live mode both supply min/max via App, but be defensive.
  const sliderActive =
    !!anomalyDate &&
    !!anomalyMinDate &&
    !!anomalyMaxDate &&
    !!onAnomalyDateChange &&
    anomalyMinDate < anomalyMaxDate;

  const eventKey = useMemo(
    () =>
      events
        ? `ev-${events.features.length}-${events.features[0]?.id ?? '0'}-${selectedId ?? 'none'}`
        : 'ev-empty',
    [events, selectedId]
  );

  const catBreakdown = useMemo(() => {
    if (!events) return null;
    const counts: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
    for (const f of events.features) {
      const c = (f.properties as { category?: number } | undefined)?.category ?? 1;
      if (counts[c] !== undefined) counts[c] += 1;
    }
    return counts;
  }, [events]);

  const styleEvent = (feat?: Feature) => {
    const cat = (feat?.properties as { category?: number } | undefined)?.category ?? 1;
    const isSelected =
      selectedId != null &&
      feat?.id != null &&
      String(feat.id) === String(selectedId);
    // Cat-5 (#370617) is intentionally near-black in the design palette
    // for severity ramping, but on a dark basemap that disappears. Use a
    // brighter contrasting stroke for high-cat events so they remain
    // legible against the CARTO dark tiles, and lift fill opacity so
    // the heat-magnitude reads at a glance.
    const stroke = isSelected
      ? '#ffffff'
      : cat >= 4 ? '#ffe1c4' : CATEGORY_COLORS[cat];
    const baseFill = CATEGORY_FILL_OPACITY[cat] ?? 0.45;
    const lift = cat >= 4 ? 0.18 : 0;
    return {
      color: stroke,
      weight: (CATEGORY_WEIGHT[cat] ?? 2) + (isSelected ? 2 : 0),
      fillColor: CATEGORY_COLORS[cat],
      fillOpacity: isSelected
        ? Math.min(0.92, baseFill + 0.25)
        : Math.min(0.92, baseFill + lift),
      opacity: 1,
      // Marching-ants stroke on selected events draws the eye without
      // hiding the underlying SST anomaly.
      dashArray: isSelected ? '6,4' : undefined,
      // Attach a className so the CSS pulse / glow animation can target it.
      className: isSelected ? 'mhw-event-selected' : 'mhw-event',
    };
  };

  const onEachEvent = (feat: Feature, layer: Layer) => {
    const p = feat.properties as MhwEventFeature['properties'] | undefined;
    if (!p) return;

    const hasImpact =
      !!p.impact &&
      (p.impact.n_aquaculture_sites > 0 ||
        p.impact.mpa_area_km2 > 0 ||
        p.impact.seagrass_area_km2 > 0);
    const impactLine = p.impact
      ? `<br><span class="mhw-tip-impact">⚑</span> ${p.impact.n_aquaculture_sites} aquaculture · ${p.impact.mpa_area_km2.toFixed(0)} km² MPA · ${p.impact.seagrass_area_km2.toFixed(0)} km² seagrass`
      : '';
    const impactBadge = hasImpact
      ? '<span class="mhw-tip-badge" title="overlaps sectoral overlay">impact</span>'
      : '';
    layer.bindTooltip(
      `<div class="mhw-tip">
         <div class="mhw-tip-head" style="color:${CATEGORY_COLORS[p.category] ?? '#fff'}">● ${p.category_name}${impactBadge}</div>
         <div>${p.date_start} → ${p.date_end} <span class="mhw-tip-muted">(${p.duration_days}d)</span></div>
         <div class="mhw-tip-muted">${p.n_pixels} pixel${p.n_pixels === 1 ? '' : 's'}${impactLine}</div>
       </div>`,
      { sticky: true, direction: 'top', className: 'mhw-tooltip' }
    );

    const path = layer as unknown as L.Path;
    const baseWeight = CATEGORY_WEIGHT[p.category] ?? 2;
    const baseFill = CATEGORY_FILL_OPACITY[p.category] ?? 0.45;

    layer.on({
      click: () => onSelect(feat as unknown as MhwEventFeature),
      mouseover: () => {
        path.setStyle?.({ weight: baseWeight + 1.5, fillOpacity: Math.min(0.85, baseFill + 0.18) });
        (layer as unknown as { bringToFront?: () => void }).bringToFront?.();
      },
      mouseout: () => {
        path.setStyle?.({ weight: baseWeight, fillOpacity: baseFill });
      },
    });
  };

  const anomalyUrl = anomalyDate && !climatologyMissing
    ? `${API_BASE}/anomaly?date=${anomalyDate}`
    : null;

  return (
    <div
      className="map-container"
      role="region"
      aria-label="Interactive map of the Mediterranean with marine heatwave events and sectoral overlays"
    >
      <MapContainer center={MED_CENTER} zoom={MED_ZOOM} scrollWheelZoom className="map">
        <TileLayer
          // Keyed on basemap so the tile pyramid is replaced cleanly when
          // the user toggles light/dark — without a key, react-leaflet
          // patches in place and leaves cached dark tiles ghosting.
          key={`basemap-${basemap}`}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &middot; &copy; <a href="https://carto.com/attributions">CARTO</a> &middot; SST <a href="https://marine.copernicus.eu">Copernicus Marine</a> &middot; overlays <a href="https://emodnet.ec.europa.eu">EMODnet</a> / <a href="https://www.eea.europa.eu/data-and-maps/data/natura-2000-spatial-data">EEA Natura 2000</a>'
          url={BASEMAPS[basemap].url}
          subdomains={BASEMAPS[basemap].subdomains}
          maxZoom={19}
        />

        <BBoxDrawControl bbox={bbox ?? null} onDraw={onBboxDraw} />
        <HomeControl />
        <ScaleBar />
        <CoordinateReadout />
        <FlyToSelected events={events} selectedId={selectedId} />

        {toggles.anomaly && anomalyUrl && (
          <ImageOverlay
            key={`anom-${anomalyDate}-${anomOpacity.toFixed(2)}`}
            url={anomalyUrl}
            bounds={anomalyBounds ?? DEFAULT_ANOM_BOUNDS}
            opacity={anomOpacity}
            // CSS class lets us bilinear-smooth the source raster so it
            // doesn't look pixelated at the basin scale (the source SST
            // is on a 0.0625° grid — natively blocky without smoothing).
            className="anomaly-overlay-smooth"
          />
        )}

        {toggles.mpa && overlays.mpa && (
          <GeoJSON
            key={`mpa-${overlays.mpa.features.length}`}
            data={overlays.mpa as unknown as GeoJSON.GeoJsonObject}
            style={() => OVERLAY_STYLES.mpa}
            onEachFeature={(f, l) => {
              const name = (f.properties as { name?: string })?.name ?? 'MPA';
              l.bindTooltip(String(name));
            }}
          />
        )}

        {toggles.seagrass && overlays.seagrass && (
          <GeoJSON
            key={`sg-${overlays.seagrass.features.length}`}
            data={overlays.seagrass as unknown as GeoJSON.GeoJsonObject}
            style={() => OVERLAY_STYLES.seagrass}
            onEachFeature={(f, l) => {
              const h = (f.properties as { habitat?: string })?.habitat ?? 'Seagrass';
              l.bindTooltip(String(h));
            }}
          />
        )}

        {toggles.aquaculture && overlays.aquaculture && (
          <GeoJSON
            key={`aq-${overlays.aquaculture.features.length}`}
            data={overlays.aquaculture as unknown as GeoJSON.GeoJsonObject}
            pointToLayer={(_feat, latlng) => L.circleMarker(latlng, AQUA_ICON_STYLE)}
            onEachFeature={(f, l) => {
              const name = (f.properties as { name?: string })?.name ?? 'Aquaculture site';
              const species = (f.properties as { species?: string })?.species ?? '';
              l.bindTooltip(`${name}${species ? ` (${species})` : ''}`);
            }}
          />
        )}

        {events && (
          <GeoJSON
            key={eventKey}
            data={eventsAsBubblesGeoJson(events) as unknown as GeoJSON.GeoJsonObject}
            onEachFeature={onEachEvent}
            // Bubble-plot rendering — every event becomes a single circle
            // marker centered on its centroid. Radius scales with sqrt
            // (n_pixels) so a 264-pixel cluster reads as a much bigger
            // bubble than a 1-pixel detection without overwhelming the
            // map at high event counts. Colour follows the per-category
            // palette; high-cat events get the bright `#ffe1c4` stroke
            // so they remain legible on the dark basemap.
            pointToLayer={(feat, latlng) => {
              const p = (feat?.properties ?? {}) as {
                category?: number; intensity_max?: number; n_pixels?: number;
              };
              const cat = p.category ?? 1;
              const npix = Math.max(1, p.n_pixels ?? 1);
              const intensity = Math.max(0.5, Math.min(6, p.intensity_max ?? 1));
              // Radius blends pixel-count (sqrt → quadratic-fair) with
              // intensity, so a small but very intense event still
              // stands out and a big mild one still fills the area.
              const sizeFromPx = Math.sqrt(npix) * 4;            // 4..~65 px
              const sizeFromInt = 4 + (intensity / 6) * 14;       // 5..18 px
              const radius = Math.min(70, Math.max(8, Math.max(sizeFromPx, sizeFromInt)));
              const isSelected =
                selectedId != null && feat?.id != null &&
                String(feat.id) === String(selectedId);
              const stroke = isSelected
                ? '#ffffff'
                : cat >= 4 ? '#ffe1c4' : CATEGORY_COLORS[cat];
              return L.circleMarker(latlng, {
                radius,
                color: stroke,
                weight: (CATEGORY_WEIGHT[cat] ?? 2) + (isSelected ? 2 : 0),
                fillColor: CATEGORY_COLORS[cat],
                fillOpacity: Math.min(0.85, (CATEGORY_FILL_OPACITY[cat] ?? 0.45) + 0.18),
                opacity: 1,
                className: isSelected ? 'mhw-event-selected' : 'mhw-event',
              });
            }}
          />
        )}
      </MapContainer>

      {events && (
        <div className="event-counter" role="status" aria-live="polite">
          <div className="event-counter-top">
            {/* Render the localised "N clusters on screen" template with the
                numeric token swapped for a <strong>. Splitting on /(\d+)/
                keeps the EN/FR/IT word order intact since the count is the
                only digit run in the template. */}
            {(events.features.length === 1
              ? t('map.clustersSingular', { n: events.features.length })
              : t('map.clustersPlural', { n: events.features.length })
            )
              .split(/(\d+)/)
              .map((part, i) =>
                /^\d+$/.test(part) ? <strong key={i}>{part}</strong> : part,
              )}
          </div>
          {catBreakdown && events.features.length > 0 && (
            <div className="event-counter-breakdown" aria-label={t('map.categoryBreakdown')}>
              {[1, 2, 3, 4, 5].map((c) => {
                const n = catBreakdown[c];
                if (!n) return null;
                return (
                  <span
                    key={c}
                    className="event-counter-chip"
                    title={`${n} Category ${CATEGORY_SHORT[c]} cluster${n === 1 ? '' : 's'}`}
                  >
                    <span
                      className="event-counter-dot"
                      style={{ background: CATEGORY_COLORS[c] }}
                      aria-hidden="true"
                    />
                    {CATEGORY_SHORT[c]} {n}
                  </span>
                );
              })}
            </div>
          )}
        </div>
      )}

      {toggles.anomaly && climatologyMissing && (
        <div
          className="anomaly-missing"
          role="alert"
          data-testid="map-climatology-missing"
        >
          {climatologyMissingMessage ??
            'SST anomaly unavailable: climatology artifact missing on the server.'}
        </div>
      )}

      {toggles.anomaly && anomalyUrl && (
        <div className="anomaly-panel" role="group" aria-label="SST anomaly overlay controls">
          <div className="anomaly-legend" aria-label={`${t('anomalyLegend.label')}, range ${anomalyVmin} to ${anomalyVmax}`}>
            <div className="anomaly-legend-bar" aria-hidden="true" />
            <div className="anomaly-legend-ticks" aria-hidden="true">
              {ANOMALY_TICKS.map((v) => {
                const pct = ((v - anomalyVmin) / (anomalyVmax - anomalyVmin)) * 100;
                return (
                  <span
                    key={v}
                    className="anomaly-legend-tick"
                    style={{ left: `${Math.max(0, Math.min(100, pct))}%` }}
                  >
                    <span className="anomaly-legend-tick-mark" />
                    <span className="anomaly-legend-tick-label">
                      {v > 0 ? `+${v}` : v}
                    </span>
                  </span>
                );
              })}
            </div>
            <div className="anomaly-legend-units">{t('anomalyLegend.label')}</div>
          </div>
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
              aria-valuemin={0}
              aria-valuemax={1}
              aria-valuenow={anomOpacity}
            />
            <span className="anomaly-opacity-val">{Math.round(anomOpacity * 100)}%</span>
          </div>
        </div>
      )}

      {/* Open-standards "interop" badge — clickable proof of the proposal's
          OGC API + STAC + ARCO claims, anchored top-left where reviewers look
          first. The pill colour rotates per-standard so they are individually
          recognisable in screenshots. */}
      <div className="interop-badge" role="group" aria-label={t('interop.aria')}>
        <a
          className="interop-pill interop-pill-ogc"
          href="/api/ogcapi"
          target="_blank"
          rel="noreferrer"
          title={t('interop.ogcTitle')}
        >
          {t('interop.ogc')}
        </a>
        <a
          className="interop-pill interop-pill-stac"
          href="/api/stac/collections"
          target="_blank"
          rel="noreferrer"
          title={t('interop.stacTitle')}
        >
          {t('interop.stac')}
        </a>
        <a
          className="interop-pill interop-pill-arco"
          href="/api/docs#tag/anomaly"
          target="_blank"
          rel="noreferrer"
          title={t('interop.arcoTitle')}
        >
          {t('interop.arco')}
        </a>
      </div>

      {/* Basemap switcher — small overlay control below the interop badge.
          Persisted to localStorage so the chosen style survives reloads. */}
      <div className="basemap-switcher" role="group" aria-label={t('map.basemap')}>
        <button
          type="button"
          className={`basemap-btn${basemap === 'dark' ? ' is-active' : ''}`}
          onClick={() => setBasemap('dark')}
          aria-pressed={basemap === 'dark'}
          title={t('map.basemapDark')}
        >
          {t('map.basemapDark')}
        </button>
        <button
          type="button"
          className={`basemap-btn${basemap === 'light' ? ' is-active' : ''}`}
          onClick={() => setBasemap('light')}
          aria-pressed={basemap === 'light'}
          title={t('map.basemapLight')}
        >
          {t('map.basemapLight')}
        </button>
      </div>

      {sliderActive && (
        <MapTimeSlider
          value={anomalyDate as string}
          min={anomalyMinDate as string}
          max={anomalyMaxDate as string}
          onChange={onAnomalyDateChange as (d: string) => void}
        />
      )}

      <KeyboardHelp />
    </div>
  );
}
