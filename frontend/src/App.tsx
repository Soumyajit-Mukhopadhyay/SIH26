/**
 * The operational console.
 *
 * Layout: a full-bleed globe with the freshness strip across the top, the
 * location/vessel rail on the left, and the verdict plus evidence on the right.
 * Panels float over an unframed map — the map is the document, the panels are
 * instruments laid on it.
 *
 * The wiring that matters: clicking the sea fetches the forecast and the
 * deterministic verdict together, so the number on the card and the numbers in
 * the evidence panel are the same fetch and cannot disagree.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import type { Layer } from '@deck.gl/core';
import { Anchor, Crosshair, Loader2, MapPin, Ruler, Waves } from 'lucide-react';
import { clsx } from 'clsx';
import { api, ApiError } from '@/lib/api';
import type {
  FreshnessReport,
  Health,
  Landmark,
  PointForecast,
  RiskResult,
  ThresholdTable,
} from '@/lib/types';
import { AOI, HOME_VIEW, OceanMap, type OceanMapHandle } from '@/components/OceanMap';
import { FreshnessStrip } from '@/components/FreshnessStrip';
import { VerdictCard } from '@/components/VerdictCard';
import { EvidencePanel } from '@/components/EvidencePanel';

/**
 * The AOI outline as a densified ring.
 *
 * A four-point rectangle is wrong on a globe: each edge renders as a straight
 * chord rather than following the parallel or meridian, so the box visibly does
 * not match the coverage it claims. Sampling every 2 degrees fixes it.
 */
function aoiRing(step = 2): [number, number][] {
  const ring: [number, number][] = [];
  for (let lon = AOI.west; lon <= AOI.east; lon += step) ring.push([lon, AOI.south]);
  for (let lat = AOI.south; lat <= AOI.north; lat += step) ring.push([AOI.east, lat]);
  for (let lon = AOI.east; lon >= AOI.west; lon -= step) ring.push([lon, AOI.north]);
  for (let lat = AOI.north; lat >= AOI.south; lat -= step) ring.push([AOI.west, lat]);
  ring.push([AOI.west, AOI.south]);
  return ring;
}

interface Selection {
  lon: number;
  lat: number;
  label?: string;
}

export default function App() {
  const mapRef = useRef<OceanMapHandle>(null);

  const [health, setHealth] = useState<Health | null>(null);
  const [freshness, setFreshness] = useState<FreshnessReport | null>(null);
  const [landmarks, setLandmarks] = useState<Landmark[]>([]);
  const [thresholds, setThresholds] = useState<ThresholdTable | null>(null);

  const [selection, setSelection] = useState<Selection | null>(null);
  const [forecast, setForecast] = useState<PointForecast | null>(null);
  const [risk, setRisk] = useState<RiskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [loaM, setLoaM] = useState(8.2);

  // ---- boot ----
  useEffect(() => {
    void (async () => {
      const [h, f, l, t] = await Promise.allSettled([
        api.health(),
        api.freshness(),
        api.landmarks(),
        api.thresholds(),
      ]);
      if (h.status === 'fulfilled') setHealth(h.value);
      if (f.status === 'fulfilled') setFreshness(f.value);
      if (l.status === 'fulfilled') setLandmarks(l.value.landmarks);
      if (t.status === 'fulfilled') setThresholds(t.value);
      if (h.status === 'rejected') {
        setError('Backend unreachable. Start it with scripts\\dev.ps1.');
      }
    })();
  }, []);

  // The freshness strip is only honest if it keeps up with reality.
  useEffect(() => {
    const id = setInterval(() => {
      void api.freshness().then(setFreshness).catch(() => undefined);
    }, 15_000);
    return () => clearInterval(id);
  }, []);

  // ---- the core interaction ----
  const query = useCallback(
    async (lon: number, lat: number, label?: string, loa = loaM) => {
      setSelection({ lon, lat, label });
      setLoading(true);
      setError(null);
      try {
        // Fetched together on purpose: the verdict and the evidence panel must
        // be reading the same numbers, or the card and the panel could disagree
        // on screen, which would be worse than either being slightly stale.
        const [pointForecast, verdict] = await Promise.all([
          api.forecastPoint(lat, lon),
          api.assessRisk(lat, lon, loa),
        ]);
        setForecast(pointForecast);
        setRisk(verdict);
        void api.freshness().then(setFreshness).catch(() => undefined);
      } catch (cause) {
        const message =
          cause instanceof ApiError
            ? cause.status === 0
              ? 'Backend unreachable — ORCA is offline.'
              : cause.detail
            : 'Unexpected failure.';
        setError(message);
        setForecast(null);
        setRisk(null);
      } finally {
        setLoading(false);
      }
    },
    [loaM],
  );

  // Re-run when the vessel changes: the same sea is a different verdict for a
  // canoe and a trawler, and that is the point worth demonstrating.
  useEffect(() => {
    if (selection) void query(selection.lon, selection.lat, selection.label, loaM);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaM]);

  // ---- layers ----
  const layers = useMemo<Layer[]>(() => {
    const out: Layer[] = [];

    // The AOI envelope. ORCA states the area it actually covers rather than
    // implying global competence, so this boundary needs to be legible — the
    // first version was alpha 46 and invisible, which made the honesty
    // invisible too. Drawn as a densified path so it curves correctly under the
    // globe projection instead of cutting a chord across it.
    out.push(
      new PathLayer<{ path: [number, number][] }>({
        id: 'aoi-boundary',
        data: [{ path: aoiRing() }],
        getPath: (d) => d.path,
        getColor: [34, 211, 238, 120],
        getWidth: 1.5,
        widthUnits: 'pixels',
        widthMinPixels: 1,
        jointRounded: true,
        capRounded: true,
        pickable: false,
      }),
    );

    if (landmarks.length > 0) {
      out.push(
        new ScatterplotLayer<Landmark>({
          id: 'landmarks',
          data: landmarks,
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 4,
          radiusUnits: 'pixels',
          radiusMinPixels: 3,
          getFillColor: [148, 168, 187, 190],
          getLineColor: [11, 23, 37, 255],
          lineWidthMinPixels: 1,
          stroked: true,
          pickable: true,
          onClick: ({ object }) => {
            if (object) void query(object.lon, object.lat, object.label);
          },
        }),
      );
    }

    if (selection) {
      const colour: [number, number, number] =
        risk?.verdict === 'GO'
          ? [52, 211, 153]
          : risk?.verdict === 'CAUTION'
            ? [245, 158, 11]
            : risk?.verdict === 'NO-GO'
              ? [239, 68, 68]
              : [34, 211, 238];

      out.push(
        new ScatterplotLayer<Selection>({
          id: 'selection-halo',
          data: [selection],
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 18,
          radiusUnits: 'pixels',
          getFillColor: [...colour, 40],
          pickable: false,
        }),
        new ScatterplotLayer<Selection>({
          id: 'selection',
          data: [selection],
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 6,
          radiusUnits: 'pixels',
          getFillColor: [...colour, 255],
          getLineColor: [4, 9, 15, 255],
          lineWidthMinPixels: 2,
          stroked: true,
          pickable: false,
          updateTriggers: { getFillColor: risk?.verdict },
        }),
      );
    }

    return out;
  }, [landmarks, selection, risk?.verdict, query]);

  const activeClass = useMemo(
    () => thresholds?.classes.find((c) => loaM >= c.loa_range_m[0] && loaM < c.loa_range_m[1]),
    [thresholds, loaM],
  );

  return (
    <div className="bg-abyss-0 flex h-full flex-col">
      <FreshnessStrip health={health} freshness={freshness} />

      <div className="relative flex-1 overflow-hidden">
        <OceanMap
          ref={mapRef}
          layers={layers}
          onClick={(lon, lat) => void query(lon, lat)}
          className="absolute inset-0"
        />

        {/* ---------------- left rail: place and vessel ---------------- */}
        <div className="pointer-events-none absolute top-3 left-3 z-20 flex w-64 flex-col gap-2">
          <div className="glass pointer-events-auto rounded-lg">
            <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
              <MapPin className="text-cyan h-3.5 w-3.5" aria-hidden />
              <span className="label">Location</span>
              <button
                type="button"
                onClick={() => mapRef.current?.resetView()}
                className="text-ink-2 hover:text-cyan ml-auto text-2xs transition-colors"
                title={`Return to ${HOME_VIEW.center[1]}°N ${HOME_VIEW.center[0]}°E`}
              >
                reset
              </button>
            </div>
            <div className="max-h-52 overflow-y-auto p-1.5">
              {landmarks.map((place) => {
                const active =
                  selection &&
                  Math.abs(selection.lat - place.lat) < 1e-6 &&
                  Math.abs(selection.lon - place.lon) < 1e-6;
                return (
                  <button
                    key={place.key}
                    type="button"
                    onClick={() => {
                      mapRef.current?.flyTo(place.lon, place.lat, 8);
                      void query(place.lon, place.lat, place.label);
                    }}
                    className={clsx(
                      'flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors',
                      active ? 'bg-cyan/12 text-cyan' : 'text-ink-1 hover:bg-abyss-2',
                    )}
                  >
                    <Anchor className="h-3 w-3 shrink-0 opacity-60" aria-hidden />
                    <span className="truncate">{place.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="glass pointer-events-auto rounded-lg">
            <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
              <Ruler className="text-cyan h-3.5 w-3.5" aria-hidden />
              <span className="label">Your vessel</span>
            </div>
            <div className="p-3">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="data text-ink-0 text-lg">{loaM.toFixed(1)} m</span>
                <span className="text-ink-2 text-2xs">length overall</span>
              </div>
              <input
                type="range"
                min={3}
                max={30}
                step={0.1}
                value={loaM}
                onChange={(event) => setLoaM(Number(event.target.value))}
                className="accent-cyan w-full"
                aria-label="Boat length overall in metres"
              />
              {activeClass && (
                <div className="border-hairline mt-2 border-t pt-2">
                  <div className="text-ink-1 text-2xs leading-snug">{activeClass.label}</div>
                  <div className="data text-ink-2 mt-1 flex gap-3 text-2xs">
                    <span>
                      Hs&nbsp;<span className="text-ink-0">{activeClass.max_wave_m} m</span>
                    </span>
                    <span>
                      wind&nbsp;<span className="text-ink-0">{activeClass.max_wind_kn} kn</span>
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ---------------- right: verdict + evidence ---------------- */}
        <div className="pointer-events-none absolute top-3 right-3 bottom-3 z-20 flex w-[24rem] flex-col gap-2">
          {loading && !risk && (
            <div className="glass pointer-events-auto flex items-center gap-2 rounded-lg px-4 py-3">
              <Loader2 className="text-cyan h-4 w-4 animate-spin" aria-hidden />
              <span className="text-ink-1 text-xs">
                Fetching live conditions and computing the verdict…
              </span>
            </div>
          )}

          {error && (
            <div className="glass border-red/40 pointer-events-auto rounded-lg border px-4 py-3">
              <div className="label text-red mb-1">Request failed</div>
              <p className="text-ink-1 text-xs leading-snug">{error}</p>
            </div>
          )}

          {risk && (
            <div className="pointer-events-auto">
              <VerdictCard result={risk} />
            </div>
          )}

          {forecast && (
            <div className="glass pointer-events-auto min-h-0 flex-1 overflow-y-auto rounded-lg">
              <div className="glass border-hairline sticky top-0 z-10 flex items-center gap-1.5 border-b px-3 py-2">
                <Waves className="text-cyan h-3.5 w-3.5" aria-hidden />
                <span className="label">Evidence — how ORCA knows</span>
                {loading && <Loader2 className="text-cyan ml-auto h-3 w-3 animate-spin" aria-hidden />}
              </div>
              <EvidencePanel forecast={forecast} />
            </div>
          )}
        </div>

        {/* ---------------- empty state ---------------- */}
        {!selection && !loading && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
            <div className="glass pointer-events-auto max-w-md rounded-lg p-5 text-center">
              <Crosshair className="text-cyan mx-auto mb-3 h-6 w-6" aria-hidden />
              <h1 className="text-ink-0 mb-1.5 text-base font-semibold">
                Click anywhere on the sea
              </h1>
              <p className="text-ink-1 text-xs leading-relaxed">
                ORCA pulls live wave, wind, visibility and convective data for that point, computes
                a GO / NO-GO with a deterministic rule engine, and shows you every number it used —
                with its source, its age and its provenance state.
              </p>
              <p className="text-ink-3 mt-3 text-2xs leading-snug">
                Coverage is the Indian EEZ envelope, {AOI.west}–{AOI.east}°E and {AOI.south}–
                {AOI.north}°N, outlined in cyan.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
