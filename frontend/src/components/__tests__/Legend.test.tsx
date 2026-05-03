import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, type RenderResult } from '@testing-library/react';
import { Legend, CATEGORY_COLORS, CATEGORY_LABELS } from '../Legend';
import { I18nProvider } from '../../i18n';
import type { MhwEventCollection } from '../../types';

/**
 * Legend reads strings from i18n; wrap every render so the EN dictionary
 * supplies the labels asserted below.
 */
type LegendProps = React.ComponentProps<typeof Legend>;
function renderLegend(props: LegendProps = {}): RenderResult {
  return render(
    <I18nProvider>
      <Legend {...props} />
    </I18nProvider>,
  );
}

function makeEvents(categories: number[]): MhwEventCollection {
  return {
    type: 'FeatureCollection',
    features: categories.map((c, i) => ({
      type: 'Feature',
      id: `e${i}`,
      geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
      properties: {
        event_id: `e${i}`,
        date_start: '2022-07-01',
        date_end: '2022-07-10',
        date_peak: '2022-07-05',
        duration_days: 10,
        intensity_max: 3,
        intensity_mean: 2,
        intensity_cumulative: 20,
        category: c,
        category_name: 'x',
        n_pixels: 100,
        centroid: [12, 40],
      },
    })),
  };
}

describe('Legend', () => {
  it('renders all five Hobday 2018 categories', () => {
    renderLegend();
    for (const c of [1, 2, 3, 4, 5]) {
      expect(screen.getByLabelText(new RegExp(`^${CATEGORY_LABELS[c].replace(/[-\s]/g, '.')}`)))
        .toBeTruthy();
    }
  });

  it('counts events by category and shows percentages', () => {
    const events = makeEvents([1, 1, 2, 3, 3, 3]);
    renderLegend({ events });
    // 3 events of category III → 50%.
    const cat3 = screen.getByLabelText(/III - Severe — 3 events \(50%\)/);
    expect(cat3).toBeTruthy();
    // 2 events of category I → 33%.
    const cat1 = screen.getByLabelText(/I - Moderate — 2 events \(33%\)/);
    expect(cat1).toBeTruthy();
    // 0 events for cat 4 → 0%.
    const cat4 = screen.getByLabelText(/IV - Extreme — 0 events \(0%\)/);
    expect(cat4).toBeTruthy();
  });

  it('calls onCategoryClick when a row is clicked', () => {
    const handler = vi.fn();
    const events = makeEvents([2, 2, 3]);
    renderLegend({ events, onCategoryClick: handler });
    fireEvent.click(screen.getByLabelText(/III - Severe/));
    expect(handler).toHaveBeenCalledWith(3);
  });

  it('shows "Show all categories" clear button only when a non-default filter is active', () => {
    const events = makeEvents([1]);
    const { rerender } = render(
      <I18nProvider>
        <Legend events={events} minCategory={1} onCategoryClick={() => {}} />
      </I18nProvider>,
    );
    expect(screen.queryByText('Show all categories')).toBeNull();
    rerender(
      <I18nProvider>
        <Legend events={events} minCategory={3} onCategoryClick={() => {}} />
      </I18nProvider>,
    );
    expect(screen.getByText('Show all categories')).toBeTruthy();
  });

  it('exposes canonical Hobday colour hex codes', () => {
    expect(CATEGORY_COLORS[1]).toBe('#ffd166');
    expect(CATEGORY_COLORS[5]).toBe('#370617');
  });
});
