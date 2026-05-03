import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useT } from '../i18n';

interface Props {
  /** Current anomaly date as YYYY-MM-DD. */
  value: string;
  /** Inclusive lower bound (YYYY-MM-DD). */
  min: string;
  /** Inclusive upper bound (YYYY-MM-DD). */
  max: string;
  onChange: (date: string) => void;
}

const MS_PER_DAY = 86400000;
const FRAME_MS = 220; // ~1 day per ~220 ms — feels alive without being frantic.

/**
 * Convert a YYYY-MM-DD ISO date to a UTC day-since-epoch integer. Using UTC
 * sidesteps DST and timezone-rollover bugs that bite range-input scrubbers.
 */
function dateToInt(s: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s.trim());
  if (!m) return 0;
  return Math.floor(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) / MS_PER_DAY);
}

function intToDate(n: number): string {
  const d = new Date(n * MS_PER_DAY);
  const y = d.getUTCFullYear();
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
  const da = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${mo}-${da}`;
}

const PLAY_SVG =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
  '<path d="M4 2.5v11l10-5.5z" fill="currentColor"/></svg>';

const PAUSE_SVG =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
  '<rect x="3.5" y="2.5" width="3.5" height="11" fill="currentColor"/>' +
  '<rect x="9" y="2.5" width="3.5" height="11" fill="currentColor"/></svg>';

const STEP_BACK_SVG =
  '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
  '<path d="M11 3.5L5 8l6 4.5z M3.5 3v10" stroke="currentColor" ' +
  'stroke-width="1.5" fill="currentColor" stroke-linejoin="round"/></svg>';

const STEP_FWD_SVG =
  '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
  '<path d="M5 3.5L11 8l-6 4.5z M12.5 3v10" stroke="currentColor" ' +
  'stroke-width="1.5" fill="currentColor" stroke-linejoin="round"/></svg>';

/**
 * Bottom-of-map time scrubber. Replaces the static anomaly-date input with
 * a draggable slider plus play/pause + step buttons, so a reviewer can
 * watch an event evolve across the SST anomaly raster instead of pulling
 * down a date picker each time.
 *
 * Keyboard:
 *   * ``Space``     — toggle play/pause
 *   * ``←``/``→``    — step one day (when not focused on an input)
 *   * ``Home``/``End`` — jump to ``min``/``max``
 */
export function MapTimeSlider({ value, min, max, onChange }: Props) {
  const { t } = useT();
  const minInt = useMemo(() => dateToInt(min), [min]);
  const maxInt = useMemo(() => dateToInt(max), [max]);
  const valueInt = useMemo(() => dateToInt(value), [value]);
  const span = Math.max(1, maxInt - minInt);

  // `playing` lives in state so the button can re-render; `playRef` carries
  // the live value into intervals without retriggering the effect.
  const [playing, setPlaying] = useState(false);
  const playRef = useRef(false);
  playRef.current = playing;

  // Hold the latest value in a ref so the play loop (a single setInterval)
  // doesn't capture a stale closure on every value change.
  const valueRef = useRef(valueInt);
  valueRef.current = valueInt;

  // Drag-to-commit throttling: the first scrub event fires onChange
  // immediately (so single clicks and tests work synchronously), then we
  // suppress further fires for `THROTTLE_MS`. The thumb still tracks the
  // mouse at 60 fps via `dragValue` (local), and after the throttle window
  // any pending value commits via the trailing fire. Stops the slider
  // from spamming /api/anomaly + /api/freshness per pixel of mouse-move.
  const THROTTLE_MS = 80;
  const [dragValue, setDragValue] = useState<number | null>(null);
  const lastFireAt = useRef<number>(0);
  const pendingTimer = useRef<number | null>(null);
  const pendingValue = useRef<number | null>(null);
  useEffect(() => () => {
    if (pendingTimer.current) window.clearTimeout(pendingTimer.current);
  }, []);

  const clamp = useCallback(
    (n: number) => Math.min(maxInt, Math.max(minInt, n)),
    [minInt, maxInt],
  );

  const setInt = useCallback(
    (n: number) => onChange(intToDate(clamp(n))),
    [onChange, clamp],
  );

  // Auto-stop if the bounds shift past the current value (e.g. live-mode
  // extent rollover at midnight); otherwise the slider would silently drift
  // off-range while still claiming to be playing.
  useEffect(() => {
    if (valueInt > maxInt) onChange(intToDate(maxInt));
    if (valueInt < minInt) onChange(intToDate(minInt));
    // We only care about bounds changes here; value changes handle themselves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [minInt, maxInt]);

  // Single play loop: advances by 1 day per FRAME_MS, loops back to min on
  // overrun. Using setInterval (vs requestAnimationFrame) keeps the rate
  // wall-clock-stable in a backgrounded tab — important when the user
  // alt-tabs to read the proposal mid-demo.
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      const next = valueRef.current + 1;
      if (next > maxInt) {
        // Loop back so the demo keeps cycling — easier than autostop for
        // recording a screencast or a reviewer who walked away.
        onChange(intToDate(minInt));
      } else {
        onChange(intToDate(next));
      }
    }, FRAME_MS);
    return () => window.clearInterval(id);
  }, [playing, minInt, maxInt, onChange]);

  // Global keyboard shortcuts (Space / arrows / Home / End). Bail out if a
  // form field is focused so we don't fight the date picker / sidebar.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const tgt = ev.target as HTMLElement | null;
      if (tgt && /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(tgt.tagName)) return;
      if (ev.key === ' ') {
        ev.preventDefault();
        setPlaying((p) => !p);
      } else if (ev.key === 'ArrowLeft') {
        ev.preventDefault();
        setPlaying(false);
        setInt(valueRef.current - 1);
      } else if (ev.key === 'ArrowRight') {
        ev.preventDefault();
        setPlaying(false);
        setInt(valueRef.current + 1);
      } else if (ev.key === 'Home') {
        ev.preventDefault();
        setPlaying(false);
        setInt(minInt);
      } else if (ev.key === 'End') {
        ev.preventDefault();
        setPlaying(false);
        setInt(maxInt);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setInt, minInt, maxInt]);

  return (
    <div
      className="map-time-slider"
      role="group"
      aria-label={t('timeSlider.aria')}
      data-testid="map-time-slider"
    >
      <button
        type="button"
        className={`mts-btn mts-play${playing ? ' is-playing' : ''}`}
        onClick={() => setPlaying((p) => !p)}
        aria-label={playing ? t('timeSlider.pause') : t('timeSlider.play')}
        aria-pressed={playing}
        title={
          playing
            ? `${t('timeSlider.pause')} (Space)`
            : `${t('timeSlider.play')} (Space)`
        }
        dangerouslySetInnerHTML={{ __html: playing ? PAUSE_SVG : PLAY_SVG }}
      />
      <button
        type="button"
        className="mts-btn mts-step"
        onClick={() => {
          setPlaying(false);
          setInt(valueInt - 1);
        }}
        aria-label={t('timeSlider.stepBack')}
        title={`${t('timeSlider.stepBack')} (←)`}
        dangerouslySetInnerHTML={{ __html: STEP_BACK_SVG }}
      />
      <div className="mts-track-wrap">
        <input
          type="range"
          className="mts-track"
          min={minInt}
          max={maxInt}
          step={1}
          // Use a local "drag" state so the thumb tracks the mouse at 60fps
          // without firing a network-bound onChange per pixel; commit the
          // real onChange only on release (or 80 ms after the last move,
          // whichever comes first). Massively smoother scrubbing on the
          // 12k-day cube — was firing /api/anomaly + freshness on every
          // mouse-move event before this debounce.
          value={dragValue ?? valueInt}
          onMouseDown={() => setPlaying(false)}
          onChange={(e) => {
            const n = Number(e.target.value);
            setDragValue(n);
            const now = performance.now();
            const elapsed = now - lastFireAt.current;
            if (elapsed >= THROTTLE_MS) {
              // Leading-edge fire — immediate response (and keeps the
              // synchronous "fire onChange on a single change event" test
              // passing without test-side timer plumbing).
              lastFireAt.current = now;
              setInt(n);
              setDragValue(null);
              pendingValue.current = null;
            } else {
              // Inside the throttle window — defer to the trailing fire.
              pendingValue.current = n;
              if (pendingTimer.current) window.clearTimeout(pendingTimer.current);
              pendingTimer.current = window.setTimeout(() => {
                if (pendingValue.current != null) {
                  lastFireAt.current = performance.now();
                  setInt(pendingValue.current);
                  setDragValue(null);
                  pendingValue.current = null;
                }
              }, THROTTLE_MS - elapsed);
            }
          }}
          onMouseUp={() => {
            if (pendingTimer.current) window.clearTimeout(pendingTimer.current);
            if (pendingValue.current != null) {
              lastFireAt.current = performance.now();
              setInt(pendingValue.current);
              pendingValue.current = null;
            }
            setDragValue(null);
          }}
          onTouchEnd={() => {
            if (pendingTimer.current) window.clearTimeout(pendingTimer.current);
            if (pendingValue.current != null) {
              lastFireAt.current = performance.now();
              setInt(pendingValue.current);
              pendingValue.current = null;
            }
            setDragValue(null);
          }}
          aria-valuemin={minInt}
          aria-valuemax={maxInt}
          aria-valuenow={valueInt}
          aria-valuetext={value}
          aria-label={t('timeSlider.scrubAria')}
        />
        <div className="mts-axis" aria-hidden="true">
          <span>{min}</span>
          <span>{max}</span>
        </div>
      </div>
      <button
        type="button"
        className="mts-btn mts-step"
        onClick={() => {
          setPlaying(false);
          setInt(valueInt + 1);
        }}
        aria-label={t('timeSlider.stepFwd')}
        title={`${t('timeSlider.stepFwd')} (→)`}
        dangerouslySetInnerHTML={{ __html: STEP_FWD_SVG }}
      />
      <div className="mts-readout" aria-live="polite">
        <span className="mts-readout-date">{value}</span>
        <span className="mts-readout-pos" aria-hidden="true">
          {Math.round(((valueInt - minInt) / span) * 100)}%
        </span>
      </div>
    </div>
  );
}

// Exported for tests.
export const __test__ = { dateToInt, intToDate, FRAME_MS };
