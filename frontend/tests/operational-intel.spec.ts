import { expect, test, type Route } from 'playwright/test';

const now = '2026-08-31T19:00:00Z';

function json(route: Route, body: unknown) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

function applicationErrors(errors: string[]) {
  // System Chrome's headless WebGL backend can intermittently reject a MapLibre
  // shader. This test exercises the HTML operational panel, not the map renderer.
  return errors.filter((error) => !error.includes('Could not compile fragment shader'));
}

test('operational panel calls and renders every new browser endpoint', async ({ page }) => {
  const requested = new Set<string>();
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.stack ?? error.message));
  await page.addInitScript(() => sessionStorage.setItem('orca.intro.seen', '1'));
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    requested.add(url.pathname);
    switch (url.pathname) {
      case '/api/healthz':
        return json(route, {
          status: 'ok', degraded: [], notes: [], service: 'orca', version: 'test', env: 'test',
          time: now, uptime_s: 1, runtime: { python: 'test', platform: 'test' },
          infrastructure: { db: 'sqlite', db_fallback_reason: null, cache: 'memory', queue: 'apscheduler', redis_reason: null, geofence_index: 'shapely-strtree', started_at: now },
          capabilities: {}, sources: {},
        });
      case '/api/freshness':
        return json(route, { generated_at: now, summary: {}, sources: [], legend: {} });
      case '/api/landmarks':
        return json(route, { landmarks: [{ key: 'chennai', lat: 13.1, lon: 80.4, label: 'Chennai test point' }] });
      case '/api/risk/thresholds':
        return json(route, { classes: [], policy: {} });
      case '/api/geofence/geojson':
        return json(route, { type: 'FeatureCollection', features: [], generated_at: now, source: 'test' });
      case '/api/rasters/catalogue':
        return json(route, { generated_at: now, variables: [], refresh: { running: false } });
      case '/api/sar/classes':
        return json(route, { classes: [], current_field_error_ms: 0.15, note: 'test' });
      case '/api/forecast/point':
        return json(route, { lat: 13.1, lon: 80.4, place: null, generated_at: now, evidence: {}, summary: { count: 0, provenance: null, mix: [], stale: false, max_age_hours: null } });
      case '/api/risk/assess':
        return json(route, {
          verdict: 'GO', verdict_source: 'rule_engine', index: 10, vetoes: [], components: [],
          boat_class_code: 'IND-MOTOR-S', boat_class_label: 'test boat', loa_m: 8.2,
          confidence: 'high', escalate: false, escalation_message: null, data_age_hours: 0,
          thresholds_version: 'test', evaluated_at: now, evidence: [], citations: [],
          what_would_change_it: [], disclaimer: 'test only',
        });
      case '/api/geofence/check':
        return json(route, {
          position: { lat: 13.1, lon: 80.4 }, heading_deg: null, speed_kn: null,
          generated_at: now, fences_in_range: 0, proximities: [], transitions: [], states: {},
          provenance: 'derived', note: 'test',
        });
      case '/api/satellites/overpasses':
        return json(route, {
          lat: 13.1, lon: 80.4, generated_at: now, horizon_hours: 48, step_seconds: 30,
          unavailable_satellites: [], method: 'SGP4', caveat: 'opportunity only',
          passes: [{ satellite: 'Sentinel-3A', norad_id: 41335, sensor: 'OLCI', start_time: now, closest_time: '2026-09-01T04:47:00Z', end_time: '2026-09-01T04:52:00Z', closest_distance_km: 28.4, swath_width_km: 1270, daylight_at_target: true, tle_epoch: now, tle_age_hours: 14.2, tle_provenance: 'live', confidence: 'nominal', source_url: 'https://example.test', caveat: 'opportunity only', ground_track: [] }],
        });
      case '/api/validation/point':
        return json(route, { lat: 13.1, lon: 80.4, generated_at: now, summary: { agree: 3, disagree: 0, inconclusive: 0, unavailable: 0 }, note: 'test', checks: [{ variable: 'sst', status: 'agree', message: 'Sources agree within 0.65 degC.' }, { variable: 'wind_speed', status: 'agree', message: 'Sources agree within 2.50 m/s.' }, { variable: 'wave_height', status: 'agree', message: 'Sources agree within 0.50 m.' }] });
      case '/api/traffic/ais':
        return json(route, { bbox: [79.9, 12.6, 80.9, 13.6], started_at: now, duration_seconds: 5, connected: true, vessels: [], raw_position_reports: 0, provenance: 'live', error: null, coverage_note: 'sparse coverage' });
      case '/api/traffic/fishing-effort':
        return json(route, { bbox: [79.9, 12.6, 80.9, 13.6], start_date: '2026-07-28', end_date: '2026-08-27', available: true, entries: [], total_apparent_fishing_hours: 206.3, vessel_count: 24, evidence: {}, error: null, caveat: 'apparent only' });
      case '/api/catalog/nasa':
        return json(route, { hits: 5, granules: [], note: 'metadata' });
      case '/api/catalog/sentinel':
        return json(route, { returned: 3, items: [], note: 'metadata' });
      default:
        return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"mock missing"}' });
    }
  });

  await page.goto('/');
  await page.getByRole('button', { name: /Chennai test point/ }).click();
  await expect.poll(() => [...requested]).toContain('/api/satellites/overpasses');
  await page.waitForTimeout(500);
  expect(applicationErrors(pageErrors)).toEqual([]);

  await expect(page.getByText('test only')).toBeVisible();
  await page.getByRole('button', { name: 'Collapse safety verdict' }).click();
  await expect(page.getByRole('button', { name: 'Expand safety verdict' })).toBeVisible();
  await expect(page.getByText('test only')).toBeHidden();
  await page.getByRole('button', { name: 'Expand safety verdict' }).click();
  await expect(page.getByText('test only')).toBeVisible();
  await expect(page.getByText(/deterministic rule engine/i)).toHaveCount(0);

  await page.getByRole('button', { name: /Orbital & vessel intelligence/ }).click();
  await expect(page.getByText(/Sentinel-3A/).first()).toBeVisible();
  await expect(page.getByText(/Opportunity only/)).toBeVisible();

  await page.getByRole('button', { name: /Cross-validate SST/ }).click();
  await expect(page.getByText('Sources agree within 0.65 degC.')).toBeVisible();
  await page.getByRole('button', { name: /Live AIS scan/ }).click();
  await expect(page.getByText(/Sparse coverage means zero/)).toBeVisible();
  await page.getByRole('button', { name: /GFW history/ }).click();
  await expect(page.getByText(/206.3 apparent fishing h/)).toBeVisible();
  await page.getByRole('button', { name: /Search NASA & Sentinel/ }).click();
  await expect(page.getByText(/NASA CMR: 5 matches/)).toBeVisible();

  expect([...requested]).toEqual(expect.arrayContaining([
    '/api/satellites/overpasses', '/api/validation/point', '/api/traffic/ais',
    '/api/traffic/fishing-effort', '/api/catalog/nasa', '/api/catalog/sentinel',
  ]));
  expect(applicationErrors(pageErrors)).toEqual([]);
});
