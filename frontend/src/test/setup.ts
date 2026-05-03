import { afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// jsdom 25 ships a partial localStorage (setItem only). Replace it with a
// Map-backed polyfill so tests for i18n / PWA persistence work reliably.
function installStoragePolyfill() {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k) => (store.has(k) ? store.get(k)! : null),
    key: (i) => Array.from(store.keys())[i] ?? null,
    removeItem: (k) => void store.delete(k),
    setItem: (k, v) => void store.set(k, String(v)),
  };
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: storage,
  });
  Object.defineProperty(window, 'sessionStorage', {
    configurable: true,
    value: storage,
  });
}

installStoragePolyfill();

// MapLibre-GL bootstraps by creating worker URLs via createObjectURL +
// loads tiles via fetch on import. jsdom doesn't ship either. We don't
// actually render the map in unit tests (Playwright covers that); the
// imports just need to not throw, so polyfill the missing globals with
// no-op stubs.
if (typeof window.URL.createObjectURL === 'undefined') {
  Object.defineProperty(window.URL, 'createObjectURL', {
    configurable: true,
    value: () => 'blob:noop',
  });
}
if (typeof window.URL.revokeObjectURL === 'undefined') {
  Object.defineProperty(window.URL, 'revokeObjectURL', {
    configurable: true,
    value: () => undefined,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});
