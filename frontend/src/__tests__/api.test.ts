import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fetchEvents, fetchOverlay, fetchEventSeries } from '../api';

function okResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

describe('api.ts — URL composition and error handling', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(okResponse({ type: 'FeatureCollection', features: [] }));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetchEvents with no params hits /api/events with no query string', async () => {
    await fetchEvents();
    expect(fetchMock).toHaveBeenCalledWith('/api/events');
  });

  it('fetchEvents encodes bbox as comma-joined floats', async () => {
    await fetchEvents({ bbox: [6, 38, 14, 43], minCategory: 3 });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain('bbox=6%2C38%2C14%2C43');
    expect(url).toContain('min_category=3');
  });

  it('fetchEvents includes start/end when given', async () => {
    await fetchEvents({ start: '2022-07-01', end: '2022-07-31' });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain('start=2022-07-01');
    expect(url).toContain('end=2022-07-31');
  });

  it('fetchOverlay routes by kind', async () => {
    await fetchOverlay('mpa');
    expect(fetchMock).toHaveBeenCalledWith('/api/overlays/mpa');
  });

  it('fetchEventSeries URL-encodes the event id and includes lon/lat', async () => {
    await fetchEventSeries('evt/2022#3', 12.5, 40.1);
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain('/api/events/evt%2F2022%233/series?');
    expect(url).toContain('lon=12.5');
    expect(url).toContain('lat=40.1');
  });

  it('throws with status + preview body on non-2xx', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('boom: bad bbox', { status: 400, statusText: 'Bad Request' }),
    );
    await expect(fetchEvents({ bbox: [0, 0, 1, 1] })).rejects.toThrow(/400 Bad Request.*boom/);
  });
});
