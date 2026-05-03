import React from 'react';
import type { MhwEventCollection } from '../types';
import { useT } from '../i18n';
import { usePlainMode, categoryDisplay, useCbSafe, categoryGlyph } from './PlainMode';

export const CATEGORY_COLORS: Record<number, string> = {
  1: '#ffd166', // Moderate
  2: '#ff9f1c', // Strong
  3: '#e63946', // Severe
  4: '#9d0208', // Extreme
  5: '#370617', // Super-Extreme
};

export const CATEGORY_LABELS: Record<number, string> = {
  1: 'I - Moderate',
  2: 'II - Strong',
  3: 'III - Severe',
  4: 'IV - Extreme',
  5: 'V - Super-Extreme',
};

export const CATEGORY_SHORT: Record<number, string> = {
  1: 'I',
  2: 'II',
  3: 'III',
  4: 'IV',
  5: 'V',
};

export const CATEGORY_NAME: Record<number, string> = {
  1: 'Moderate',
  2: 'Strong',
  3: 'Severe',
  4: 'Extreme',
  5: 'Super-Extreme',
};

interface LegendProps {
  events?: MhwEventCollection | null;
  minCategory?: number;
  onCategoryClick?: (cat: number) => void;
}

export function Legend({ events, minCategory, onCategoryClick }: LegendProps) {
  const { t } = useT();
  const [plain] = usePlainMode();
  const [cbSafe] = useCbSafe();
  const counts: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  if (events) {
    for (const f of events.features) {
      const c = f.properties.category;
      if (counts[c] !== undefined) counts[c] += 1;
    }
  }
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...Object.values(counts));

  return (
    <div className="legend" role="region" aria-label="MHW category legend">
      <div className="legend-title">{t('legend.title')}</div>
      {[1, 2, 3, 4, 5].map((c) => {
        const n = counts[c];
        const pct = total > 0 ? Math.round((n / total) * 100) : 0;
        const width = (n / max) * 100;
        const active = minCategory === c;
        const clickable = onCategoryClick !== undefined;
        // In plain mode swap "I Moderate" for "Mild warming" etc.; in
        // technical mode we keep the historical CATEGORY_LABELS string
        // so existing tests + reviewer expectations still hold.
        const longLabel = plain
          ? categoryDisplay(c, true, t, 'long')
          : CATEGORY_LABELS[c];
        const shortLabel = plain
          ? categoryDisplay(c, true, t, 'short')
          : CATEGORY_SHORT[c];
        const nameLabel = plain
          ? ''
          : CATEGORY_NAME[c];
        return (
          <button
            key={c}
            className={`legend-bar-row${active ? ' active' : ''}${clickable ? ' clickable' : ''}`}
            onClick={clickable ? () => onCategoryClick!(c) : undefined}
            aria-label={`${longLabel} — ${n} events (${pct}%). Click to filter.`}
            aria-pressed={active}
            disabled={!clickable}
            type="button"
          >
            <span
              className="legend-swatch"
              style={{ background: `var(--cat-${c}, ${CATEGORY_COLORS[c]})` }}
              aria-hidden="true"
            />
            <span className="legend-bar-label">
              {cbSafe && (
                <span className="cat-glyph" aria-hidden="true">
                  {categoryGlyph(c, true)}
                </span>
              )}
              <span className="legend-bar-cat">{shortLabel}</span>
              {nameLabel ? <> {nameLabel}</> : null}
            </span>
            <span className="legend-bar-track" aria-hidden="true">
              <span
                className="legend-bar-fill"
                style={{
                  width: `${width}%`,
                  background: `var(--cat-${c}, ${CATEGORY_COLORS[c]})`,
                }}
              />
            </span>
            <span className="legend-bar-count">
              {n} <span className="legend-bar-pct">({pct}%)</span>
            </span>
          </button>
        );
      })}
      {events && onCategoryClick && minCategory && minCategory > 1 && (
        <button
          className="legend-clear"
          type="button"
          onClick={() => onCategoryClick(1)}
          aria-label={t('legend.clearAria')}
        >
          {t('legend.showAll')}
        </button>
      )}
    </div>
  );
}
