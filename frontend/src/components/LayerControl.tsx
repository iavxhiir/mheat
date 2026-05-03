import React, { useState, useRef, useEffect } from 'react';
import type { LayerToggles } from '../types';
import { useT } from '../i18n';

interface Props {
  value: LayerToggles;
  onChange: (next: LayerToggles) => void;
  /** Per-layer in-flight indicator. Truthy entries render a tiny spinner
      next to the layer name while the lazy fetch is resolving (UI cross-cut
      #4 — sectoral overlays no longer load eagerly at boot). */
  loading?: Partial<Record<keyof LayerToggles, boolean>>;
}

/* Per-layer "what is this" copy. Surfaced on click of the ⓘ icon next to
   each layer's checkbox. Two paragraphs: WHAT (definition + source) and
   WHY (relevance to MHWs / decision-support). The text is intentionally
   long-form because reviewers / first-time users won't know e.g. that
   Posidonia is endemic to the Med, or that Natura 2000 is the EU's
   flagship biodiversity protection network. */
const LAYER_INFO: Record<keyof LayerToggles, { title: string; what: string; why: string }> = {
  anomaly: {
    title: 'SST anomaly raster',
    what: 'Sea-surface-temperature anomaly = today\'s SST minus the 1993-2019 day-of-year mean from Copernicus Med MFC. Pixel resolution ~7 km. Red = warmer than seasonal climatology, blue = colder.',
    why: 'The base diagnostic for marine heatwave detection. Hobday 2016 categorises SST anomalies above the 90th-percentile threshold into 5 severity classes (I-V). Reviewers and analysts can scrub the time slider to watch a heatwave grow, peak, and decay.',
  },
  aquaculture: {
    title: 'Aquaculture sites (EMODnet finfish)',
    what: 'Sea-cage fish farms — mainly European sea bass, gilthead sea bream, and Atlantic bluefin tuna in floating net pens 0.5-5 km offshore. Source: EMODnet Human Activities, layer "emodnet:finfish" (~344 sites in the Mediterranean). Each marker = one farm.',
    why: 'Caged fish cannot migrate to cooler water during a marine heatwave — they are trapped at surface temperatures and suffer mass mortality. Greek + Italian farms lost an estimated €200-400 M during the 2022 W Med MHW. Insurance underwriters, fisheries authorities, and EU CFP responders need to know which sites were inside which event for damage assessment + emergency funding.',
  },
  mpa: {
    title: 'Marine Protected Areas (Natura 2000)',
    what: 'EU-designated sea zones where human activity is restricted to protect ecosystems. Source: EEA Natura 2000 ArcGIS REST API, layer 2 (combined Habitats + Birds Directive sites). ~500 polygon sites in the Mediterranean. Each polygon carries SITECODE, SITENAME, member-state code, area in hectares.',
    why: 'These are the EU\'s flagship biodiversity protection sites under the Habitats Directive (92/43/EEC) and Birds Directive (2009/147/EC). An MHW inside an MPA is a direct Marine Strategy Framework Directive Descriptor 7 indicator failure. Site managers + DG ENV need to know "did my reserve get cooked, and how badly" — that drives emergency restoration funding and adaptive-management decisions.',
  },
  seagrass: {
    title: 'Seagrass meadows (EMODnet seabed habitats)',
    what: 'Underwater flowering plants forming dense meadows on the seafloor. In the Mediterranean, mainly Posidonia oceanica (endemic — found only in the Med) plus Cymodocea nodosa. Source: EMODnet Seabed Habitats, layer "emodnet_open:seagrass_eov_poly_2025" (~2 000 polygons, the most recent comprehensive Med-wide inventory).',
    why: 'Posidonia stores ~35× more carbon per hectare than tropical rainforest — it is the Mediterranean\'s major carbon sink. It also nurses fish (sea bream, mullet, pipefish), stabilises sand, and lives at depths 1-40 m where MHW warming is felt directly. Mass die-offs documented during 2003 + 2022 MHWs (Marbà & Duarte, Global Change Biology 2010). Posidonia regrows ~1 cm/year, so a single MHW kills meadows that took centuries to form.',
  },
};

export function LayerControl({ value, onChange, loading }: Props) {
  const { t } = useT();
  const LABELS: Record<keyof LayerToggles, string> = {
    anomaly: t('layers.anomaly'),
    aquaculture: t('layers.aquaculture'),
    mpa: t('layers.mpa'),
    seagrass: t('layers.seagrass'),
  };
  const toggle = (k: keyof LayerToggles) => onChange({ ...value, [k]: !value[k] });

  // Track which layer's info popover is open (only one at a time).
  const [openInfo, setOpenInfo] = useState<keyof LayerToggles | null>(null);
  const wrapRef = useRef<HTMLFieldSetElement | null>(null);
  // Close on click-outside or Escape.
  useEffect(() => {
    if (!openInfo) return;
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpenInfo(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpenInfo(null);
    };
    window.addEventListener('mousedown', onClick);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onClick);
      window.removeEventListener('keydown', onKey);
    };
  }, [openInfo]);

  return (
    <fieldset className="layer-control" ref={wrapRef}>
      <legend className="layer-title">{t('layers.title')}</legend>
      {(Object.keys(LABELS) as (keyof LayerToggles)[]).map((k) => {
        const info = LAYER_INFO[k];
        return (
          <div key={k} className="layer-row-wrap">
            <label className="layer-row">
              <input
                type="checkbox"
                checked={value[k]}
                onChange={() => toggle(k)}
                aria-label={`Toggle ${LABELS[k]} layer`}
              />
              <span>{LABELS[k]}</span>
              {loading?.[k] && (
                <span
                  className="layer-loading"
                  role="status"
                  aria-live="polite"
                  aria-label={t('layers.loadingAria')}
                  title={t('layers.loading')}
                  data-testid={`layer-loading-${k}`}
                >
                  <span className="layer-loading-spinner" aria-hidden="true" />
                  <span className="layer-loading-text">{t('layers.loading')}</span>
                </span>
              )}
              <button
                type="button"
                className="layer-info-btn"
                aria-label={`What is ${LABELS[k]}?`}
                aria-expanded={openInfo === k}
                title={`What is ${LABELS[k]}?`}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setOpenInfo(openInfo === k ? null : k);
                }}
              >
                ⓘ
              </button>
            </label>
            {openInfo === k && (
              <div className="layer-info-pop" role="dialog" aria-label={info.title}>
                <div className="layer-info-title">{info.title}</div>
                <div className="layer-info-section">
                  <span className="layer-info-tag">What</span>
                  <p>{info.what}</p>
                </div>
                <div className="layer-info-section">
                  <span className="layer-info-tag">Why it matters</span>
                  <p>{info.why}</p>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}
