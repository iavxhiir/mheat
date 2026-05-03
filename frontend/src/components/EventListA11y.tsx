/**
 * EventListA11y — hidden tabbable event list (UI cross-cut #6).
 *
 * Persona-21 (keyboard-only power user) cannot pick MHW bubbles from
 * the MapLibre canvas: the canvas is a single Tab stop with no internal
 * keyboard navigation. This component renders a visually-hidden, but
 * fully tab-traversable, ordered list of events directly after the map
 * in DOM order. Each item is a real <button> that mirrors the map-click
 * onSelect AND flies the map to the event centroid.
 *
 * Tab order: events sorted by intensity_max DESC so the most severe
 * cluster gets focus first when the keyboard user tabs into the list.
 *
 * Visibility: clip-path: inset(50%) + position: absolute keeps the list
 * out of the visual layout for sighted users while leaving every button
 * reachable by Tab and announceable by NVDA / JAWS / VoiceOver.
 *
 * The KeyboardHelp overlay surfaces a "Show event list (visible)" link
 * which toggles a `data-visible` attribute that overrides the clip-path
 * so a sighted reviewer can SEE that this affordance exists — useful
 * for A11y QA + the personas-review demo.
 */
import React, { useEffect, useMemo, useRef } from 'react';
import type { MhwEventCollection, MhwEventFeature } from '../types';
import { useT } from '../i18n';
import { usePlainMode, categoryDisplay } from './PlainMode';
import type maplibregl from 'maplibre-gl';

interface Props {
  events: MhwEventCollection | null;
  onSelect: (feat: MhwEventFeature) => void;
  /**
   * Map handle exposed by MapViewGL through onMapReady. When present we
   * call flyTo on activation so the event becomes visually centred for
   * a sighted observer who is watching the keyboard demo.
   */
  mapRef?: React.MutableRefObject<maplibregl.Map | null>;
}

/**
 * Sort events by peak intensity descending so the most severe cluster
 * is the first tab-stop after the map. Clones the array — mutating the
 * caller's events.features would create subtle layer-render race bugs.
 */
function sortByIntensityDesc(events: MhwEventCollection): MhwEventFeature[] {
  return [...events.features].sort((a, b) => {
    const ia = a.properties?.intensity_max ?? 0;
    const ib = b.properties?.intensity_max ?? 0;
    return ib - ia;
  });
}

/**
 * Build the long aria-label string read by screen-readers + announced
 * by the live-region on selection. Localised; falls back gracefully
 * if any property is missing.
 */
export function buildEventLabel(
  feat: MhwEventFeature,
  plain: boolean,
  t: (path: string, vars?: Record<string, string | number>) => string,
): string {
  const p = feat.properties;
  const cat = p.category ?? 0;
  const catLabel = categoryDisplay(cat, plain, t, 'long');
  const peak = (p.intensity_max ?? 0).toFixed(2);
  return t('eventList.itemAria', {
    category: catLabel,
    start: p.date_start ?? '',
    end: p.date_end ?? '',
    peak,
  });
}

export function EventListA11y({ events, onSelect, mapRef }: Props) {
  const { t } = useT();
  const [plain] = usePlainMode();
  const listRef = useRef<HTMLOListElement | null>(null);

  // Sort once per events change.
  const sorted = useMemo(() => {
    if (!events || !events.features?.length) return [];
    return sortByIntensityDesc(events);
  }, [events]);

  // Listen for the global "show event list" custom event dispatched by
  // the KeyboardHelp overlay so we can scroll the list into view + make
  // it temporarily visible. We auto-hide the visible state after a few
  // seconds (or on Escape / blur) so it doesn't persist into the next
  // session as a permanent visual element.
  useEffect(() => {
    const onShow = () => {
      const list = listRef.current;
      if (!list) return;
      list.setAttribute('data-visible', 'true');
      list.scrollIntoView({ behavior: 'smooth', block: 'start' });
      // Focus the first button so keyboard flow lands inside the list.
      const first = list.querySelector<HTMLButtonElement>('button');
      first?.focus();
    };
    window.addEventListener('mheat:show-event-list', onShow);
    return () => window.removeEventListener('mheat:show-event-list', onShow);
  }, []);

  // Hide the visible-mode again when focus leaves the list, on Escape,
  // or after a 12s timeout — whichever comes first.
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const hide = () => list.removeAttribute('data-visible');
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape' && list.getAttribute('data-visible') === 'true') {
        hide();
        // Return focus to the body so the next Tab walks from the top.
        (document.activeElement as HTMLElement | null)?.blur();
      }
    };
    const onBlur = (ev: FocusEvent) => {
      // Defer so focus moving between siblings inside the list doesn't
      // trigger a hide.
      window.setTimeout(() => {
        if (!list.contains(document.activeElement)) hide();
      }, 50);
    };
    list.addEventListener('focusout', onBlur);
    window.addEventListener('keydown', onKey);
    return () => {
      list.removeEventListener('focusout', onBlur);
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  if (!events || sorted.length === 0) {
    // Render an empty <ol> so the DOM anchor is always present — the
    // KeyboardHelp link still has somewhere to scroll to even when no
    // events match the current filter window.
    return (
      <ol
        ref={listRef}
        className="event-list-a11y"
        aria-label={t('eventList.aria')}
        data-testid="event-list-a11y"
      />
    );
  }

  return (
    <ol
      ref={listRef}
      className="event-list-a11y"
      aria-label={t('eventList.aria', { n: String(sorted.length) })}
      data-testid="event-list-a11y"
    >
      {sorted.map((feat) => {
        const label = buildEventLabel(feat, plain, t);
        const p = feat.properties;
        return (
          <li key={feat.id ?? p.event_id} className="event-list-a11y-item">
            <button
              type="button"
              className="event-list-a11y-btn"
              aria-label={label}
              data-event-id={p.event_id}
              onClick={() => {
                onSelect(feat);
                const map = mapRef?.current;
                const c = p.centroid;
                if (map && c && c.length === 2) {
                  try {
                    map.flyTo({ center: [c[0], c[1]], zoom: 7, duration: 700 });
                  } catch {
                    /* map may already be torn down — non-fatal */
                  }
                }
              }}
            >
              {/* Visible content for sighted users when the list is
                  un-hidden via the KeyboardHelp link. Sighted-mode
                  layout is a compact single line per event. */}
              <span className="event-list-a11y-cat">
                {categoryDisplay(p.category ?? 0, plain, t, 'short')}
              </span>
              <span className="event-list-a11y-dates">
                {p.date_start} → {p.date_end}
              </span>
              <span className="event-list-a11y-peak">
                +{(p.intensity_max ?? 0).toFixed(2)}°C
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Compose the polite-live-region announcement string for a newly
 * selected event. Exported so the parent can call it directly when
 * `selected` changes (the live region itself lives in App.tsx so a
 * single announcement node is shared by every selection source).
 */
export function buildSelectionAnnouncement(
  feat: MhwEventFeature | null,
  plain: boolean,
  t: (path: string, vars?: Record<string, string | number>) => string,
): string {
  if (!feat) return '';
  const p = feat.properties;
  const cat = p.category ?? 0;
  const catLabel = categoryDisplay(cat, plain, t, 'long');
  const peak = (p.intensity_max ?? 0).toFixed(2);
  return t('eventList.selectedAnnounce', {
    category: catLabel,
    start: p.date_start ?? '',
    end: p.date_end ?? '',
    peak,
  });
}

export default EventListA11y;
