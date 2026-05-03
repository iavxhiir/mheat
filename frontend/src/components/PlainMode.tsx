/**
 * Plain English / Technical wording toggle + Colour-blind safe palette toggle.
 *
 * Persona-9 (tour operator) and persona-16 (educator) hit the dashboard
 * without any background in MHW classification — "Cat-IV +6.6 °C" reads
 * as scientific noise. Persona-19 (non-English speaker) needs the same
 * affordance in their language. Persona-23 (deuteranopia, ~6% of males)
 * cannot distinguish the default Hobday red-amber ramp.
 *
 * When Plain mode is ON, category labels become impact-language strings
 * ("Severe", "Extreme — mass mortality risk") instead of "I Moderate"
 * → "V Super-Extreme". Persisted in localStorage as `mheat-plain-mode`,
 * read on mount, written on toggle.
 *
 * When CB-safe is ON (UI-cross-cut #5), the Hobday red-amber ramp is
 * swapped for the Okabe-Ito categorical palette, applied via
 * `--cat-1`..`--cat-5` overrides on `<html>` plus a
 * `data-cb-safe="true"` attribute that triggers shape glyphs next to
 * category chips. Persisted in `localStorage['mheat-cb-safe']`.
 *
 * Affects:
 *  - Legend (per-category bars + glyph)
 *  - EventPanel (category badge)
 *  - MapViewGL hover tooltip (Cat-X header line + ramp via CSS var)
 *  - HeroStat chip labels
 *
 * Centralised here so all four call sites read from one source of truth.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useT } from '../i18n';

const STORAGE_KEY = 'mheat-plain-mode';
const CB_STORAGE_KEY = 'mheat-cb-safe';

function readStored(): boolean {
  try {
    const v = window.localStorage?.getItem(STORAGE_KEY);
    return v === '1' || v === 'true';
  } catch {
    return false;
  }
}

function writeStored(plain: boolean): void {
  try {
    window.localStorage?.setItem(STORAGE_KEY, plain ? '1' : '0');
  } catch {
    /* noop */
  }
}

function readCbStored(): boolean {
  try {
    const v = window.localStorage?.getItem(CB_STORAGE_KEY);
    return v === '1' || v === 'true';
  } catch {
    return false;
  }
}

function writeCbStored(on: boolean): void {
  try {
    window.localStorage?.setItem(CB_STORAGE_KEY, on ? '1' : '0');
  } catch {
    /* noop */
  }
}

export type SetPlainMode = (plain: boolean) => void;
export type SetCbSafe = (on: boolean) => void;

/** Okabe-Ito derived ramp — colour-blind-safe (deuteranopia, protanopia,
 *  tritanopia all distinguish these five luminance/hue combinations). */
export const CB_SAFE_PALETTE: Record<number, string> = {
  1: '#E69F00', // orange
  2: '#56B4E9', // sky blue
  3: '#009E73', // bluish green
  4: '#F0E442', // yellow
  5: '#D55E00', // vermilion
};

/** Shape glyphs — second visual encoding for CB-safe mode. */
export const CATEGORY_GLYPHS: Record<number, string> = {
  1: '●',
  2: '▲',
  3: '■',
  4: '◆',
  5: '★',
};

/** Small subscriber-set so multiple consumers stay in sync without
    needing a context provider — keeps the change surface minimal. */
const subscribers = new Set<(p: boolean) => void>();
let currentValue: boolean | null = null;

function getCurrent(): boolean {
  if (currentValue === null) currentValue = readStored();
  return currentValue;
}

function setCurrent(v: boolean): void {
  currentValue = v;
  writeStored(v);
  for (const s of subscribers) s(v);
}

/** Imperative setter for callers outside the React tree (e.g. URL-param
    bootstrap in App.tsx that needs to override the localStorage default
    before any consumer mounts). Equivalent to the setter returned by
    {@link usePlainMode} but callable from module scope. */
export function setPlainMode(v: boolean): void {
  setCurrent(v);
}

const cbSubscribers = new Set<(on: boolean) => void>();
let cbCurrentValue: boolean | null = null;

function getCbCurrent(): boolean {
  if (cbCurrentValue === null) cbCurrentValue = readCbStored();
  return cbCurrentValue;
}

function applyCbDom(on: boolean): void {
  try {
    const html = (typeof document !== 'undefined' ? document.documentElement : null);
    if (!html) return;
    if (on) {
      html.setAttribute('data-cb-safe', 'true');
      // Override the per-category CSS custom-properties globally so
      // every consumer (legend swatches, chips, etc.) picks up the
      // colour-blind ramp without explicit prop drilling.
      for (const cat of [1, 2, 3, 4, 5] as const) {
        html.style.setProperty(`--cat-${cat}`, CB_SAFE_PALETTE[cat]);
      }
    } else {
      html.removeAttribute('data-cb-safe');
      for (const cat of [1, 2, 3, 4, 5] as const) {
        html.style.removeProperty(`--cat-${cat}`);
      }
    }
  } catch {
    /* noop — non-DOM environments */
  }
}

function setCbCurrent(on: boolean): void {
  cbCurrentValue = on;
  writeCbStored(on);
  applyCbDom(on);
  for (const s of cbSubscribers) s(on);
}

/** Imperative setter for callers outside the React tree, mirroring
    {@link setPlainMode}. */
export function setCbSafe(on: boolean): void {
  setCbCurrent(on);
}

export function usePlainMode(): [boolean, SetPlainMode] {
  const [plain, setPlain] = useState<boolean>(getCurrent);
  useEffect(() => {
    const sub = (v: boolean) => setPlain(v);
    subscribers.add(sub);
    // In case another mount changed it before we subscribed.
    if (currentValue !== null && currentValue !== plain) setPlain(currentValue);
    return () => {
      subscribers.delete(sub);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const set = useCallback<SetPlainMode>((v) => setCurrent(v), []);
  return [plain, set];
}

export function useCbSafe(): [boolean, SetCbSafe] {
  const [on, setOn] = useState<boolean>(getCbCurrent);
  useEffect(() => {
    // Re-apply DOM attrs on mount in case they were cleared (HMR,
    // tests that reset DOM, etc.) — idempotent so safe to repeat.
    if (cbCurrentValue) applyCbDom(true);
    const sub = (v: boolean) => setOn(v);
    cbSubscribers.add(sub);
    if (cbCurrentValue !== null && cbCurrentValue !== on) setOn(cbCurrentValue);
    return () => {
      cbSubscribers.delete(sub);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const set = useCallback<SetCbSafe>((v) => setCbCurrent(v), []);
  return [on, set];
}

/** Returns the shape glyph for a category when CB-safe mode is on, or
    an empty string otherwise — kept as a one-call helper so consumers
    don't need to import both `useCbSafe` and `CATEGORY_GLYPHS`. */
export function categoryGlyph(cat: number, cbSafe: boolean): string {
  if (!cbSafe) return '';
  if (cat < 1 || cat > 5) return '';
  return CATEGORY_GLYPHS[cat];
}

/**
 * Resolve the displayable category label given the current plain-mode
 * flag and the i18n `t()` function. Used by every consumer so wording
 * stays consistent across the UI.
 *
 * - `kind: 'long'` returns e.g. "Mild warming" / "I Moderate".
 * - `kind: 'short'` returns the chip-friendly form ("Mild" / "I").
 */
export function categoryDisplay(
  cat: number,
  plain: boolean,
  t: (path: string, vars?: Record<string, string | number>) => string,
  kind: 'long' | 'short' = 'long',
): string {
  if (cat < 1 || cat > 5) return '';
  if (plain) {
    return kind === 'short'
      ? t(`category.plainShort${cat}`)
      : t(`category.plain${cat}`);
  }
  const roman = ['', 'I', 'II', 'III', 'IV', 'V'][cat];
  if (kind === 'short') return roman;
  // Technical long: "I Moderate" / "II Strong" / etc. — matches the
  // historic CATEGORY_LABELS used by the legend and tooltip.
  return `${roman} ${t(`category.name${cat}`)}`;
}

interface ToggleProps {
  className?: string;
}

export function PlainModeToggle({ className }: ToggleProps) {
  const { t } = useT();
  const [plain, setPlain] = usePlainMode();
  const onToggle = () => setPlain(!plain);
  return (
    <fieldset className={`plain-mode-toggle${className ? ` ${className}` : ''}`}>
      <legend className="layer-title">{t('plainMode.title')}</legend>
      <label className="plain-mode-row">
        <input
          type="checkbox"
          checked={plain}
          onChange={onToggle}
          aria-label={t('plainMode.ariaLabel')}
          data-testid="plain-mode-checkbox"
        />
        <span className="plain-mode-label">{t('plainMode.label')}</span>
        <span className="plain-mode-side" aria-hidden="true">
          {plain ? t('plainMode.plainShort') : t('plainMode.technicalShort')}
        </span>
      </label>
      <div className="plain-mode-hint" role="note">
        {plain ? t('plainMode.plainDescription') : t('plainMode.technicalDescription')}
      </div>
    </fieldset>
  );
}

/**
 * Colour-blind safe palette toggle — sits next to {@link PlainModeToggle}.
 *
 * When ON, swaps the Hobday red-amber category ramp for an Okabe-Ito
 * categorical palette via CSS custom-property overrides on `<html>`,
 * and adds shape glyphs (●▲■◆★) next to category labels for a
 * second non-colour visual encoding (deuteranopes can't tell the
 * default ramp's red, deep red, and dark red apart).
 */
export function CbSafeToggle({ className }: ToggleProps) {
  const { t } = useT();
  const [on, setOn] = useCbSafe();
  const onToggle = () => setOn(!on);
  return (
    <fieldset className={`plain-mode-toggle cb-safe-toggle${className ? ` ${className}` : ''}`}>
      <legend className="layer-title">{t('cbSafe.title')}</legend>
      <label className="plain-mode-row">
        <input
          type="checkbox"
          checked={on}
          onChange={onToggle}
          aria-label={t('cbSafe.ariaLabel')}
          data-testid="cb-safe-checkbox"
        />
        <span className="plain-mode-label">{t('cbSafe.label')}</span>
        <span className="plain-mode-side" aria-hidden="true">
          {on ? t('cbSafe.onShort') : t('cbSafe.offShort')}
        </span>
      </label>
      <div className="plain-mode-hint" role="note">
        {on ? t('cbSafe.onDescription') : t('cbSafe.offDescription')}
      </div>
    </fieldset>
  );
}
