/**
 * Lightweight custom i18n for MHEAT.
 *
 * Loads JSON dictionaries under src/locales/, auto-detects the browser
 * language (EN fallback), and exposes a `useT()` hook that returns a
 * `t(path, vars?)` function with dotted-path lookup.
 */
import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import en from './locales/en.json';
import fr from './locales/fr.json';
import it from './locales/it.json';

export type Locale = 'en' | 'fr' | 'it';

type Dict = Record<string, unknown>;

const DICTS: Record<Locale, Dict> = {
  en: en as Dict,
  fr: fr as Dict,
  it: it as Dict,
};

const SUPPORTED: Locale[] = ['en', 'fr', 'it'];
const STORAGE_KEY = 'mheat-locale';

function detectBrowserLocale(): Locale {
  try {
    const stored = window.localStorage?.getItem(STORAGE_KEY);
    if (stored && (SUPPORTED as string[]).includes(stored)) return stored as Locale;
  } catch {
    /* noop */
  }
  const nav = navigator.language?.slice(0, 2).toLowerCase();
  if (nav && (SUPPORTED as string[]).includes(nav)) return nav as Locale;
  return 'en';
}

function lookup(dict: Dict, path: string): string {
  const parts = path.split('.');
  let cur: unknown = dict;
  for (const p of parts) {
    if (cur && typeof cur === 'object' && p in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return path;
    }
  }
  return typeof cur === 'string' ? cur : path;
}

function interpolate(msg: string, vars?: Record<string, string | number>): string {
  if (!vars) return msg;
  return msg.replace(/\{(\w+)\}/g, (_, k) => (vars[k] !== undefined ? String(vars[k]) : `{${k}}`));
}

interface I18nCtx {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (path: string, vars?: Record<string, string | number>) => string;
}

const Ctx = createContext<I18nCtx | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(detectBrowserLocale());

  const setLocale = (l: Locale) => {
    setLocaleState(l);
    try {
      window.localStorage?.setItem(STORAGE_KEY, l);
    } catch {
      /* noop */
    }
  };

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo<I18nCtx>(
    () => ({
      locale,
      setLocale,
      t: (path: string, vars?: Record<string, string | number>) =>
        interpolate(lookup(DICTS[locale], path), vars),
    }),
    [locale]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useT(): I18nCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useT must be used inside <I18nProvider>');
  return c;
}

/**
 * Variant of {@link useT} that does NOT throw when called outside an
 * I18nProvider — falls back to the English dictionary. Used by leaf
 * widgets (e.g. HeroStat) that ship in tests rendered without the
 * provider; keeps the existing test surface intact while the widget
 * still gets translated copy when run inside the real app.
 */
export function useTOptional(): I18nCtx {
  const c = useContext(Ctx);
  if (c) return c;
  return {
    locale: 'en',
    setLocale: () => undefined,
    t: (path: string, vars?: Record<string, string | number>) =>
      interpolate(lookup(DICTS.en, path), vars),
  };
}

export const SUPPORTED_LOCALES: Locale[] = SUPPORTED;
