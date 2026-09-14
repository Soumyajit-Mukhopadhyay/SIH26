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
  /** Qualitative CAPE band when name is lightning and CAPE was assessed. */
  band?: string | null;
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
  /** Null when the vessel was unknown and no LOA was supplied. */
  loa_m: number | null;
  /** How the vessel was resolved: category chip, LOA match, or unknown. */
  vessel_source?: 'category' | 'loa' | 'unknown';
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
  /** Phase 3 — parallel models (ORCA_LEGACY 0-100 + optional INCOIS BSI 0-7). */
  environmental_models?: EnvironmentalModel[];
}

export interface EnvironmentalModel {
  model: string;
  score: number | null;
  scale: string;
  verdict?: string | null;
  completeness?: string;
  hazard?: string;
  partial_contribution?: number;
  components?: Array<Record<string, unknown>>;
  beam_criterion?: Record<string, unknown> | null;
  limitations?: string[];
  notes?: string;
  scientific_status?: string;
  source?: string;
  source_url?: string;
  disclaimer?: string;
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

export interface GeocodeResult {
  name: string;
  address: string;
  lat: number;
  lon: number;
  provider: string;
}

export interface GeocodeSearchResponse {
  results: GeocodeResult[];
  provider: string;
  ok: boolean;
  cached?: boolean;
  error?: string;
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

export interface IntegrationState {
  implemented: boolean;
  configured: boolean;
  runtime_ready: boolean;
  mode: string;
  caveat: string;
}

export interface GroundPoint {
  time: string;
  lon: number;
  lat: number;
}

export interface SatelliteOverpass {
  satellite: string;
  norad_id: number;
  sensor: string;
  start_time: string;
  closest_time: string;
  end_time: string;
  closest_distance_km: number;
  swath_width_km: number;
  daylight_at_target: boolean;
  tle_epoch: string;
  tle_age_hours: number;
  tle_provenance: Provenance;
  confidence: string;
  source_url: string;
  caveat: string;
  ground_track: GroundPoint[];
}

export interface OverpassResponse {
  lat: number;
  lon: number;
  generated_at: string;
  horizon_hours: number;
  step_seconds: number;
  passes: SatelliteOverpass[];
  unavailable_satellites: string[];
  method: string;
  caveat: string;
}

export type ValidationStatus = 'agree' | 'disagree' | 'inconclusive' | 'unavailable';

export interface CrossCheck {
  variable: string;
  status: ValidationStatus;
  primary: Evidence;
  secondary: Evidence;
  canonical_unit: string;
  primary_value: number | null;
  secondary_value: number | null;
  absolute_difference: number | null;
  tolerance: number | null;
  time_separation_hours: number | null;
  tolerance_basis: string;
  message: string;
}

export interface CrossValidationResponse {
  lat: number;
  lon: number;
  generated_at: string;
  checks: CrossCheck[];
  summary: Record<ValidationStatus, number>;
  note: string;
}

export interface VesselPosition {
  mmsi: string;
  name: string | null;
  lat: number;
  lon: number;
  speed_kn: number | null;
  course_deg: number | null;
  heading_deg: number | null;
  message_type: string;
  received_at: string;
  provenance: Provenance;
}

export type CollisionLevel = 'danger' | 'warning' | 'monitor' | 'clear' | 'insufficient';

export interface CollisionAdvisory {
  mmsi: string;
  name: string | null;
  level: CollisionLevel;
  current_distance_nm: number;
  bearing_deg: number;
  tcpa_minutes: number | null;
  dcpa_nm: number | null;
  reason: string;
}

export interface OwnMotion {
  speed_kn: number;
  course_deg: number;
  horizon_minutes: number;
}

export interface AisSnapshot {
  bbox: [number, number, number, number];
  started_at: string;
  duration_seconds: number;
  connected: boolean;
  vessels: VesselPosition[];
  raw_position_reports: number;
  own_motion: OwnMotion | null;
  collision_advisories: CollisionAdvisory[];
  collision_summary: Record<CollisionLevel, number>;
  provenance: Provenance;
  error: string | null;
  coverage_note: string;
}

export interface FishingEntry {
  vessel_id: string | null;
  mmsi: string | null;
  name: string | null;
  flag: string | null;
  gear_type: string | null;
  lat: number | null;
  lon: number | null;
  apparent_fishing_hours: number;
}

export interface FishingEffortResponse {
  bbox: [number, number, number, number];
  start_date: string;
  end_date: string;
  available: boolean;
  entries: FishingEntry[];
  total_apparent_fishing_hours: number;
  vessel_count: number;
  evidence: Evidence;
  error: string | null;
  caveat: string;
}

export interface NasaGranuleSearch {
  hits: number;
  granules: { concept_id: string; title: string; start_time: string | null }[];
  note: string;
}

export interface SentinelCatalogueSearch {
  returned: number;
  items: { item_id: string; acquired_at: string | null; cloud_cover_percent: number | null }[];
  note: string;
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
    cape_hard_veto?: boolean;
    cape_veto_j_kg: number;
    cape_bands_j_kg?: Record<string, string>;
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
  | 'bathymetric';

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

export interface VectorEncoding {
  u_channel: string;
  v_channel: string;
  magnitude_channel: string;
  mask_channel: string;
  zero_point: number;
  max_abs: number;
  scale: number;
  formula: string;
  note: string;
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
  /** 'vector' for the u/v flow fields; absent for colour-mapped scalars. */
  kind?: 'vector';
  encoding?: VectorEncoding;
  direction_convention?: 'from' | 'to';
  convention_note?: string;
  particle_speed?: number;
  label?: string;
  description?: string;
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

/** One lattice cell on a planned route, with the verdict the engine gave it. */
export interface RouteCell {
  lat: number;
  lon: number;
  passable: boolean;
  reason: string | null;
  verdict: Verdict | null;
  index: number | null;
  wave_m: number | null;
  wind_kn: number | null;
}

export interface RoutePlan {
  ok: boolean;
  router_version: string;
  boat_class: string;
  /** True when neither category nor LOA was supplied — route refused honestly. */
  vessel_unknown?: boolean;
  thresholds_version?: string | null;
  lattice: {
    step_deg: number;
    coarsened: boolean;
    nodes: number;
    water_nodes: number;
    passable_nodes: number;
    vetoed_water_nodes: number;
    note: string;
  };
  /** Set when a whole upstream variable was missing, so the route was costed on
   *  less than the full picture. Governs how much any of it is worth. */
  degraded: string | null;
  /** Present only when `ok`. */
  start_note?: string;
  goal_note?: string;
  waypoints?: RouteCell[];
  /** [lon, lat] pairs — the full lattice path, for drawing. */
  path?: [number, number][];
  distance_nm?: number;
  direct_nm?: number;
  detour_pct?: number;
  duration_h?: number;
  speed_kn?: number;
  worst_verdict?: Verdict;
  worst_index?: number | null;
  mean_index?: number | null;
  why_this_route?: string;
  disclaimer?: string;
  /** Present on both outcomes: the cells a straight run would have crossed. */
  refused_on_direct_line?: RouteCell[];
  /** Present only on a refusal. */
  reason?: string;
  direct_line?: RouteCell[];
  what_would_change_it?: string[];
  blocked_by?: string[];
  requested: {
    from: [number, number];
    to: [number, number];
    speed_kn: number;
    boat_class: string;
  };
  generated_at: string;
}

/** One containment area from a drift simulation. */
export interface DriftArea {
  /** 0.5 or 0.95 — the fraction of particles inside the ring. */
  fraction: number;
  /** Closed ring of [lon, lat]. */
  ring: [number, number][];
  kept: number;
  of: number;
  centre: [number, number];
  radius_km: number;
  area_km2: number;
}

export interface DriftPlan {
  drift_version: string;
  hours: number;
  object_class: {
    code: string;
    label: string;
    downwind_leeway_pct_of_wind: number;
    crosswind_leeway_pct_of_wind: number;
    coefficient_spread: number;
    note: string;
  };
  last_known_position: [number, number];
  /** The centre of the distribution. NOT a predicted position, and the UI must
   *  never present it as one. */
  mean_position: [number, number];
  displacement_km: number;
  displacement_nm: number;
  bearing_deg: number;
  bearing: string;
  track: [number, number][];
  areas: DriftArea[];
  spread_sources: {
    current_field_error_ms: number;
    current_field_error_km_1sigma: number;
    leeway_coefficient_spread: number;
    subgrid_eddy_ms: number;
    /** Which term is actually setting the size of the area. */
    dominant: string;
  } | null;
  diagnostics: {
    steps: number;
    step_minutes: number;
    particles: number;
    missing_fields: string[];
    field_samples_taken: number;
    warning?: string;
  };
  model: string;
  not_modelled: string[];
  disclaimer: string;
  geojson: { type: 'FeatureCollection'; features: unknown[] };
  generated_at: string;
}

export interface DriftClass {
  code: string;
  label: string;
  downwind_leeway_pct_of_wind: number;
  crosswind_leeway_pct_of_wind: number;
  coefficient_spread: number;
  note: string;
}

// --------------------------------------------------------------------------
// Distress: the full response package for a person or vessel in the water.
// --------------------------------------------------------------------------

/** One Maritime Rescue Coordination Centre or Sub-Centre, with the numbers
 *  from Appendix 'A' of the National Maritime SAR Plan 2022. */
export interface RescueContact {
  name: string;
  kind: 'MRCC' | 'MRSC';
  coordinates: { lat: number; lon: number };
  distance_km: number;
  bearing_from_incident_deg: number;
  state: string;
  sea_area: string;
  coordinating_mrcc: string;
  /** Distress line first — under pressure people dial the first number they see. */
  telephone: string[];
  email: string[];
  fax: string[];
  /** The link that still works when the mobile network does not. */
  inmarsat_c: string | null;
  aftn: string | null;
  position_note: string;
}

export interface SweepWidth {
  search_object: string;
  substituted_default: boolean;
  sru_column: 'vessel' | 'small_boat';
  visibility_nm: number;
  visibility_assumed: boolean;
  uncorrected_nm: number;
  weather_factor: number;
  weather_note: string;
  fatigue_factor: number;
  corrected_nm: number;
  corrected_km: number;
}

export interface SearchPlan {
  unit: {
    code: string;
    label: string;
    search_speed_kn: number;
    transit_speed_kn: number;
    on_scene_endurance_h: number;
    max_search_wave_m: number;
    note: string;
  };
  units_assigned: number;
  sweep_width: SweepWidth;
  track_spacing_nm: number;
  track_spacing_km: number;
  /** True when conditions want legs closer than a boat can navigate, which
   *  costs coverage rather than gaining it. */
  track_spacing_floored: boolean;
  coverage_requested: number;
  coverage_achieved: number;
  area_km2: number;
  area_sq_nm: number;
  search_hours: number;
  track_length_nm: number;
  probability_of_detection: number;
  pattern: { code: string; name: string; why: string };
  /** Everything that stops this plan from closing. Empty means it closes. */
  limits: string[];
  closes: boolean;
  conditions_used: {
    visibility_km: number | null;
    wind_kn: number | null;
    wave_m: number | null;
  };
  how_to_read: string;
  pod_is_modelled: string;
  pos_note: string;
  aircraft_note: string;
  citations: Citation[];
}

export interface DistressTransit {
  /** False means this is a great-circle fallback, not a checked route. */
  routed: boolean;
  estimate?: string;
  reason?: string;
  unit?: string;
  transit_speed_kn?: number;
  distance_nm?: number;
  distance_km?: number;
  direct_nm?: number;
  detour_pct?: number;
  launch_delay_h?: number;
  steaming_hours?: number;
  total_hours?: number;
  eta_note?: string;
  path?: [number, number][];
  worst_verdict?: string;
  why_this_route?: string;
  degraded?: string | null;
  router_note?: string;
  what_a_refusal_means?: string;
}

export interface DistressResponse {
  distress_version: string;
  raised_at: string;
  incident: {
    last_known_position: { lat: number; lon: number };
    hours_since_last_known: number;
    object_class: DriftPlan['object_class'];
    datum: { lat: number; lon: number };
    datum_note: string;
  };
  search_area: {
    containment: { fraction: number; area_km2: number; ring: [number, number][] }[];
    displacement_km: number;
    bearing: string;
    spread_sources: DriftPlan['spread_sources'];
  };
  search_plan: SearchPlan;
  conditions_at_datum: {
    wave_m: number | null;
    wind_kn: number | null;
    visibility_km: number | null;
  };
  notify: RescueContact[];
  coordinating_mrcc: string;
  first_call: {
    number: string;
    why: string;
    nearest_centre: string;
    nearest_centre_km: number;
    nearest_centre_bearing_deg: number;
  };
  transit: DistressTransit;
  datum_growth: {
    estimated: boolean;
    why?: string;
    area_now_km2?: number;
    hours_at_arrival?: number;
    area_on_arrival_km2?: number;
    growth_factor?: number;
    why_it_matters?: string;
    method?: string;
  };
  not_a_dispatch: string;
  citations: Citation[];
}
