import { describe, it, expect } from 'vitest';
import {
  parseBbox,
  serialiseBbox,
  parseView,
  serialiseView,
  formatBboxHeader,
  type BBox,
} from '../lib/url';

describe('url.parseBbox', () => {
  it('accepts a canonical 4-float csv', () => {
    expect(parseBbox('6,38,14,43')).toEqual([6, 38, 14, 43]);
    expect(parseBbox('-6.0,30,36.5,46')).toEqual([-6.0, 30, 36.5, 46]);
  });

  it('rejects missing, malformed, or wrong-arity values', () => {
    expect(parseBbox(null)).toBeNull();
    expect(parseBbox(undefined)).toBeNull();
    expect(parseBbox('')).toBeNull();
    expect(parseBbox('not,a,bbox')).toBeNull();
    expect(parseBbox('1,2,3')).toBeNull();
    expect(parseBbox('1,2,3,4,5')).toBeNull();
  });

  it('rejects degenerate (min ≥ max) boxes', () => {
    expect(parseBbox('10,10,10,10')).toBeNull();
    expect(parseBbox('20,10,10,20')).toBeNull();
  });

  it('rejects out-of-range coordinates', () => {
    expect(parseBbox('-200,-10,10,10')).toBeNull();
    expect(parseBbox('-10,-100,10,10')).toBeNull();
    expect(parseBbox('-10,-10,200,10')).toBeNull();
    expect(parseBbox('-10,-10,10,100')).toBeNull();
  });
});

describe('url.serialiseBbox', () => {
  it('round-trips integer corners without decimals', () => {
    expect(serialiseBbox([6, 38, 14, 43])).toBe('6,38,14,43');
  });

  it('keeps 2-decimal precision on non-integer values', () => {
    expect(serialiseBbox([-6.0, 30.5, 36.53, 46.0])).toBe('-6,30.50,36.53,46');
  });

  it('returns undefined for empty input', () => {
    expect(serialiseBbox(null)).toBeUndefined();
    expect(serialiseBbox(undefined)).toBeUndefined();
  });
});

describe('url.parseView / serialiseView', () => {
  it('round-trips a full view state', () => {
    const view = {
      start: '2022-07-01',
      end: '2022-08-31',
      bbox: [6, 38, 14, 43] as BBox,
      minCategory: 3,
    };
    const qs = serialiseView(view);
    expect(parseView(qs)).toEqual(view);
  });

  it('omits min_category=1 (default) from the URL', () => {
    expect(serialiseView({ minCategory: 1 })).toBe('');
    expect(serialiseView({ minCategory: 3 })).toContain('min_category=3');
  });

  it('ignores malformed date strings', () => {
    const out = parseView('start=July-1st&end=2022-99-99');
    expect(out.start).toBeUndefined();
    expect(out.end).toBeUndefined();
  });

  it('ignores out-of-range minCategory', () => {
    expect(parseView('min_category=0').minCategory).toBeUndefined();
    expect(parseView('min_category=6').minCategory).toBeUndefined();
    expect(parseView('min_category=abc').minCategory).toBeUndefined();
  });

  it('tolerates a leading "?"', () => {
    expect(parseView('?bbox=1,2,3,4').bbox).toEqual([1, 2, 3, 4]);
  });
});

describe('url.formatBboxHeader', () => {
  it('uses E/N/W/S suffixes with one decimal', () => {
    expect(formatBboxHeader([6, 38, 14, 43])).toBe('6.0E-14.0E, 38.0N-43.0N');
  });

  it('handles the western Mediterranean (negative lon)', () => {
    expect(formatBboxHeader([-6, 30, 5, 44])).toBe('6.0W-5.0E, 30.0N-44.0N');
  });

  it('handles a southern-hemisphere demo box', () => {
    expect(formatBboxHeader([-10, -30, 10, -10])).toBe('10.0W-10.0E, 30.0S-10.0S');
  });
});
