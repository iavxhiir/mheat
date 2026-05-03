/**
 * HeroStat — banner at the top of the sidebar that reads
 * "X marine heatwaves detected" before any other control. Animates
 * the count via useCountUp, honours prefers-reduced-motion, falls back
 * to "…" while loading, and surfaces a per-category chip breakdown.
 *
 * The hook reads window.matchMedia('(prefers-reduced-motion: reduce)')
 * on first render, so we stub it deterministically per test.
 */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { HeroStat } from '../App';
import type { MhwEventCollection, MhwEventFeature } from '../types';

function feat(category: number, id = `e${category}`): MhwEventFeature {
  return {
    type: 'Feature',
    id,
    geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
    properties: {
      event_id: id,
      date_start: '2024-07-01',
      date_end: '2024-07-15',
      date_peak: '2024-07-08',
      duration_days: 14,
      intensity_max: 3.4,
      intensity_mean: 2.1,
      intensity_cumulative: 30,
      category,
      category_name: ['', 'I Moderate', 'II Strong', 'III Severe', 'IV Extreme', 'V Super-Extreme'][category],
      n_pixels: 100,
      centroid: [12, 40],
    },
  };
}

function collection(categories: number[]): MhwEventCollection {
  return {
    type: 'FeatureCollection',
    features: categories.map((c, i) => feat(c, `evt-${i}`)),
  };
}

/**
 * Force prefers-reduced-motion: reduce so useCountUp snaps directly
 * to its target instead of animating across requestAnimationFrame
 * frames — keeps the test deterministic and free of timer juggling.
 */
function stubReducedMotion() {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((q: string) => ({
      matches: q.includes('prefers-reduced-motion'),
      media: q,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

describe('HeroStat', () => {
  beforeEach(() => {
    stubReducedMotion();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders "N marine heatwaves" with the right total', () => {
    const events = collection([1, 2, 3, 4, 5]);
    render(<HeroStat events={events} start="2024-07-01" end="2024-08-31" loading={false} />);
    // Total digit and pluralised label are rendered as separate nodes; assert each.
    expect(screen.getByText('5')).toBeTruthy();
    expect(screen.getByText('marine heatwaves')).toBeTruthy();
    // Date range shows in the meta row.
    expect(screen.getByText(/2024-07-01.+2024-08-31/)).toBeTruthy();
  });

  it('renders per-category chips in descending category order, only for non-zero buckets', () => {
    const events = collection([1, 1, 3, 5, 5, 5]); // 3 cat-5, 1 cat-3, 2 cat-1; no cat-2/4
    const { container } = render(
      <HeroStat events={events} start="2024-07-01" end="2024-08-31" loading={false} />,
    );
    const chips = container.querySelectorAll('.hero-stat-chip');
    expect(chips.length).toBe(3);
    // Chips are ordered V → I (descending category).
    const chipTexts = Array.from(chips).map((c) => c.textContent ?? '');
    expect(chipTexts[0]).toMatch(/3.*V/);
    expect(chipTexts[1]).toMatch(/1.*III/);
    expect(chipTexts[2]).toMatch(/2.*I/);
    // Severe (≥III) tally surfaces in the meta row: 3 (V) + 1 (III) = 4.
    expect(screen.getByText(/4 severe/i)).toBeTruthy();
  });

  it('shows "…" while loading and suppresses the chip breakdown', () => {
    const events = collection([1, 2, 3]);
    const { container } = render(
      <HeroStat events={events} start="2024-07-01" end="2024-08-31" loading={true} />,
    );
    expect(screen.getByText('…')).toBeTruthy();
    expect(container.querySelectorAll('.hero-stat-chip').length).toBe(0);
    // Severe affordance is suppressed during loading too.
    expect(screen.queryByText(/severe/i)).toBeNull();
  });

  it('uses singular "marine heatwave" for exactly one event', () => {
    const events = collection([4]);
    render(<HeroStat events={events} start="2024-07-01" end="2024-08-31" loading={false} />);
    expect(screen.getByText('marine heatwave')).toBeTruthy();
    expect(screen.queryByText('marine heatwaves')).toBeNull();
  });

  it('uses plural "marine heatwaves" for two or more events', () => {
    const events = collection([1, 2]);
    render(<HeroStat events={events} start="2024-07-01" end="2024-08-31" loading={false} />);
    expect(screen.getByText('marine heatwaves')).toBeTruthy();
    expect(screen.queryByText('marine heatwave')).toBeNull();
  });

  it('renders zero state with the .is-zero modifier and "marine heatwaves"', () => {
    const events = collection([]);
    const { container } = render(
      <HeroStat events={events} start="2024-07-01" end="2024-08-31" loading={false} />,
    );
    // "0 marine heatwaves" with the zero-styled num element.
    expect(screen.getByText('0')).toBeTruthy();
    expect(screen.getByText('marine heatwaves')).toBeTruthy();
    expect(container.querySelector('.hero-stat-num.is-zero')).toBeTruthy();
    expect(container.querySelectorAll('.hero-stat-chip').length).toBe(0);
  });

  it('renders the live region with role="status" + aria-live="polite"', () => {
    const events = collection([2]);
    const { container } = render(
      <HeroStat events={events} start="2024-07-01" end="2024-08-31" loading={false} />,
    );
    const region = container.querySelector('.hero-stat');
    expect(region).toBeTruthy();
    expect(region!.getAttribute('role')).toBe('status');
    expect(region!.getAttribute('aria-live')).toBe('polite');
  });

  it('handles a null events collection without crashing (renders zero)', () => {
    render(<HeroStat events={null} start="2024-07-01" end="2024-08-31" loading={false} />);
    expect(screen.getByText('0')).toBeTruthy();
    expect(screen.getByText('marine heatwaves')).toBeTruthy();
  });
});
