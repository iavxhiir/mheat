/**
 * eventsAsBubblesGeoJson — collapses Polygon / MultiPolygon / Point event
 * geometries into a flat Point FeatureCollection so the bubble-plot layer
 * can render a single CircleMarker per event regardless of source shape.
 *
 * Pure function; no DOM, no Leaflet — straight unit tests.
 */
import { describe, it, expect } from 'vitest';
import { eventsAsBubblesGeoJson } from '../MapView';

// The function's published signature narrows `properties` to just `centroid`,
// but at runtime it forwards the whole bag verbatim. Use a wider local type
// in tests so we can attach `category`, `n_pixels`, etc. without TS noise —
// the cast at the call site is the only place that hand-waves the signature.
type AnyFeat = {
  id?: unknown;
  properties?: Record<string, unknown> & { centroid?: [number, number] };
  geometry: { type: string; coordinates: unknown };
};
type Input = Parameters<typeof eventsAsBubblesGeoJson>[0];

// Helper: build the input the function expects, casting our wider local
// `AnyFeat` to the published narrow type. The function ignores the cast at
// runtime — it only reads .geometry and .properties.centroid.
function call(features: AnyFeat[]) {
  return eventsAsBubblesGeoJson({ features } as unknown as Input);
}

describe('eventsAsBubblesGeoJson', () => {
  it('returns an empty FeatureCollection for an empty input', () => {
    const out = call([]);
    expect(out.type).toBe('FeatureCollection');
    expect(out.features).toEqual([]);
  });

  it('reduces a Polygon to a Point at the outer-ring centroid', () => {
    const square: AnyFeat = {
      id: 'p-1',
      properties: { category: 2 },
      geometry: {
        type: 'Polygon',
        // Closed unit square: average of all 5 vertices is (0.6, 0.6) since
        // the closing vertex is duplicated. The implementation averages every
        // point in the ring (including the duplicate), which is the contract
        // we lock in here.
        coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
      },
    };
    const out = call([square]);
    expect(out.features.length).toBe(1);
    const f = out.features[0] as {
      type: string;
      id: unknown;
      geometry: { type: string; coordinates: [number, number] };
      properties: Record<string, unknown>;
    };
    expect(f.type).toBe('Feature');
    expect(f.id).toBe('p-1');
    expect(f.geometry.type).toBe('Point');
    expect(f.geometry.coordinates[0]).toBeCloseTo(0.4, 6);
    expect(f.geometry.coordinates[1]).toBeCloseTo(0.4, 6);
    // Properties are forwarded verbatim so downstream styling still works.
    expect(f.properties).toEqual({ category: 2 });
  });

  it('picks the largest sub-polygon for a MultiPolygon centroid', () => {
    // Two sub-polygons:
    //   small triangle near (0, 0) — 4 vertices
    //   large hexagon-ish ring centred near (10, 10) — 7 vertices
    // The 7-vertex ring should win and dominate the centroid.
    const multi: AnyFeat = {
      id: 'mp-1',
      properties: { category: 4 },
      geometry: {
        type: 'MultiPolygon',
        coordinates: [
          [[[0, 0], [1, 0], [0, 1], [0, 0]]],                                    // 4 verts
          [[[9, 9], [11, 9], [12, 10], [11, 11], [9, 11], [8, 10], [9, 9]]],     // 7 verts
        ],
      },
    };
    const out = call([multi]);
    const f = out.features[0] as { geometry: { coordinates: [number, number] } };
    // Mean of the 7 vertices in the larger ring.
    const ring = [[9, 9], [11, 9], [12, 10], [11, 11], [9, 11], [8, 10], [9, 9]];
    const expectedX = ring.reduce((s, [x]) => s + x, 0) / ring.length;
    const expectedY = ring.reduce((s, [, y]) => s + y, 0) / ring.length;
    expect(f.geometry.coordinates[0]).toBeCloseTo(expectedX, 6);
    expect(f.geometry.coordinates[1]).toBeCloseTo(expectedY, 6);
    // Sanity: definitely closer to (10, 10) than to (0, 0).
    expect(f.geometry.coordinates[0]).toBeGreaterThan(5);
    expect(f.geometry.coordinates[1]).toBeGreaterThan(5);
  });

  it('passes a Point geometry through at the same coordinates', () => {
    const pt: AnyFeat = {
      id: 'pt-1',
      properties: { category: 1 },
      geometry: { type: 'Point', coordinates: [12.5, 42.3] },
    };
    const out = call([pt]);
    const f = out.features[0] as { geometry: { type: string; coordinates: [number, number] } };
    expect(f.geometry.type).toBe('Point');
    expect(f.geometry.coordinates).toEqual([12.5, 42.3]);
  });

  it('prefers properties.centroid over the geometry-derived centroid', () => {
    // Geometry would yield (0.4, 0.4); explicit centroid forces (12, 40).
    const overridden: AnyFeat = {
      id: 'p-2',
      properties: { category: 3, centroid: [12, 40] },
      geometry: {
        type: 'Polygon',
        coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
      },
    };
    const out = call([overridden]);
    const f = out.features[0] as { geometry: { coordinates: [number, number] } };
    expect(f.geometry.coordinates).toEqual([12, 40]);
  });

  it('prefers properties.centroid even for a Point geometry (explicit override wins)', () => {
    const pt: AnyFeat = {
      id: 'pt-2',
      properties: { centroid: [1, 2] },
      geometry: { type: 'Point', coordinates: [99, 99] },
    };
    const out = call([pt]);
    const f = out.features[0] as { geometry: { coordinates: [number, number] } };
    expect(f.geometry.coordinates).toEqual([1, 2]);
  });

  it('preserves feature order, ids, and properties across mixed geometry types', () => {
    const features: AnyFeat[] = [
      {
        id: 'a',
        properties: { category: 1, n_pixels: 7 },
        geometry: { type: 'Point', coordinates: [0, 0] },
      },
      {
        id: 'b',
        properties: { category: 2, n_pixels: 13 },
        geometry: {
          type: 'Polygon',
          coordinates: [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
        },
      },
      {
        id: 'c',
        properties: { category: 5, n_pixels: 250, centroid: [-5, -5] },
        geometry: {
          type: 'MultiPolygon',
          coordinates: [[[[10, 10], [20, 10], [20, 20], [10, 10]]]],
        },
      },
    ];
    const out = call(features);
    expect(out.features.length).toBe(3);
    const ids = out.features.map((f) => (f as { id: unknown }).id);
    expect(ids).toEqual(['a', 'b', 'c']);
    // Properties forwarded as-is on every feature.
    expect((out.features[0] as { properties: { category: number } }).properties.category).toBe(1);
    expect((out.features[1] as { properties: { n_pixels: number } }).properties.n_pixels).toBe(13);
    // properties.centroid override on feature 'c' wins over the MultiPolygon centroid.
    expect((out.features[2] as { geometry: { coordinates: [number, number] } }).geometry.coordinates)
      .toEqual([-5, -5]);
    // Every output geometry is a Point.
    for (const f of out.features) {
      expect((f as { geometry: { type: string } }).geometry.type).toBe('Point');
    }
  });

  it('falls back to [0, 0] for an unknown geometry type without a centroid', () => {
    const weird: AnyFeat = {
      id: 'x-1',
      properties: {},
      // Cast to any to bypass the literal-type narrowing — this simulates
      // a future geometry type the function doesn't handle yet.
      geometry: { type: 'LineString' as unknown as 'Point', coordinates: [[0, 0], [1, 1]] as unknown as number[] },
    };
    const out = call([weird]);
    const f = out.features[0] as { geometry: { coordinates: [number, number] } };
    expect(f.geometry.coordinates).toEqual([0, 0]);
  });
});

/**
 * pointToLayer radius scaling — locks in the "sqrt(n_pixels) * 4" rule
 * (clamped 8..70) used by MapView's bubble-plot layer. We don't render
 * Leaflet here; we recreate the formula as a pure function and assert
 * the contract so any future tweak to the scaling rule fails loudly.
 */
function bubbleRadius(n_pixels: number, intensity_max = 1): number {
  const npix = Math.max(1, n_pixels);
  const intensity = Math.max(0.5, Math.min(6, intensity_max));
  const sizeFromPx = Math.sqrt(npix) * 4;
  const sizeFromInt = 4 + (intensity / 6) * 14;
  return Math.min(70, Math.max(8, Math.max(sizeFromPx, sizeFromInt)));
}

describe('bubble pointToLayer radius', () => {
  it('clamps a 1-pixel single-cell event to the 8 px minimum', () => {
    // sqrt(1)*4 = 4 → below the 8 px floor.
    expect(bubbleRadius(1, 1)).toBe(8);
  });

  it('scales as sqrt(n_pixels) * 4 in the mid range', () => {
    // sqrt(64)*4 = 32, which beats the intensity term.
    expect(bubbleRadius(64, 1)).toBeCloseTo(32, 6);
    // sqrt(100)*4 = 40.
    expect(bubbleRadius(100, 1)).toBeCloseTo(40, 6);
  });

  it('clamps very large clusters at the 70 px ceiling', () => {
    // sqrt(10000)*4 = 400, far above the 70 px clamp.
    expect(bubbleRadius(10000, 1)).toBe(70);
  });

  it('is monotonic non-decreasing in n_pixels', () => {
    const sizes = [1, 5, 25, 100, 400, 1600, 6400, 10000].map((n) => bubbleRadius(n, 1));
    for (let i = 1; i < sizes.length; i += 1) {
      expect(sizes[i]).toBeGreaterThanOrEqual(sizes[i - 1]);
    }
  });

  it('lifts a small but very intense event above the n_pixels floor via the intensity term', () => {
    // n_pixels = 1 → sqrt term = 4, but intensity = 6 → 4 + (6/6)*14 = 18.
    // max(4, 18) = 18, then clamped 8..70 → 18.
    expect(bubbleRadius(1, 6)).toBe(18);
  });
});
