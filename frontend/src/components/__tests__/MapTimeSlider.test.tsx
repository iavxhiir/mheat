import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { MapTimeSlider, __test__ } from '../MapTimeSlider';

const { dateToInt, intToDate, FRAME_MS } = __test__;

function renderSlider(props: {
  value: string;
  min: string;
  max: string;
  onChange?: (d: string) => void;
}) {
  const onChange = props.onChange ?? vi.fn();
  return {
    onChange,
    ...render(
      <I18nProvider>
        <MapTimeSlider {...props} onChange={onChange} />
      </I18nProvider>,
    ),
  };
}

describe('MapTimeSlider · date↔int conversion', () => {
  it('round-trips a typical demo date', () => {
    expect(intToDate(dateToInt('2022-07-20'))).toBe('2022-07-20');
  });

  it('round-trips epoch boundaries', () => {
    expect(intToDate(dateToInt('1970-01-01'))).toBe('1970-01-01');
  });

  it('handles dates that span DST without drifting (uses UTC)', () => {
    expect(intToDate(dateToInt('2022-03-27'))).toBe('2022-03-27');
    expect(intToDate(dateToInt('2022-03-28'))).toBe('2022-03-28');
  });

  it('returns a stable integer day count', () => {
    const a = dateToInt('2022-07-20');
    const b = dateToInt('2022-07-21');
    expect(b - a).toBe(1);
  });
});

describe('MapTimeSlider · scrubbing', () => {
  it('calls onChange with the new date when the range is dragged', () => {
    const { onChange } = renderSlider({
      value: '2022-07-20',
      min: '2022-05-15',
      max: '2022-09-15',
    });
    const slider = screen.getByRole('slider');
    const targetInt = dateToInt('2022-08-01');
    fireEvent.change(slider, { target: { value: String(targetInt) } });
    expect(onChange).toHaveBeenCalledWith('2022-08-01');
  });

  it('renders the current date in the readout', () => {
    renderSlider({ value: '2022-07-20', min: '2022-05-15', max: '2022-09-15' });
    expect(screen.getByText('2022-07-20')).toBeTruthy();
  });

  it('renders the min and max in the axis labels', () => {
    renderSlider({ value: '2022-07-20', min: '2022-05-15', max: '2022-09-15' });
    expect(screen.getByText('2022-05-15')).toBeTruthy();
    expect(screen.getByText('2022-09-15')).toBeTruthy();
  });

  it('clamps a stale value above the new max back into range', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <I18nProvider>
        <MapTimeSlider
          value="2022-09-15"
          min="2022-05-15"
          max="2022-09-15"
          onChange={onChange}
        />
      </I18nProvider>,
    );
    onChange.mockClear();
    // Bounds shrink; the prop value (2022-09-15) is now > max (2022-08-01).
    rerender(
      <I18nProvider>
        <MapTimeSlider
          value="2022-09-15"
          min="2022-05-15"
          max="2022-08-01"
          onChange={onChange}
        />
      </I18nProvider>,
    );
    expect(onChange).toHaveBeenCalledWith('2022-08-01');
  });
});

describe('MapTimeSlider · play/pause', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('advances the value by one day per FRAME_MS while playing', () => {
    let current = '2022-07-20';
    const onChange = vi.fn((d: string) => {
      current = d;
    });
    const { rerender } = render(
      <I18nProvider>
        <MapTimeSlider value={current} min="2022-05-15" max="2022-09-15" onChange={onChange} />
      </I18nProvider>,
    );
    // Click play.
    fireEvent.click(screen.getByLabelText(/play/i));
    // First tick.
    act(() => {
      vi.advanceTimersByTime(FRAME_MS);
    });
    expect(onChange).toHaveBeenCalledWith('2022-07-21');
    // Re-render with the new prop value and tick again — production code
    // does this via parent state; here we mirror it.
    onChange.mockClear();
    rerender(
      <I18nProvider>
        <MapTimeSlider value="2022-07-21" min="2022-05-15" max="2022-09-15" onChange={onChange} />
      </I18nProvider>,
    );
    act(() => {
      vi.advanceTimersByTime(FRAME_MS);
    });
    expect(onChange).toHaveBeenCalledWith('2022-07-22');
  });

  it('loops back to min after passing max', () => {
    const onChange = vi.fn();
    render(
      <I18nProvider>
        <MapTimeSlider
          value="2022-09-15"
          min="2022-05-15"
          max="2022-09-15"
          onChange={onChange}
        />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByLabelText(/play/i));
    act(() => {
      vi.advanceTimersByTime(FRAME_MS);
    });
    expect(onChange).toHaveBeenCalledWith('2022-05-15');
  });
});
