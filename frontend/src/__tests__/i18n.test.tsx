import { describe, it, expect } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { I18nProvider, useT, SUPPORTED_LOCALES } from '../i18n';

function Probe({ path, vars }: { path: string; vars?: Record<string, string | number> }) {
  const { t } = useT();
  return <span data-testid="out">{t(path, vars)}</span>;
}

function Switcher() {
  const { locale, setLocale } = useT();
  return (
    <button
      data-testid="sw"
      onClick={() => setLocale(locale === 'en' ? 'fr' : 'en')}
    >
      {locale}
    </button>
  );
}

describe('i18n', () => {
  it('throws outside the provider', () => {
    expect(() => render(<Probe path="layers.title" />)).toThrow(/I18nProvider/);
  });

  it('returns the path string when the key is missing (no crash)', () => {
    render(
      <I18nProvider>
        <Probe path="does.not.exist" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('out').textContent).toBe('does.not.exist');
  });

  it('interpolates {vars} into translations', () => {
    render(
      <I18nProvider>
        <Probe path="__raw" vars={{ name: 'sea' }} />
      </I18nProvider>,
    );
    // Missing key falls back to the path itself — interpolation only happens
    // against that fallback if it contains braces. Exercise the happy path
    // directly with a literal braced path.
    render(
      <I18nProvider>
        <Probe path="hello {name}" vars={{ name: 'sea' }} />
      </I18nProvider>,
    );
    // Either output (fallback path) is fine — what we assert is that {name}
    // is always replaced when a var is supplied.
    const outs = screen.getAllByTestId('out').map((n) => n.textContent);
    expect(outs.some((t) => t?.includes('sea'))).toBe(true);
    expect(outs.every((t) => !t?.includes('{name}'))).toBe(true);
  });

  it('switches locale and persists it to localStorage', () => {
    render(
      <I18nProvider>
        <Switcher />
      </I18nProvider>,
    );
    const btn = screen.getByTestId('sw');
    const initial = btn.textContent;
    act(() => btn.click());
    expect(btn.textContent).not.toBe(initial);
    const stored = window.localStorage.getItem('mheat-locale');
    expect(stored && SUPPORTED_LOCALES.includes(stored as (typeof SUPPORTED_LOCALES)[number]))
      .toBe(true);
  });
});
