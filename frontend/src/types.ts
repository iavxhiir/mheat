// Shared TypeScript types mirroring the FastAPI schemas.

export interface MhwImpact {
  n_aquaculture_sites: number;
  mpa_area_km2: number;
  seagrass_area_km2: number;
  summary?: string;
}

export interface MhwEventProperties {
  event_id: string;
  date_start: string;
  date_end: string;
  date_peak: string;
  duration_days: number;
  intensity_max: number;
  intensity_mean: number;
  intensity_cumulative: number;
  category: number; // 1..5
  category_name: string;
  n_pixels: number;
  centroid: [number, number];
  impact?: MhwImpact | null;
}

export interface MhwEventFeature {
  type: 'Feature';
  id: string;
  geometry: {
    type: 'Polygon';
    coordinates: number[][][];
  };
  properties: MhwEventProperties;
}

export interface MhwEventCollection {
  type: 'FeatureCollection';
  features: MhwEventFeature[];
}

export interface OverlayFeature {
  type: 'Feature';
  properties: Record<string, unknown>;
  geometry: {
    type: string;
    coordinates: unknown;
  };
}

export interface OverlayCollection {
  type: 'FeatureCollection';
  features: OverlayFeature[];
}

export type OverlayKind = 'aquaculture' | 'mpa' | 'seagrass';

export interface LayerToggles {
  aquaculture: boolean;
  mpa: boolean;
  seagrass: boolean;
  anomaly: boolean;
}
