import React from 'react';
import { useT } from '../i18n';

interface Props {
  start: string;
  end: string;
  onChange: (s: string, e: string) => void;
  /** Optional inclusive lower bound for both inputs (live-mode only). */
  min?: string;
  /** Optional inclusive upper bound for both inputs (live-mode only). */
  max?: string;
}

const PRESETS: { label: string; start: string; end: string }[] = [
  { label: '2003 Euro heatwave', start: '2003-06-01', end: '2003-08-31' },
  { label: '2022 Med summer', start: '2022-05-15', end: '2022-09-15' },
  { label: '2024 season', start: '2024-05-01', end: '2024-09-30' },
];

export function Timeline({ start, end, onChange, min, max }: Props) {
  const { t } = useT();
  // Drop presets that fall entirely outside the available extent.
  const visiblePresets = PRESETS.filter((p) => {
    if (min && p.end < min) return false;
    if (max && p.start > max) return false;
    return true;
  });
  return (
    <fieldset className="timeline">
      <legend className="timeline-title">{t('timeline.title')}</legend>
      <div className="timeline-inputs">
        <label>
          {t('timeline.start')}&nbsp;
          <input
            type="date"
            value={start}
            min={min}
            max={max}
            onChange={(e) => onChange(e.target.value, end)}
            aria-label={t('timeline.start')}
          />
        </label>
        <label>
          {t('timeline.end')}&nbsp;
          <input
            type="date"
            value={end}
            min={min}
            max={max}
            onChange={(e) => onChange(start, e.target.value)}
            aria-label={t('timeline.end')}
          />
        </label>
      </div>
      <div className="timeline-presets" role="group" aria-label="Preset date ranges">
        {visiblePresets.map((p) => (
          <button
            key={p.label}
            onClick={() => onChange(p.start, p.end)}
            className="preset-btn"
            type="button"
            aria-label={`Jump to preset: ${p.label}, ${p.start} to ${p.end}`}
          >
            {p.label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}
