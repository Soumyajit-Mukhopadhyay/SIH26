import { expect, test, type Route } from 'playwright/test';
import { Buffer } from 'node:buffer';

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
  await page.setViewportSize({ width: 1093, height: 879 });
  const requested = new Set<string>();
  let geofenceRequests = 0;
  let forecastRequests = 0;
  let riskRequests = 0;
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
        forecastRequests += 1;
        if (forecastRequests > 1) {
          return json(route, {
            lat: 22.5,
            lon: 78.5,
            place: null,
            generated_at: now,
            evidence: {
              wind_speed: {
                dataset_id: 'open_meteo.forecast', provider: 'open_meteo', variable: 'wind_speed',
                value: 6.1, unit: 'kn', provenance: 'live',
                freshness: { valid_time: now, retrieved_at: now, age_hours: 0, is_stale: false, stale_after: now, note: null },
                lineage: [], url: null, location: [78.5, 22.5], method: null, uncertainty: null,
                citations: [], notes: null,
              },
              visibility: {
                dataset_id: 'open_meteo.forecast', provider: 'open_meteo', variable: 'visibility',
                value: 4.42, unit: 'km', provenance: 'live',
                freshness: { valid_time: now, retrieved_at: now, age_hours: 0, is_stale: false, stale_after: now, note: null },
                lineage: [], url: null, location: [78.5, 22.5], method: null, uncertainty: null,
                citations: [], notes: null,
              },
            },
            summary: { count: 2, provenance: 'live', mix: ['live'], stale: false, max_age_hours: 0 },
          });
        }
        return json(route, { lat: 13.1, lon: 80.4, place: null, generated_at: now, evidence: {}, summary: { count: 0, provenance: null, mix: [], stale: false, max_age_hours: null } });
      case '/api/risk/assess':
        riskRequests += 1;
        if (riskRequests > 1) {
          return json(route, {
            verdict: 'UNVERIFIABLE', verdict_source: 'rule_engine', index: 0, vetoes: [],
            components: [
              { name: 'wave', value: null, unit: 'm', limit: 1.5, score: 0, weight: 0.35, contribution: 0, formula: 'missing', exceeded: false },
              { name: 'wind', value: 6.1, unit: 'kn', limit: 22, score: 95, weight: 0.3, contribution: 28.5, formula: 'test', exceeded: false },
              { name: 'visibility', value: 4.42, unit: 'km', limit: 2, score: 80, weight: 0.15, contribution: 12, formula: 'test', exceeded: false },
              { name: 'lightning', value: 0, unit: '%', limit: 60, score: 100, weight: 0.2, contribution: 20, formula: 'test', exceeded: false },
            ],
            boat_class_code: 'IND-MOTOR-S', boat_class_label: 'test boat', loa_m: 8.2,
            confidence: 'low', escalate: true, escalation_message: 'wave unavailable', data_age_hours: 0,
            thresholds_version: 'test', evaluated_at: now, evidence: [], citations: [],
            what_would_change_it: [], disclaimer: 'test',
          });
        }
        return json(route, {
          verdict: 'GO', verdict_source: 'rule_engine', index: 82, vetoes: [], components: [
            { name: 'wave', value: 1.2, unit: 'm', limit: 1.5, score: 82, weight: 0.35, contribution: 28.7, formula: 'test', exceeded: false },
            { name: 'wind', value: 5.6, unit: 'kn', limit: 22, score: 95, weight: 0.3, contribution: 28.5, formula: 'test', exceeded: false },
            { name: 'visibility', value: 21, unit: 'km', limit: 2, score: 100, weight: 0.15, contribution: 15, formula: 'test', exceeded: false },
            { name: 'lightning', value: 0, unit: '%', limit: 60, score: 100, weight: 0.2, contribution: 20, formula: 'test', exceeded: false },
          ],
          boat_class_code: 'IND-MOTOR-S', boat_class_label: 'test boat', loa_m: 8.2,
          confidence: 'high', escalate: false, escalation_message: null, data_age_hours: 0,
          thresholds_version: 'test', evaluated_at: now, evidence: [{
            dataset_id: 'test-cape', provider: 'test', variable: 'convective_energy', value: 120,
            unit: 'J/kg', provenance: 'live', freshness: { valid_time: now, retrieved_at: now, age_hours: 0, is_stale: false, stale_after: now, note: null },
            lineage: [], url: null, location: [13.1, 80.4], method: null, uncertainty: null,
            citations: [], notes: null,
          }], citations: [],
          what_would_change_it: ['old future-facing suggestion'], disclaimer: 'test only',
        });
      case '/api/geofence/check':
        geofenceRequests += 1;
        return json(route, {
          position: { lat: 13.1, lon: 80.4 }, heading_deg: null, speed_kn: null,
          generated_at: now, fences_in_range: 1, proximities: [{
            fence: 'eez_india', name: 'Indian Exclusive Economic Zone', kind: 'eez', inside: true,
            distance_km: 183.4, bearing_deg: 90, compass: 'E', nearest_point: { lat: 13.1, lon: 82 },
            state: 'inside', time_to_cross_min: null, closing: null,
            consequence: 'test consequence', authority: 'test authority', narrative: 'test narrative',
          }], transitions: [], states: { eez_india: 'inside' },
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
      case '/api/imagery/sentinel/preview':
        const collection = url.searchParams.get('collection');
        return route.fulfill({
          status: 200,
          contentType: 'image/png',
          headers: {
            'X-ORCA-Acquired-At': '2026-09-01T04:45:44Z',
            'X-ORCA-Provenance': 'live',
            'X-ORCA-Source': collection === 'sentinel-2-l2a' ? 'Copernicus Sentinel-2 L2A' : 'Copernicus Sentinel-3 OLCI',
            'X-ORCA-Satellite': collection === 'sentinel-2-l2a' ? 'sentinel-2b' : 'sentinel-3a',
            'X-ORCA-Resolution-M': collection === 'sentinel-2-l2a' ? '10' : '300',
            ...(collection === 'sentinel-2-l2a' ? { 'X-ORCA-Cloud-Cover': '12.5' } : {}),
          },
          body: Buffer.from(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
            'base64',
          ),
        });
      case '/api/imagery/nasa/preview':
        return route.fulfill({
          status: 200,
          contentType: 'image/png',
          headers: {
            'X-ORCA-Observation-Date': '2026-09-01',
            'X-ORCA-Time-Precision': 'date',
            'X-ORCA-Provenance': 'live',
            'X-ORCA-Source': 'NASA GIBS corrected reflectance',
            'X-ORCA-Satellite': 'NOAA-21',
            'X-ORCA-Instrument': 'VIIRS',
            'X-ORCA-Resolution-M': '750',
          },
          body: Buffer.from(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
            'base64',
          ),
        });
      default:
        return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"mock missing"}' });
    }
  });

  await page.goto('/');
  await page.getByRole('button', { name: /Chennai test point/ }).click();
  await expect.poll(() => [...requested]).toContain('/api/satellites/overpasses');
  await page.waitForTimeout(500);
  expect(applicationErrors(pageErrors)).toEqual([]);

  const topControlBoxes = await Promise.all([
    page.getByRole('button', { name: 'Alerts' }).boundingBox(),
    page.getByRole('button', { name: 'Visual treatments' }).boundingBox(),
    page.getByRole('button', { name: 'Orbital & vessel intelligence' }).boundingBox(),
  ]);
  expect(topControlBoxes.every(Boolean)).toBe(true);
  for (let left = 0; left < topControlBoxes.length; left += 1) {
    for (let right = left + 1; right < topControlBoxes.length; right += 1) {
      const a = topControlBoxes[left]!;
      const b = topControlBoxes[right]!;
      const overlap =
        a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
      expect(overlap).toBe(false);
    }
  }

  await expect(page.getByText('Current-condition analysis')).toBeVisible();

  await page.getByRole('button', { name: 'Close Ask ORCA chat' }).click();
  await expect(page.getByRole('button', { name: 'Open Ask ORCA chat' })).toBeVisible();
  await expect(page.getByText('Ask ORCA')).toBeHidden();
  const shiftedAlertBox = await page.getByRole('button', { name: 'Alerts' }).boundingBox();
  expect(shiftedAlertBox).not.toBeNull();
  expect(shiftedAlertBox!.x).toBeLessThan(topControlBoxes[0]!.x);

  await page.getByRole('button', { name: /Sea view/ }).click();
  const seaView = page.locator('[data-orca="sea-state-panel"]');
  await seaView.getByRole('button', { name: /Sentinel-3 OLCI/ }).click();
  await expect(
    seaView.getByRole('img', { name: /Copernicus Sentinel-3 OLCI observation/ }),
  ).toBeVisible();
  await expect(seaView.getByText('Copernicus Sentinel-3 OLCI', { exact: true })).toBeVisible();
  await expect(seaView.getByText(/Acquired .*Sep.*2026/)).toBeVisible();
  await seaView.getByRole('button', { name: /NASA VIIRS\/MODIS/ }).click();
  await expect(seaView.getByRole('img', { name: /NASA GIBS corrected reflectance/ })).toBeVisible();
  await expect(seaView.getByText(/NOAA-21 · VIIRS · 750 m/)).toBeVisible();
  await seaView.getByRole('button', { name: /Sentinel-2 L2A/ }).click();
  await expect(seaView.getByRole('img', { name: /Copernicus Sentinel-2 L2A/ })).toBeVisible();
  await expect(seaView.getByText(/scene cloud 12.5%/)).toBeVisible();
  await expect(seaView.getByText('not live video')).toBeVisible();
  await seaView.getByRole('button', { name: 'Close sea view' }).click();

  await page.getByRole('button', { name: 'Open Ask ORCA chat' }).click();
  await expect(page.getByText('Ask ORCA')).toBeVisible();

  await expect(page.getByText('Significant wave height')).toBeVisible();
  await expect(page.getByText("Inside India's EEZ")).toBeVisible();
  await expect(page.getByText('test only')).toHaveCount(0);
  await expect(page.getByText('old future-facing suggestion')).toHaveCount(0);
  await expect(page.getByText(/evidence items/i)).toHaveCount(0);
  await page.getByRole('button', { name: 'Collapse safety verdict horizontally' }).click();
  await expect(page.getByRole('button', { name: 'Expand safety verdict horizontally' })).toBeVisible();
  await expect(page.getByText('Current-condition analysis')).toBeHidden();
  await page.getByRole('button', { name: 'Expand safety verdict horizontally' }).click();
  await expect(page.getByText('Current-condition analysis')).toBeVisible();

  await expect(page.getByText('Evidence — how ORCA knows')).toBeVisible();
  await page.getByRole('button', { name: 'Collapse evidence panel horizontally' }).click();
  await expect(page.getByRole('button', { name: 'Expand evidence panel horizontally' })).toBeVisible();
  await expect(page.getByText('Evidence — how ORCA knows')).toBeHidden();
  await page.getByRole('button', { name: 'Expand evidence panel horizontally' }).click();
  await expect(page.getByText('Evidence — how ORCA knows')).toBeVisible();
  await expect(page.getByText(/deterministic rule engine/i)).toHaveCount(0);

  await page.getByRole('button', { name: /Orbital & vessel intelligence/ }).click();
  await expect(page.getByText(/Sentinel-3A/).first()).toBeVisible();
  await expect(page.getByText(/Opportunity only/)).toBeVisible();
  const intelPanelBox = await page.getByText('Next nominal swath crossings').boundingBox();
  expect(intelPanelBox).not.toBeNull();
  expect(intelPanelBox!.y).toBeGreaterThan(
    topControlBoxes[2]!.y + topControlBoxes[2]!.height,
  );

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
    '/api/imagery/sentinel/preview', '/api/imagery/nasa/preview',
  ]));

  await page.getByRole('button', { name: 'Close marine intelligence' }).click();
  await page.getByRole('button', { name: 'Close Ask ORCA chat' }).click();
  const geofenceRequestsBeforeLand = geofenceRequests;
  await page.evaluate(async () => {
    const map = (window as unknown as {
      __orcaMap: {
        jumpTo: (options: { center: [number, number]; zoom: number }) => void;
        areTilesLoaded: () => boolean;
        once: (event: string, callback: () => void) => void;
      };
    }).__orcaMap;
    map.jumpTo({ center: [78.5, 22.5], zoom: 7 });
    if (!map.areTilesLoaded()) {
      await new Promise<void>((resolve) => map.once('idle', resolve));
    }
  });
  const mapBox = await page.locator('.maplibregl-map').boundingBox();
  expect(mapBox).not.toBeNull();
  await page.mouse.click(mapBox!.x + mapBox!.width / 2, mapBox!.y + mapBox!.height / 2);
  await expect(page.getByText('LAND POINT')).toBeVisible();
  await expect(page.getByText('Atmospheric analysis')).toBeVisible();
  await expect(page.getByText('Land observation')).toBeVisible();
  await expect(page.getByText('Wind speed').first()).toBeVisible();
  await expect(page.getByText(/maritime EEZ status are intentionally omitted/)).toBeVisible();
  await expect(page.getByText('Significant wave height')).toHaveCount(0);
  await expect(page.getByText(/Outside India's EEZ/)).toHaveCount(0);
  expect(geofenceRequests).toBe(geofenceRequestsBeforeLand);
  expect(applicationErrors(pageErrors)).toEqual([]);
});
