/**
 * Types mirroring the backend's pydantic models.
 *
 * Hand-written rather than generated, deliberately: the shapes that matter here
 * are few and stable, and writing them out means the frontend states its
 * expectations explicitly instead of inheriting whatever the server happens to
 * emit. `Evidence` in particular is the contract, and it should be readable.
 */

/** The provenance badge states. Mirrors orca.provenance.Provenance exactly. */
export type Provenance =
  | 'live'
  | 'cached'
  | 'curated'
  | 'derived'
  | 'simulated'
  | 'unavailable';

export interface Freshness {
  valid_time: string;
  retrieved_at: string;
  age_hours: number;
  is_stale: boolean;
  stale_after: string;
  /** Human sentence. When present it is shown BEFORE the value, never after. */
  note: string | null;
}

export interface Citation {
  label: string;
  provider: string;
  url: string | null;
  identifier: string | null;
  accessed_at: string | null;
  quote: string | null;
}

/** One value, and everything needed to defend it. Never a bare number. */
export interface Evidence {
  dataset_id: string;
  provider: string;
  variable: string;
  value: number | string | boolean | null;
  unit: string | null;
  provenance: Provenance;
  freshness: Freshness;
  /** For DERIVED: the dataset_ids it was computed from. Always non-empty there. */
  lineage: string[];
  url: string | null;
  location: [number, number] | null;
  method: string | null;
  uncertainty: number | null;
  citations: Citation[];
  notes: string | null;
}

export interface EvidenceSummary {
  count: number;
  /** The WORST provenance state present, never the most flattering. */
  provenance: Provenance | null;
  mix: Provenance[];
  stale: boolean;
  max_age_hours: number | null;
}

export interface PointForecast {
  lat: number;
  lon: number;
  place: string | null;
  generated_at: string;
  evidence: Record<string, Evidence>;
  summary: EvidenceSummary;
}

export type Verdict = 'GO' | 'CAUTION' | 'NO-GO' | 'UNVERIFIABLE';

export interface RiskComponent {
  name: 'wave' | 'wind' | 'visibility' | 'lightning';
  value: number | null;
  unit: string;
  limit: number;
  score: number;
  weight: number;
  contribution: number;
  /** The actual arithmetic, shown in the UI. */
  formula: string;
  exceeded: boolean;
}

export interface RiskResult {
  verdict: Verdict;
  /** Typed on the server so 'llm' is unrepresentable. */
  verdict_source: 'rule_engine';
  index: number;
  vetoes: string[];
  components: RiskComponent[];
  boat_class_code: string;
  boat_class_label: string;
  loa_m: number;
  confidence: 'high' | 'low';
  escalate: boolean;
  escalation_message: string | null;
  data_age_hours: number;
  thresholds_version: string;
  evaluated_at: string;
  evidence: Evidence[];
  citations: Citation[];
  what_would_change_it: string[];
  disclaimer: string;
}

export interface SeriesPoint {
  t: string;
  v: number;
}

export interface SeriesVariable {
  unit: string | null;
  provenance: Provenance;
  points: SeriesPoint[];
}

export interface ForecastSeries {
  lat: number;
  lon: number;
  generated_at: string;
  variables: Record<string, SeriesVariable>;
}

export interface Landmark {
  key: string;
  lat: number;
  lon: number;
  label: string;
}

export interface SourceRow {
  source: string;
  provider: string;
  variables: string[];
  status: 'ok' | 'degraded' | 'failing' | 'untried' | 'circuit_open' | 'dormant';
  dormant_reason: string | null;
  last_success: string | null;
  last_attempt: string | null;
  age_hours: number | null;
  last_error: string | null;
  successes: number;
  failures: number;
  consecutive_failures: number;
  circuit_open: boolean;
  provenance: Provenance;
  median_latency_ms: number | null;
}

export interface FreshnessReport {
  generated_at: string;
  summary: Record<string, number>;
  sources: SourceRow[];
  legend: Record<string, string>;
}

export interface Health {
  status: 'ok' | 'degraded';
  degraded: string[];
  notes: string[];
  service: string;
  version: string;
  env: string;
  time: string;
  uptime_s: number;
  runtime: { python: string; platform: string };
  infrastructure: {
    db: string;
    db_fallback_reason: string | null;
    cache: string;
    queue: string;
    redis_reason: string | null;
    geofence_index: string;
    started_at: string;
  };
  capabilities: Record<string, boolean>;
  sources: Record<string, number>;
}

export interface GridDatasetRow {
  key: string;
  dataset_id: string;
  title: string;
  provider: string;
  role: 'live' | 'climatology' | 'archive';
  coverage_start: string | null;
  coverage_end: string | null;
  is_current: boolean;
  provenance: Provenance;
  notes: string | null;
  url: string;
}

export interface DatasetRoster {
  generated_at: string;
  grids: GridDatasetRow[];
  point_sources: {
    name: string;
    provider: string;
    variables: string[];
    role: string;
  }[];
}

export interface ThresholdClass {
  code: string;
  label: string;
  loa_range_m: [number, number];
  max_wave_m: number;
  max_wind_kn: number;
  min_visibility_km: number;
  notes: string | null;
  source_citation: Citation;
}

export interface ThresholdTable {
  classes: ThresholdClass[];
  policy: {
    thresholds_version: string;
    lightning_veto_pct: number;
    cape_veto_j_kg: number;
    confidence_age_limit_h: number;
    weights: Record<string, number>;
    citations: Citation[];
    disclaimer: string;
  };
}

/** A stylistic full-screen post-process. Held separate from data colormaps. */
export type Treatment =
  | 'standard'
  | 'thermal'
  | 'night-vision'
  | 'radar'
  | 'bathymetric'
  | 'crt'
  | 'noir';

// ---------------------------------------------------------------- rasters

export interface ColormapStop {
  value: number;
  rgba: [number, number, number, number];
}

export interface ColormapMeta {
  kind: 'continuous' | 'categorical';
  cmap?: string;
  vmin?: number;
  vmax?: number;
  label?: string;
  description?: string;
  stops?: ColormapStop[];
  classes?: { value: number; rgba: [number, number, number, number]; label: string }[];
}

export interface RasterStatistics {
  valid_cells: number;
  total_cells: number;
  min: number | null;
  max: number | null;
  mean: number | null;
}

export interface PfzDerivation {
  detector: string;
  inputs_used: string[];
  inputs_missing: string[];
  rank_cells: Record<string, number>;
  max_rank: number;
  zone_count: number;
  method: string;
  notes: string[];
  front?: { detector: string; method: string; citation: string; front_cells: number };
}

export interface RasterVariable {
  variable: string;
  unit: string | null;
  valid_time: string | null;
  generated_at: string | null;
  provenance: Provenance;
  lineage: string[];
  method: string | null;
  /** deck.gl BitmapLayer bounds: [west, south, east, north]. */
  bounds: [number, number, number, number] | null;
  bytes: number;
  colormap: ColormapMeta | null;
  statistics: RasterStatistics | null;
  png: string;
  sidecar: string;
  timesteps: string[];
  /** Present on pfz_rank only. */
  pfz?: PfzDerivation;
}

export interface RasterCatalogue {
  generated_at: string;
  variables: RasterVariable[];
  grid: Record<string, number | string>;
  h3_resolution: number;
  refresh: { running: boolean; last: Record<string, unknown> | null };
  hint?: string;
}

export interface PfzZone {
  rank: number;
  cells: number;
  area_km2: number;
  centroid: { lat: number; lon: number };
  h3: string | null;
  polygon: [number, number][];
}

export interface PfzZonesResponse {
  valid_time: string | null;
  provenance: Provenance;
  lineage: string[];
  method: string | null;
  derivation: PfzDerivation;
  zone_count: number;
  zones: PfzZone[];
  disclaimer: string;
}

export interface PfzNearest {
  found: boolean;
  detail?: string;
  zone?: PfzZone;
  distance_km?: number;
  bearing_deg?: number;
  compass?: string;
  narrative?: string;
  valid_time?: string;
  provenance?: Provenance;
  lineage?: string[];
  method?: string;
}

// ---------------------------------------------------------------- geofence

export interface FenceProperties {
  key: string;
  name: string;
  kind: 'eez' | 'imbl' | 'eez_outer';
  authority: string;
  consequence: string;
  length_km: number | null;
}

export interface FenceCollection {
  type: 'FeatureCollection';
  features: {
    type: 'Feature';
    geometry: { type: string; coordinates: unknown };
    properties: FenceProperties;
  }[];
  provenance: Provenance;
  citation: Citation;
  simplified_deg: number;
  note: string;
}

export interface Proximity {
  fence: string;
  name: string;
  kind: string;
  inside: boolean;
  distance_km: number;
  bearing_deg: number;
  compass: string;
  nearest_point: { lat: number; lon: number };
  state: 'outside' | 'approaching' | 'crossed' | 'inside' | 'exited';
  time_to_cross_min: number | null;
  closing: boolean | null;
  consequence: string;
  authority: string;
  narrative: string;
}

export interface GeofenceCheck {
  position: { lat: number; lon: number };
  heading_deg: number | null;
  speed_kn: number | null;
  generated_at: string;
  fences_in_range: number;
  proximities: Proximity[];
  transitions: Proximity[];
  states: Record<string, string>;
  provenance: Provenance;
  note: string;
}
