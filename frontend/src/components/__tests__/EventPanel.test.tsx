import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { EventPanel } from '../EventPanel';
import { I18nProvider } from '../../i18n';
import type { MhwEventFeature } from '../../types';

/**
 * EventPanel reads strings from the i18n provider; wrap every render so the
 * EN dictionary supplies the labels the assertions below match against.
 */
function renderPanel(event: MhwEventFeature | null) {
  return render(
    <I18nProvider>
      <EventPanel event={event} />
    </I18nProvider>,
  );
}

// Stub Plotly so the dynamic import doesn't try to pull a 4.6 MB bundle into jsdom.
vi.mock('plotly.js-dist-min', () => ({
  default: { react: vi.fn() },
  react: vi.fn(),
}));

function makeEvent(overrides: Partial<MhwEventFeature['properties']> = {}): MhwEventFeature {
  return {
    type: 'Feature',
    id: 'mhw-cluster-0001',
    geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
    properties: {
      event_id: 'mhw-cluster-0001',
      date_start: '2022-07-10',
      date_end: '2022-08-15',
      date_peak: '2022-07-25',
      duration_days: 36,
      intensity_max: 4.16,
      intensity_mean: 3.02,
      intensity_cumulative: 108.7,
      category: 5,
      category_name: 'V Super-Extreme',
      n_pixels: 120,
      centroid: [12.5, 42.3],
      impact: { n_aquaculture_sites: 7, mpa_area_km2: 412.8, seagrass_area_km2: 55.2 },
      ...overrides,
    },
  };
}

describe('EventPanel', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          time: ['2022-07-01', '2022-07-02'],
          sst: [26.5, 27.1],
          climatology: [24.0, 24.1],
          threshold: [25.5, 25.6],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('renders an empty-state hint when no event is selected', () => {
    renderPanel(null);
    expect(screen.getByText(/Click an MHW polygon/i)).toBeTruthy();
  });

  it('renders the event id, category badge and headline stats', () => {
    renderPanel(makeEvent());
    expect(screen.getByText('mhw-cluster-0001')).toBeTruthy();
    expect(screen.getByText(/V Super-Extreme/)).toBeTruthy();
    expect(screen.getByText('2022-07-10')).toBeTruthy(); // start
    expect(screen.getByText('2022-08-15')).toBeTruthy(); // end
    expect(screen.getByText('36 days')).toBeTruthy();
  });

  it('formats centroid and peak intensity with units', () => {
    renderPanel(makeEvent());
    // Centroid shown as "lat°N, lon°E" with 2 decimals each.
    expect(screen.getByText(/42\.30.*12\.50/)).toBeTruthy();
    // Peak intensity shows °C symbol.
    expect(screen.getAllByText((t) => /4\.16.*C/.test(t)).length).toBeGreaterThan(0);
  });

  it('renders the sectoral-impact block when impact data is present', () => {
    renderPanel(makeEvent());
    expect(screen.getByText('Aquaculture sites')).toBeTruthy();
    expect(screen.getByText('7')).toBeTruthy();
    // km² area, one decimal.
    expect(screen.getAllByText((t) => /412\.8/.test(t)).length).toBeGreaterThan(0);
  });

  it('omits the impact block when impact data is null', () => {
    renderPanel(makeEvent({ impact: null }));
    expect(screen.queryByText('Sectoral impact')).toBeNull();
    expect(screen.queryByText('Aquaculture sites')).toBeNull();
  });

  it('renders the sparkline loading hint before the series arrives', () => {
    renderPanel(makeEvent());
    expect(screen.getByText(/loading series/i)).toBeTruthy();
  });

  it('surfaces a friendly error when the series fetch fails', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('boom', { status: 500, statusText: 'Server Error' }),
    );
    renderPanel(makeEvent());
    await waitFor(() => {
      expect(screen.getByText(/Series unavailable/i)).toBeTruthy();
    });
  });
});
