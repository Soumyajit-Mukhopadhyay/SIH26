/**
 * The single door to the backend.
 *
 * Everything goes through `/api`, which Vite proxies to the FastAPI server in
 * dev and a reverse proxy serves in production. No base URL to configure, no
 * CORS story, and nothing to get wrong between the two environments.
 *
 * `ApiError` carries the status and the server's own detail string, because
 * "422: unknown boat class 'IND-XYZ'; see GET /risk/thresholds" is a useful
 * thing to show a user and "request failed" is not.
 */

import type {
  DatasetRoster,
  FenceCollection,
  GeofenceCheck,
  ForecastSeries,
  FreshnessReport,
  Health,
  Landmark,
  PfzNearest,
  PfzZonesResponse,
  PointForecast,
  DriftClass,
  DriftPlan,
  RasterCatalogue,
  RiskResult,
  RoutePlan,
  ThresholdTable,
  AisSnapshot,
  CrossValidationResponse,
  FishingEffortResponse,
  IntegrationState,
  NasaGranuleSearch,
  OverpassResponse,
  SentinelCatalogueSearch,
} from './types';

const BASE = '/api';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly path: string,
  ) {
    super(`${status} on ${path}: ${detail}`);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    });
  } catch (cause) {
    // A network failure is a first-class state, not an exception to swallow:
    // ORCA has an offline mode and the UI needs to distinguish "the server said
    // no" from "there is no server".
    throw new ApiError(0, cause instanceof Error ? cause.message : 'network unreachable', path);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* a non-JSON error body is fine; statusText will do */
    }
    throw new ApiError(response.status, detail, path);
  }

  return (await response.json()) as T;
}

const q = (params: Record<string, string | number | boolean | undefined>) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&');

export const api = {
  health: () => request<Health>('/healthz'),

  freshness: () => request<FreshnessReport>('/freshness'),

  datasets: () => request<DatasetRoster>('/datasets'),

  landmarks: () => request<{ landmarks: Landmark[] }>('/landmarks'),

  thresholds: () => request<ThresholdTable>('/risk/thresholds'),

  integrations: () => request<Record<string, IntegrationState>>('/integrations/status'),

  overpasses: (lat: number, lon: number, hours = 48) =>
    request<OverpassResponse>(`/satellites/overpasses?${q({ lat, lon, hours })}`),

  validatePoint: (lat: number, lon: number, includeWave = true) =>
    request<CrossValidationResponse>(
      `/validation/point?${q({ lat, lon, include_wave: includeWave })}`,
    ),

  aisSnapshot: (
    lat: number,
    lon: number,
    radiusDeg = 0.5,
    durationSeconds = 5,
    ownSpeedKn?: number,
    ownCourseDeg?: number,
  ) =>
    request<AisSnapshot>(
      `/traffic/ais?${q({
        lat,
        lon,
        radius_deg: radiusDeg,
        duration_seconds: durationSeconds,
        own_speed_kn: ownSpeedKn,
        own_course_deg: ownCourseDeg,
      })}`,
    ),

  fishingEffort: (lat: number, lon: number, radiusDeg = 0.5, days = 30) =>
    request<FishingEffortResponse>(
      `/traffic/fishing-effort?${q({ lat, lon, radius_deg: radiusDeg, days })}`,
    ),

  nasaCatalogue: (lat: number, lon: number, days = 7) =>
    request<NasaGranuleSearch>(`/catalog/nasa?${q({ lat, lon, days })}`),

  sentinelCatalogue: (lat: number, lon: number, days = 7) =>
    request<SentinelCatalogueSearch>(`/catalog/sentinel?${q({ lat, lon, days })}`),

  sentinelPreviewUrl: (
    lat: number,
    lon: number,
    collection: 'sentinel-3-olci' | 'sentinel-2-l2a' = 'sentinel-3-olci',
    days = 7,
    size = 384,
  ) => `/api/imagery/sentinel/preview?${q({ lat, lon, collection, days, size })}`,

  nasaGibsPreviewUrl: (lat: number, lon: number, days = 3, size = 384) =>
    `/api/imagery/nasa/preview?${q({ lat, lon, days, size })}`,

  forecastPoint: (lat: number, lon: number, includeSatelliteSst = true) =>
    request<PointForecast>(
      `/forecast/point?${q({ lat, lon, include_satellite_sst: includeSatelliteSst })}`,
    ),

  forecastSeries: (lat: number, lon: number, days = 3) =>
    request<ForecastSeries>(`/forecast/series?${q({ lat, lon, days })}`),

  fences: (simplifyDeg = 0.01) =>
    request<FenceCollection>(`/geofence/geojson?${q({ simplify_deg: simplifyDeg })}`),

  geofenceCheck: (
    lat: number,
    lon: number,
    headingDeg?: number,
    speedKn?: number,
    previous: Record<string, string> = {},
  ) =>
    request<GeofenceCheck>('/geofence/check', {
      method: 'POST',
      body: JSON.stringify({
        lat,
        lon,
        heading_deg: headingDeg,
        speed_kn: speedKn,
        // The India EEZ is an area, so a point near the middle can be more than
        // 150 km from either polygon edge. Use the backend's supported maximum
        // to expose the current inside/outside state in the verdict UI.
        radius_km: 600,
        previous,
      }),
    }),

  capUrl: (lat: number, lon: number, loaM: number, translateTo?: string) =>
    `/api/advisories/cap?${q({ lat, lon, loa_m: loaM, translate_to: translateTo })}`,

  planRoute: (args: {
    fromLat: number;
    fromLon: number;
    toLat: number;
    toLon: number;
    loaM: number;
    speedKn: number;
  }) =>
    request<RoutePlan>('/route/plan', {
      method: 'POST',
      body: JSON.stringify({
        from_lat: args.fromLat,
        from_lon: args.fromLon,
        to_lat: args.toLat,
        to_lon: args.toLon,
        loa_m: args.loaM,
        speed_kn: args.speedKn,
      }),
    }),

  sarClasses: () =>
    request<{ classes: DriftClass[]; current_field_error_ms: number; note: string }>(
      '/sar/classes',
    ),

  sarDrift: (args: { lat: number; lon: number; hours: number; objectClass: string }) =>
    request<DriftPlan>('/sar/drift', {
      method: 'POST',
      body: JSON.stringify({
        lat: args.lat,
        lon: args.lon,
        hours: args.hours,
        object_class: args.objectClass,
      }),
    }),

  rasterCatalogue: () => request<RasterCatalogue>('/rasters/catalogue'),

  refreshRasters: (detector: 'sobel' | 'canny' | 'sied' = 'sobel') =>
    request<{ status: string; detail: string }>(`/rasters/refresh?${q({ detector })}`, {
      method: 'POST',
    }),

  pfzZones: (minRank = 1) => request<PfzZonesResponse>(`/pfz/zones?${q({ min_rank: minRank })}`),

  pfzNearest: (lat: number, lon: number, minRank = 1) =>
    request<PfzNearest>(`/pfz/nearest?${q({ lat, lon, min_rank: minRank })}`),

  assessRisk: (lat: number, lon: number, loaM: number, boatClassCode?: string) =>
    request<RiskResult>('/risk/assess', {
      method: 'POST',
      body: JSON.stringify({
        lat,
        lon,
        loa_m: loaM,
        ...(boatClassCode ? { boat_class_code: boatClassCode } : {}),
      }),
    }),
};

/**
 * Human "3.2 h ago" / "just now" / "forecast +14 h".
 *
 * A forecast point legitimately has a NEGATIVE age: the value describes 06:00
 * tomorrow and we fetched it today. That is lead time, not staleness, and
 * rendering it as "-14.0 h ago" is meaningless to a reader — so future-valid
 * values are labelled as forecasts instead.
 */
export function relativeAge(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return 'never';
  if (hours < -0.017) {
    const lead = -hours;
    if (lead < 1) return `forecast +${Math.round(lead * 60)} min`;
    if (lead < 48) return `forecast +${lead.toFixed(1)} h`;
    return `forecast +${(lead / 24).toFixed(1)} days`;
  }
  if (hours < 0.017) return 'just now';
  if (hours < 1) return `${Math.round(hours * 60)} min ago`;
  if (hours < 48) return `${hours.toFixed(1)} h ago`;
  const days = hours / 24;
  if (days < 400) return `${days.toFixed(0)} days ago`;
  return `${(days / 365).toFixed(1)} years ago`;
}

/** Format a data value the way an instrument would: fixed precision, no drift. */
export function formatValue(value: number | string | boolean | null, unit?: string | null): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'string') return value;
  const magnitude = Math.abs(value);
  const decimals = magnitude >= 1000 ? 0 : magnitude >= 100 ? 1 : magnitude >= 10 ? 1 : 2;
  const text = value.toFixed(decimals);
  return unit ? `${text} ${unit}` : text;
}
