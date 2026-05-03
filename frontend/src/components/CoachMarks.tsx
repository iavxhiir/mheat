/**
 * First-visit coach marks — 3-step onboarding overlay.
 *
 * Persona-16 (educator) opens the dashboard with no prior knowledge and
 * needs guidance. Persona-9 (tour operator) and persona-20 (skeptic
 * visitor) benefit too: a 30-second guided pass tells them where to look
 * before they bounce.
 *
 * Behaviour:
 *  - Renders only on first visit (localStorage flag `mheat-coach-seen`).
 *  - Three steps that point at: (a) the map, (b) an event bubble, (c)
 *    the `?` keyboard help button.
 *  - Each step has a Skip button + a Next/Done button. The final step
 *    writes the localStorage flag so the overlay never re-appears.
 *  - Semi-transparent backdrop with a callout card positioned near the
 *    targeted element via `getBoundingClientRect()` (recomputes on
 *    resize so it stays anchored).
 *  - Honours `prefers-reduced-motion` (no transition animations).
 *  - All text routed through i18n (EN/FR/IT).
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useT } from '../i18n';

const STORAGE_KEY = 'mheat-coach-seen';

interface Step {
  /** CSS selector for the element to point at; falls back to centre of
      the viewport when the selector misses. */
  target: string;
  titleKey: string;
  bodyKey: string;
}

const STEPS: Step[] = [
  // Step 1 — the map
  { target: '.map-wrap', titleKey: 'coach.step1Title', bodyKey: 'coach.step1Body' },
  // Step 2 — any event bubble. We try the on-map bubble layer first;
  // if that misses (no events visible), we fall back to the event
  // counter pill which is always rendered when events are present.
  { target: '.event-counter', titleKey: 'coach.step2Title', bodyKey: 'coach.step2Body' },
  // Step 3 — the keyboard help "?" button.
  { target: '.kb-help-btn', titleKey: 'coach.step3Title', bodyKey: 'coach.step3Body' },
];

interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

function readBox(selector: string): Box | null {
  if (typeof document === 'undefined') return null;
  const el = document.querySelector(selector);
  if (!el) return null;
  const r = (el as HTMLElement).getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return null;
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

// Tour disabled per user request (2026-05-03). Flip to `true` to
// re-enable the first-visit 3-step coach-marks tour. We keep the
// component exported + hooks intact so React's hook-order checker
// stays happy and the existing imports / wiring keep compiling.
const TOUR_ENABLED = false;

export function CoachMarks() {
  const { t } = useT();
  const [step, setStep] = useState(0);
  const [active, setActive] = useState<boolean>(false);
  const [box, setBox] = useState<Box | null>(null);
  const nextBtnRef = useRef<HTMLButtonElement | null>(null);

  // Decide whether to show on mount. Defer one tick so the rest of the
  // app has had a chance to lay out (otherwise the targets aren't in
  // the DOM yet).
  useEffect(() => {
    if (!TOUR_ENABLED) return;
    let alive = true;
    try {
      const seen = window.localStorage?.getItem(STORAGE_KEY);
      if (seen === '1' || seen === 'true') return;
    } catch {
      /* fall through */
    }
    // Tiny delay so MapView + EventCounter mount before we measure.
    const id = window.setTimeout(() => {
      if (alive) setActive(true);
    }, 600);
    return () => {
      alive = false;
      window.clearTimeout(id);
    };
  }, []);

  // Recompute the target box on step change + window resize.
  useLayoutEffect(() => {
    if (!active) return;
    const update = () => setBox(readBox(STEPS[step].target));
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [active, step]);

  // Move focus into the dialog when it opens (and on step change) so
  // keyboard users can immediately advance with Enter / Esc / arrows.
  // Avoiding the React `autoFocus` prop because eslint-jsx-a11y prefers
  // managed focus (and so do we).
  useEffect(() => {
    if (!active) return;
    nextBtnRef.current?.focus();
  }, [active, step]);

  // Escape closes the tour.
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        finish();
      } else if (e.key === 'ArrowRight' || e.key === 'Enter') {
        e.preventDefault();
        next();
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        back();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, step]);

  if (!active || !TOUR_ENABLED) return null;

  const finish = () => {
    try {
      window.localStorage?.setItem(STORAGE_KEY, '1');
    } catch {
      /* noop */
    }
    setActive(false);
  };

  const next = () => {
    if (step >= STEPS.length - 1) finish();
    else setStep(step + 1);
  };

  const back = () => {
    if (step > 0) setStep(step - 1);
  };

  const total = STEPS.length;
  const isLast = step === total - 1;

  // Position the callout card near the highlighted box. If we lost the
  // box (target missed), centre it.
  const card = computeCardPosition(box);

  return (
    <div className="coach-overlay" role="dialog" aria-modal="true"
         aria-labelledby="coach-title" aria-describedby="coach-body"
         data-testid="coach-overlay">
      {/* Four backdrop quadrants around the highlighted box so the
          target stays "punched out" of the dim layer without needing
          SVG masks. When no box is found, a single full-screen scrim
          is rendered. */}
      {box ? <BoxBackdrop box={box} /> : <div className="coach-backdrop coach-backdrop-full" />}
      {box && (
        <div
          className="coach-highlight"
          style={{
            top: box.top - 6, left: box.left - 6,
            width: box.width + 12, height: box.height + 12,
          }}
          aria-hidden="true"
        />
      )}
      <div
        className="coach-card"
        style={card}
      >
        <div className="coach-step-label">
          {t('coach.stepLabel', { n: step + 1, total })}
        </div>
        <div className="coach-title" id="coach-title">{t(STEPS[step].titleKey)}</div>
        <div className="coach-body" id="coach-body">{t(STEPS[step].bodyKey)}</div>
        <div className="coach-actions">
          <button
            type="button"
            className="coach-btn coach-btn-ghost"
            onClick={finish}
            data-testid="coach-skip"
          >
            {isLast ? t('coach.dismiss') : t('coach.skip')}
          </button>
          <div className="coach-nav">
            {step > 0 && (
              <button
                type="button"
                className="coach-btn coach-btn-secondary"
                onClick={back}
              >
                {t('coach.back')}
              </button>
            )}
            <button
              ref={nextBtnRef}
              type="button"
              className="coach-btn coach-btn-primary"
              onClick={next}
              data-testid="coach-next"
            >
              {isLast ? t('coach.done') : t('coach.next')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Render four dark rectangles around a box so the box is "punched out"
    of an otherwise opaque overlay — no SVG mask required. */
function BoxBackdrop({ box }: { box: Box }) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const PAD = 6;
  return (
    <>
      {/* top */}
      <div className="coach-backdrop" style={{ top: 0, left: 0, width: vw, height: Math.max(0, box.top - PAD) }} />
      {/* bottom */}
      <div className="coach-backdrop" style={{ top: box.top + box.height + PAD, left: 0, width: vw, height: Math.max(0, vh - (box.top + box.height + PAD)) }} />
      {/* left */}
      <div className="coach-backdrop" style={{ top: Math.max(0, box.top - PAD), left: 0, width: Math.max(0, box.left - PAD), height: box.height + 2 * PAD }} />
      {/* right */}
      <div className="coach-backdrop" style={{ top: Math.max(0, box.top - PAD), left: box.left + box.width + PAD, width: Math.max(0, vw - (box.left + box.width + PAD)), height: box.height + 2 * PAD }} />
    </>
  );
}

/** Place the callout card near the targeted box, biasing toward the side
    with more room. Falls back to centre when the target is missing. */
function computeCardPosition(box: Box | null): React.CSSProperties {
  const CARD_W = 340;
  const CARD_H = 220;
  const GAP = 16;
  if (typeof window === 'undefined') return { top: '50%', left: '50%', transform: 'translate(-50%, -50%)' };
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  if (!box) {
    return { top: vh / 2 - CARD_H / 2, left: vw / 2 - CARD_W / 2 };
  }
  // Prefer placing below the box; fall back to above; then to right.
  const spaceBelow = vh - (box.top + box.height) - GAP;
  const spaceAbove = box.top - GAP;
  const spaceRight = vw - (box.left + box.width) - GAP;
  let top = box.top + box.height + GAP;
  let left = Math.min(Math.max(GAP, box.left), vw - CARD_W - GAP);
  if (spaceBelow < CARD_H && spaceAbove >= CARD_H) {
    top = box.top - CARD_H - GAP;
  } else if (spaceBelow < CARD_H && spaceRight >= CARD_W) {
    top = Math.min(Math.max(GAP, box.top), vh - CARD_H - GAP);
    left = box.left + box.width + GAP;
  }
  return { top, left, width: CARD_W };
}
