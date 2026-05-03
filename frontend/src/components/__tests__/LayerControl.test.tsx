import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LayerControl } from '../LayerControl';
import { I18nProvider } from '../../i18n';
import type { LayerToggles } from '../../types';

const DEFAULT: LayerToggles = {
  anomaly: false,
  aquaculture: true,
  mpa: true,
  seagrass: false,
};

function renderControl(value: LayerToggles, onChange: (v: LayerToggles) => void) {
  return render(
    <I18nProvider>
      <LayerControl value={value} onChange={onChange} />
    </I18nProvider>,
  );
}

describe('LayerControl', () => {
  it('renders a checkbox for each known layer reflecting the current toggles', () => {
    renderControl(DEFAULT, () => {});
    const anomaly = screen.getByRole('checkbox', { name: /anomaly/i }) as HTMLInputElement;
    const aquaculture = screen.getByRole('checkbox', { name: /aquaculture/i }) as HTMLInputElement;
    expect(anomaly.checked).toBe(false);
    expect(aquaculture.checked).toBe(true);
  });

  it('toggles only the clicked layer, preserving the rest', () => {
    const handler = vi.fn();
    renderControl(DEFAULT, handler);
    fireEvent.click(screen.getByRole('checkbox', { name: /seagrass/i }));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ ...DEFAULT, seagrass: true });
  });

  it('wraps controls in a labelled fieldset for a11y', () => {
    const { container } = renderControl(DEFAULT, () => {});
    const fieldset = container.querySelector('fieldset');
    expect(fieldset).toBeTruthy();
    expect(fieldset!.querySelector('legend')?.textContent).toBeTruthy();
  });
});
