import React, { useEffect, useRef, useState } from 'react';
import type { MhwEventFeature } from '../types';
import { CATEGORY_COLORS } from './Legend';
import { fetchEventSeries, type EventSeries } from '../api';
import { useT } from '../i18n';
import { usePlainMode, categoryDisplay } from './PlainMode';

interface Props {
  event: MhwEventFeature | null;
}

type PlotlyModule = typeof import('plotly.js-dist-min');
let plotlyPromise: Promise<PlotlyModule> | null = null;
function loadPlotly(): Promise<PlotlyModule> {
  if (!plotlyPromise) plotlyPromise = import('plotly.js-dist-min');
  return plotlyPromise;
}

export function EventPanel({ event }: Props) {
  const { t } = useT();
  const [plain] = usePlainMode();
  const [series, setSeries] = useState<EventSeries | null>(null);
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const sparkRef = useRef<HTMLDivElement | null>(null);

  // Fetch the SST / climatology / threshold series when a new event is picked.
  useEffect(() => {
    setSeries(null);
    setSeriesError(null);
    if (!event) return;
    const { event_id, centroid, date_start, date_end } = event.properties;
    const lon = centroid[0];
    const lat = centroid[1];
    let cancelled = false;
    fetchEventSeries(event_id, lon, lat, date_start, date_end)
      .then((s) => {
        if (!cancelled) setSeries(s);
      })
      .catch((e) => {
        if (!cancelled) setSeriesError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [event?.properties.event_id]);

  // Render the Plotly sparkline.
  useEffect(() => {
    const el = sparkRef.current;
    if (!el || !series || !event) return;
    loadPlotly().then((Plotly) => {
      const traces = [
        {
          x: series.time,
          y: series.climatology,
          type: 'scatter',
          mode: 'lines',
          name: 'Climatology',
          line: { color: '#9aa7b3', width: 1.5 },
        },
        {
          x: series.time,
          y: series.threshold,
          type: 'scatter',
          mode: 'lines',
          name: '90p threshold',
          line: { color: '#e63946', width: 1.5, dash: 'dash' },
        },
        {
          x: series.time,
          y: series.sst,
          type: 'scatter',
          mode: 'lines',
          name: 'SST',
          line: { color: '#48cae4', width: 2 },
        },
      ];
      const layout = {
        margin: { t: 10, l: 36, r: 8, b: 28 },
        height: 180,
        xaxis: { type: 'date', showgrid: false, tickfont: { size: 9 } },
        yaxis: { title: '', tickfont: { size: 9 } },
        paper_bgcolor: '#0e1b2b',
        plot_bgcolor: '#0e1b2b',
        font: { color: '#e5edf7', size: 10 },
        showlegend: true,
        legend: { orientation: 'h', y: -0.25, font: { size: 9 } },
        shapes: [
          {
            type: 'rect',
            xref: 'x',
            yref: 'paper',
            x0: event.properties.date_start,
            x1: event.properties.date_end,
            y0: 0,
            y1: 1,
            fillcolor: '#ff9f1c',
            opacity: 0.15,
            line: { width: 0 },
          },
        ],
      };
      Plotly.react(el, traces, layout, { displayModeBar: false, responsive: true });
    });
  }, [series, event]);

  if (!event) {
    return (
      <div className="event-panel empty">
        <div className="event-panel-title">{t('eventPanel.title')}</div>
        <div className="empty-msg">{t('eventPanel.emptyHint')}</div>
      </div>
    );
  }
  const p = event.properties;
  return (
    <div className="event-panel">
      <div className="event-panel-title">{t('eventPanel.title')}</div>
      <div className="event-id">{p.event_id}</div>
      <div className="event-cat" style={{ background: CATEGORY_COLORS[p.category] }}>
        {plain
          ? categoryDisplay(p.category, true, t, 'long')
          : `${t('eventPanel.category')} ${p.category_name}`}
      </div>
      <table className="event-table">
        <tbody>
          <tr><th>{t('eventPanel.rowStart')}</th><td>{p.date_start}</td></tr>
          <tr><th>{t('eventPanel.rowEnd')}</th><td>{p.date_end}</td></tr>
          <tr><th>{t('eventPanel.rowPeak')}</th><td>{p.date_peak}</td></tr>
          <tr><th>{t('eventPanel.rowDuration')}</th><td>{t('eventPanel.durationDays', { n: p.duration_days })}</td></tr>
          <tr><th>{t('eventPanel.rowPeakIntensity')}</th><td>{p.intensity_max.toFixed(2)} &deg;C</td></tr>
          <tr><th>{t('eventPanel.rowMeanIntensity')}</th><td>{p.intensity_mean.toFixed(2)} &deg;C</td></tr>
          <tr><th>{t('eventPanel.rowCumulative')}</th><td>{p.intensity_cumulative.toFixed(1)} &deg;C&middot;days</td></tr>
          <tr><th>{t('eventPanel.rowCentroid')}</th><td>{p.centroid[1].toFixed(2)}&deg;N, {p.centroid[0].toFixed(2)}&deg;E</td></tr>
          <tr><th>{t('eventPanel.rowPixels')}</th><td>{p.n_pixels}</td></tr>
        </tbody>
      </table>
      {p.impact && (
        <div className="impact-block">
          <div className="impact-title">{t('eventPanel.impactTitle')}</div>
          <table className="event-table">
            <tbody>
              <tr><th>{t('eventPanel.impactAqua')}</th><td>{p.impact.n_aquaculture_sites}</td></tr>
              <tr><th>{t('eventPanel.impactMpa')}</th><td>{p.impact.mpa_area_km2.toFixed(1)} km&sup2;</td></tr>
              <tr><th>{t('eventPanel.impactSeagrass')}</th><td>{p.impact.seagrass_area_km2.toFixed(1)} km&sup2;</td></tr>
            </tbody>
          </table>
        </div>
      )}
      <div className="impact-title" style={{ marginTop: 10 }}>{t('eventPanel.sparklineTitle')}</div>
      {seriesError && (
        <div className="event-sparkline-empty">
          {t('eventPanel.sparklineError', { err: seriesError })}
        </div>
      )}
      {!series && !seriesError && (
        <div className="event-sparkline-empty">{t('eventPanel.sparklineLoading')}</div>
      )}
      <div
        className="event-sparkline"
        ref={sparkRef}
        role="img"
        aria-label={`SST, climatology and 90th-percentile threshold around ${p.event_id}. Orange band marks event duration.`}
      />
    </div>
  );
}
