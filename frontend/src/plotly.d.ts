// Minimal shim for plotly.js-dist-min which ships without type definitions.
declare module 'plotly.js-dist-min' {
  export type Data = Record<string, unknown>;
  export type Layout = Record<string, unknown>;
  export function react(
    root: HTMLElement,
    data: Data[],
    layout?: Partial<Layout>,
    config?: Record<string, unknown>
  ): Promise<void>;
  export function newPlot(
    root: HTMLElement,
    data: Data[],
    layout?: Partial<Layout>,
    config?: Record<string, unknown>
  ): Promise<void>;
  export function purge(root: HTMLElement): void;

  const Plotly: {
    react: typeof react;
    newPlot: typeof newPlot;
    purge: typeof purge;
  };
  export default Plotly;
}
