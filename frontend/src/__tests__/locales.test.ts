/**
 * Locale-dictionary completeness tests.
 *
 * For every key in the canonical English dictionary, assert the FR and IT
 * dictionaries expose the same key. Reads the files from disk to avoid
 * JSON-module-default import quirks across vitest / Vite versions.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

type Dict = Record<string, unknown>;

const HERE = dirname(fileURLToPath(import.meta.url));

function load(locale: 'en' | 'fr' | 'it'): Dict {
  return JSON.parse(
    readFileSync(resolve(HERE, '..', 'locales', `${locale}.json`), 'utf-8'),
  ) as Dict;
}

function flatten(d: Dict, prefix = ''): string[] {
  const out: string[] = [];
  for (const [k, v] of Object.entries(d)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      out.push(...flatten(v as Dict, path));
    } else {
      out.push(path);
    }
  }
  return out;
}

describe('locales — completeness', () => {
  const enDict = load('en');
  const frDict = load('fr');
  const itDict = load('it');
  const enKeys = new Set(flatten(enDict));

  it('fr.json covers every key in en.json', () => {
    const frKeys = new Set(flatten(frDict));
    const missing = [...enKeys].filter((k) => !frKeys.has(k));
    expect(missing).toEqual([]);
  });

  it('it.json covers every key in en.json', () => {
    const itKeys = new Set(flatten(itDict));
    const missing = [...enKeys].filter((k) => !itKeys.has(k));
    expect(missing).toEqual([]);
  });

  it('no translation is an empty string', () => {
    for (const [name, dict] of [
      ['en', enDict], ['fr', frDict], ['it', itDict],
    ] as const) {
      const walk = (d: Dict, prefix = '') => {
        for (const [k, v] of Object.entries(d)) {
          const path = prefix ? `${prefix}.${k}` : k;
          if (v && typeof v === 'object' && !Array.isArray(v)) {
            walk(v as Dict, path);
          } else {
            expect(v, `${name}.${path} is empty`).not.toBe('');
          }
        }
      };
      walk(dict);
    }
  });
});
