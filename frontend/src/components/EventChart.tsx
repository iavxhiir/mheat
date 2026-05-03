import React, { useEffect, useRef, useState } from 'react';
import type { MhwEventCollection } from '../types';
import { CATEGORY_COLORS, CATEGORY_LABELS } from './Legend';

interface Props {
  events: MhwEventCollection | null;
}

// Lazy-loaded Plotly reference. We use dynamic import so the main JS bundle
// excludes plotly.js-dist-min entirely (~3 MB). The chunk is fetched the first
// time the chart mounts.
type PlotlyModule = typeof import('plotly.js-dist-min');
let plotlyPromise: Promise<PlotlyModule> | null = null;
function loadPlotly(): Promise<PlotlyModule> {
  if (!plotlyPromise) {
    plotlyPromise = import('plotly.js-dist-min');
  }
  return plotlyPromise;
}

/**
 * Horizontal-bar timeline of detected MHW events. Each bar spans
 * ``date_start`` → ``date_end`` and is coloured by Hobday category.
 * Plotly is lazy-loaded on mount so it doesn't bloat the main bundle.
 */
export function EventChart({ events }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadPlotly().then(() => {
      if (!cancelled) setReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!ready || !ref.current) return;
    loadPlotly().then((Plotly) => {
      const el = ref.current;
      if (!el) return;
      if (!events || events.features.length === 0) {
        Plotly.purge(el);
        return;
      }

      const byCat: Record<
        number,
        { x: number[]; y: string[]; base: string[]; text: string[] }
      > = {};
      events.features.forEach((f, i) => {
        const p = f.properties;
        const startMs = Date.parse(p.date_start);
        const endMs = Date.parse(p.date_end);
        const durMs = Math.max(endMs - startMs, 86400000);
        if (!byCat[p.category]) byCat[p.category] = { x: [], y: [], base: [], text: [] };
        byCat[p.category].x.push(durMs);
        byCat[p.category].y.push(`${i + 1}. ${p.event_id}`);
        byCat[p.category].base.push(p.date_start);
        byCat[p.category].text.push(
          `${p.category_name}<br>${p.date_start} → ${p.date_end}<br>${p.duration_days}d, peak ${p.intensity_max.toFixed(2)}°C`
        );
      });

      const data = Object.keys(byCat)
        .map(Number)
        .sort((a, b) => a - b)
        .map((cat) => ({
          type: 'bar' as const,
          orientation: 'h' as const,
          name: CATEGORY_LABELS[cat],
          x: byCat[cat].x,
          y: byCat[cat].y,
          base: byCat[cat].base,
          text: byCat[cat].text,
          hovertemplate: '%{text}<extra></extra>',
          marker: { color: CATEGORY_COLORS[cat] },
        }));

      const layout = {
        title: { text: 'MHW event timeline', font: { size: 14 } },
        barmode: 'overlay',
        margin: { t: 30, l: 130, r: 10, b: 40 },
        height: 220,
        xaxis: { type: 'date', title: '', showgrid: true },
        yaxis: { automargin: true, tickfont: { size: 10 } },
        showlegend: true,
        legend: { orientation: 'h', y: -0.3 },
        paper_bgcolor: '#0e1b2b',
        plot_bgcolor: '#0e1b2b',
        font: { color: '#e5edf7' },
      };

      Plotly.react(el, data, layout, {
        displayModeBar: false,
        responsive: true,
      });
    });
  }, [ready, events]);

  return (
    <div
      className="event-chart"
      ref={ref}
      role="img"
      aria-label="Timeline of detected marine heatwave events, coloured by Hobday category"
    />
  );
}
