import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Timeline } from '../Timeline';
import { I18nProvider } from '../../i18n';

function renderTimeline(props: Parameters<typeof Timeline>[0]) {
  return render(
    <I18nProvider>
      <Timeline {...props} />
    </I18nProvider>,
  );
}

describe('Timeline', () => {
  it('renders two date inputs pre-filled with the controlled values', () => {
    renderTimeline({ start: '2022-07-01', end: '2022-08-31', onChange: () => {} });
    const inputs = Array.from(
      document.querySelectorAll<HTMLInputElement>('input[type=date]'),
    );
    const values = inputs.map((el) => el.value);
    expect(values).toContain('2022-07-01');
    expect(values).toContain('2022-08-31');
  });

  it('fires onChange with the new start date when the start input changes', () => {
    const handler = vi.fn();
    renderTimeline({ start: '2022-07-01', end: '2022-08-31', onChange: handler });
    const starts = Array.from(document.querySelectorAll('input[type=date]'));
    fireEvent.change(starts[0], { target: { value: '2022-06-15' } });
    expect(handler).toHaveBeenCalledWith('2022-06-15', '2022-08-31');
  });

  it('fires onChange with the new end date when the end input changes', () => {
    const handler = vi.fn();
    renderTimeline({ start: '2022-07-01', end: '2022-08-31', onChange: handler });
    const inputs = Array.from(document.querySelectorAll('input[type=date]'));
    fireEvent.change(inputs[1], { target: { value: '2022-09-15' } });
    expect(handler).toHaveBeenCalledWith('2022-07-01', '2022-09-15');
  });

  it('exposes the three event presets with ARIA-labelled buttons', () => {
    renderTimeline({ start: '', end: '', onChange: () => {} });
    for (const label of ['2003 Euro heatwave', '2022 Med summer', '2024 season']) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it('jumps to the preset window when its button is clicked', () => {
    const handler = vi.fn();
    renderTimeline({ start: '', end: '', onChange: handler });
    fireEvent.click(screen.getByText('2022 Med summer'));
    expect(handler).toHaveBeenCalledWith('2022-05-15', '2022-09-15');
  });

  it('wraps inputs in a labelled fieldset for a11y', () => {
    const { container } = renderTimeline({ start: '', end: '', onChange: () => {} });
    const fieldset = container.querySelector('fieldset');
    expect(fieldset).toBeTruthy();
    expect(fieldset!.querySelector('legend')?.textContent).toBeTruthy();
  });
});
