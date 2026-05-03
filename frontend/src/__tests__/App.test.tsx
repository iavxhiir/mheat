/**
 * App-level tests: live badge + climatology-missing banner + loading pill.
 *
 * Heavy children (MapView/Leaflet, EventChart/EventPanel sparklines) are
 * mocked so we can render <App /> in jsdom without canvas or Plotly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import App from '../App';
import { I18nProvider } from '../i18n';

vi.mock('../components/MapView', () => ({
  MapView: () => <div data-testid="mock-map" />,
  // MapViewGL also imports `eventsAsBubblesGeoJson` from this module; stub
  // it so the lazy MPA-toggle path (cross-cut #4) doesn't blow up when
  // events have resolved and the bubble useMemo recomputes.
  eventsAsBubblesGeoJson: () => ({ type: 'FeatureCollection', features: [] }),
}));
vi.mock('../components/EventChart', () => ({
  EventChart: () => <div data-testid="mock-event-chart" />,
}));
vi.mock('../components/EventPanel', () => ({
  EventPanel: () => <div data-testid="mock-event-panel" />,
}));

const HEALTH = { status: 'ok', version: 'test' };
const READYZ_OK = {
  status: 'ok', cms_credentials: true,
  cache_dir: '/tmp', zarr_store: '/tmp/zarr',
  sst_cache_present: true, climatology_present: true,
};
const READYZ_MISSING = { ...READYZ_OK, climatology_present: false };
const EXTENT = {
  start: '2023-01-01', end: '2026-04-25',
  vmin_degC: -3, vmax_degC: 3,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200, headers: { 'content-type': 'application/json' },
  });
}

function makeFetch(opts: { health?: unknown; readyz: unknown; extent: unknown }) {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === 'string' ? input : input.toString();
    if (url.startsWith('/api/health')) return jsonResponse(opts.health ?? HEALTH);
    if (url.startsWith('/api/readyz')) return jsonResponse(opts.readyz);
    if (url.startsWith('/api/anomaly/extent')) return jsonResponse(opts.extent);
    if (url.startsWith('/api/overlays/') || url.startsWith('/api/events')) {
      return jsonResponse({ type: 'FeatureCollection', features: [] });
    }
    return new Response('{}', { status: 404 });
  });
}

function renderApp() {
  return render(
    <I18nProvider>
      <App />
    </I18nProvider>,
  );
}

describe('App — cache-backed live signals', () => {
  beforeEach(() => {
    vi.spyOn(window.history, 'replaceState').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the climatology-missing banner when readyz reports climatology_present:false', async () => {
    vi.stubGlobal('fetch', makeFetch({ readyz: READYZ_MISSING, extent: EXTENT }));
    renderApp();
    const banner = await waitFor(() => screen.getByTestId('climatology-missing-banner'));
    expect(banner.textContent).toMatch(/no climatology artifact/i);
    expect(banner.textContent).toMatch(/bootstrap_climatology\.py/);
  });

  it('does NOT render the climatology banner when climatology_present is true', async () => {
    vi.stubGlobal('fetch', makeFetch({ readyz: READYZ_OK, extent: EXTENT }));
    renderApp();
    await waitFor(() => screen.getByTestId('live-badge'));
    expect(screen.queryByTestId('climatology-missing-banner')).toBeNull();
  });

  it('always renders the Copernicus live badge', async () => {
    vi.stubGlobal('fetch', makeFetch({ readyz: READYZ_OK, extent: EXTENT }));
    renderApp();
    const badge = await waitFor(() => screen.getByTestId('live-badge'));
    expect(badge.textContent).toMatch(/Copernicus Marine/);
  });

  it('shows a loading pill while initial fetches are in flight', async () => {
    // Slow-roll the extent fetch so the loading pill stays visible.
    let resolveExtent: ((v: Response) => void) | null = null;
    const slowExtent = new Promise<Response>((res) => { resolveExtent = res; });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/anomaly/extent')) return slowExtent;
      if (url.startsWith('/api/health')) return jsonResponse(HEALTH);
      if (url.startsWith('/api/readyz')) return jsonResponse(READYZ_OK);
      if (url.startsWith('/api/overlays/') || url.startsWith('/api/events')) {
        return jsonResponse({ type: 'FeatureCollection', features: [] });
      }
      return new Response('{}', { status: 404 });
    }));
    renderApp();
    await waitFor(() => screen.getByTestId('loading-pill'));
    if (resolveExtent) (resolveExtent as (v: Response) => void)(jsonResponse(EXTENT));
  });

  it('surfaces a toast when an EMODnet/EEA overlay fetch fails', async () => {
    // Cross-cut #4: sectoral overlays are now lazy — they only fetch when
    // the user toggles the layer ON. Simulate that flow: load the app,
    // wait for the layer-control to render, click the MPA checkbox, then
    // assert the failure toast appears once the (failing) lazy fetch lands.
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/health')) return jsonResponse(HEALTH);
      if (url.startsWith('/api/readyz')) return jsonResponse(READYZ_OK);
      if (url.startsWith('/api/anomaly/extent')) return jsonResponse(EXTENT);
      if (url.startsWith('/api/overlays/mpa')) return new Response('502', { status: 502 });
      if (url.startsWith('/api/overlays/') || url.startsWith('/api/events')) {
        return jsonResponse({ type: 'FeatureCollection', features: [] });
      }
      return new Response('{}', { status: 404 });
    }));
    // Suppress the expected console.error noise for the failed overlay.
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderApp();
    // Wait until the layer control has rendered (post-extent so the loading
    // pill has cleared and the sidebar is interactive).
    const mpaCheckbox = await waitFor(() =>
      screen.getByRole('checkbox', { name: /Toggle MPAs.*layer/i }),
    );
    fireEvent.click(mpaCheckbox);
    const toast = await waitFor(() =>
      screen.getByText(/EMODnet \/ EEA overlay services/i),
    );
    expect(toast.textContent).toMatch(/Sectoral layers may be incomplete/i);
    errSpy.mockRestore();
  });
});
