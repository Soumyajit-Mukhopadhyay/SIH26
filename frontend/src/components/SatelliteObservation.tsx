import { AlertTriangle, Crosshair, Loader2, RefreshCw, Satellite } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import type { PointForecast } from '@/lib/types';

export type SatelliteView = 'sentinel3' | 'nasa' | 'sentinel2';

interface Snapshot {
  url: string;
  acquiredAt: string | null;
  observationDate: string | null;
  provenance: string | null;
  source: string;
  satellite: string | null;
  instrument: string | null;
  resolutionM: number | null;
  cloudCover: number | null;
  timePrecision: string | null;
}

const VIEW = {
  sentinel3: {
    fallbackSource: 'Copernicus Sentinel-3 OLCI',
    loading: 'Requesting the latest Sentinel-3 ocean observation…',
    window: 'the last seven days',
    caveat:
      'Ocean-colour true-colour view at 300 m native resolution. Clouds may hide the sea; colour does not measure wave height.',
  },
  nasa: {
    fallbackSource: 'NASA GIBS corrected reflectance',
    loading: 'Finding the newest NASA VIIRS or MODIS swath…',
    window: 'the last three days',
    caveat:
      'Near-real-time daily browse imagery. GIBS provides the observation date here, not an exact local overpass time.',
  },
  sentinel2: {
    fallbackSource: 'Copernicus Sentinel-2 L2A',
    loading: 'Requesting a recent low-cloud Sentinel-2 observation…',
    window: 'the last 45 days',
    caveat:
      'High-resolution 10 m coastal context. It is less frequent than Sentinel-3 and is not a present-condition camera.',
  },
} as const;

function observedAt(value: string | null, dateOnly = false): string {
  if (!value) return dateOnly ? 'observation date unavailable' : 'acquisition time unavailable';
  const date = new Date(dateOnly ? `${value}T00:00:00Z` : value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    ...(dateOnly
      ? { timeZone: 'UTC' }
      : { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }),
  }).format(date);
}

function ageOf(value: string | null, dateOnly = false): string | null {
  if (!value) return null;
  const date = new Date(dateOnly ? `${value}T23:59:59Z` : value);
  const milliseconds = Date.now() - date.getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return null;
  const hours = milliseconds / 3_600_000;
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min old`;
  if (hours < 48) return `${hours.toFixed(hours < 10 ? 1 : 0)} h old`;
  return `${(hours / 24).toFixed(1)} days old`;
}

function finiteHeader(response: Response, name: string): number | null {
  const raw = response.headers.get(name);
  if (raw === null || raw.trim() === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

async function responseDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch {
    // The upstream can return plain text or an empty body. The status remains useful.
  }
  return `Satellite service returned HTTP ${response.status}`;
}

export function SatelliteObservation({
  forecast,
  view,
}: {
  forecast: PointForecast | null;
  view: SatelliteView;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const lat = forecast?.lat ?? null;
  const lon = forecast?.lon ?? null;
  const config = VIEW[view];

  const requestUrl = useMemo(() => {
    if (lat === null || lon === null) return null;
    if (view === 'nasa') return api.nasaGibsPreviewUrl(lat, lon, 3, 768);
    return api.sentinelPreviewUrl(
      lat,
      lon,
      view === 'sentinel2' ? 'sentinel-2-l2a' : 'sentinel-3-olci',
      view === 'sentinel2' ? 45 : 7,
      768,
    );
  }, [lat, lon, view]);

  useEffect(() => {
    if (!requestUrl) {
      setSnapshot(null);
      setError('Select a sea point before requesting satellite imagery.');
      return;
    }

    const controller = new AbortController();
    let objectUrl: string | null = null;
    setSnapshot(null);
    setLoading(true);
    setError(null);

    void fetch(requestUrl, {
      signal: controller.signal,
      // A manual refresh must reach ORCA rather than replaying the browser's
      // 15-minute cached response when a newer overpass may have arrived.
      cache: 'no-store',
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseDetail(response));
        const blob = await response.blob();
        if (!blob.type.startsWith('image/')) {
          throw new Error('Satellite service returned something other than an image.');
        }
        objectUrl = URL.createObjectURL(blob);
        setSnapshot({
          url: objectUrl,
          acquiredAt: response.headers.get('X-ORCA-Acquired-At'),
          observationDate: response.headers.get('X-ORCA-Observation-Date'),
          provenance: response.headers.get('X-ORCA-Provenance'),
          source: response.headers.get('X-ORCA-Source') ?? config.fallbackSource,
          satellite: response.headers.get('X-ORCA-Satellite'),
          instrument: response.headers.get('X-ORCA-Instrument'),
          resolutionM: finiteHeader(response, 'X-ORCA-Resolution-M'),
          cloudCover: finiteHeader(response, 'X-ORCA-Cloud-Cover'),
          timePrecision: response.headers.get('X-ORCA-Time-Precision'),
        });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setSnapshot(null);
        setError(cause instanceof Error ? cause.message : 'Satellite preview failed.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [config.fallbackSource, refreshKey, requestUrl]);

  const isDateOnly = snapshot?.timePrecision === 'date' || !snapshot?.acquiredAt;
  const observedValue = snapshot?.acquiredAt ?? snapshot?.observationDate ?? null;
  const age = ageOf(observedValue, isDateOnly);
  const sourceDetails = snapshot
    ? [snapshot.satellite, snapshot.instrument, snapshot.resolutionM && `${snapshot.resolutionM} m`]
        .filter(Boolean)
        .join(' · ')
    : '';

  return (
    <div className="bg-abyss-0 relative h-full overflow-hidden">
      {loading && (
        <div className="text-ink-1 absolute inset-0 flex flex-col items-center justify-center gap-2 text-xs">
          <Loader2 className="text-cyan h-5 w-5 animate-spin" aria-hidden />
          <span>{config.loading}</span>
        </div>
      )}

      {!loading && error && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-8 text-center">
          <AlertTriangle className="text-amber h-5 w-5" aria-hidden />
          <div>
            <p className="text-ink-0 text-xs">No satellite preview could be displayed</p>
            <p className="text-ink-2 mt-1 text-2xs leading-snug">{error}</p>
            <p className="text-ink-3 mt-1 text-[10px]">Searched {config.window}.</p>
          </div>
          <button
            type="button"
            onClick={() => setRefreshKey((value) => value + 1)}
            className="border-cyan/35 text-cyan flex items-center gap-1 rounded border px-2 py-1 text-2xs"
          >
            <RefreshCw className="h-3 w-3" aria-hidden /> retry
          </button>
        </div>
      )}

      {!loading && snapshot && lat !== null && lon !== null && (
        <>
          <img
            src={snapshot.url}
            alt={`${snapshot.source} observation centred on ${lat.toFixed(3)} degrees north, ${lon.toFixed(3)} degrees east`}
            className="h-full w-full object-cover"
          />
          <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_bottom,rgba(3,12,20,0.5),transparent_28%,transparent_58%,rgba(3,12,20,0.92))]" />

          <div className="pointer-events-none absolute top-2 left-2 rounded bg-black/70 px-2 py-1 backdrop-blur-sm">
            <div className="flex items-center gap-1.5">
              <Satellite className="text-cyan h-3 w-3" aria-hidden />
              <span className="text-ink-0 text-[10px] font-semibold tracking-wide uppercase">
                {snapshot.source}
              </span>
            </div>
            {sourceDetails && <div className="text-ink-1 mt-0.5 text-[9px]">{sourceDetails}</div>}
          </div>

          <button
            type="button"
            onClick={() => setRefreshKey((value) => value + 1)}
            className="bg-abyss-0/75 text-ink-1 hover:text-cyan absolute top-2 right-2 rounded p-1.5 backdrop-blur-sm transition-colors"
            aria-label={`Refresh ${snapshot.source} image`}
            title="Check for a newer acquisition"
          >
            <RefreshCw className="h-3 w-3" aria-hidden />
          </button>

          <div className="pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
            <Crosshair
              className="text-red h-6 w-6 drop-shadow-[0_1px_2px_rgba(0,0,0,0.9)]"
              aria-hidden
            />
            <span className="sr-only">Selected point at image centre</span>
          </div>

          <div className="pointer-events-none absolute right-3 bottom-2 left-3">
            <div className="flex items-end justify-between gap-3">
              <div>
                <div className="text-ink-0 text-xs font-medium">
                  {isDateOnly ? 'Observation date' : 'Acquired'}{' '}
                  {observedAt(observedValue, isDateOnly)}
                </div>
                <div className="text-ink-1 mt-0.5 text-2xs">
                  {age ?? 'age unavailable'}
                  {snapshot.cloudCover !== null
                    ? ` · scene cloud ${snapshot.cloudCover.toFixed(1)}%`
                    : ''}
                  {' · '}
                  {snapshot.provenance ?? 'unknown fetch'}
                </div>
              </div>
              <span className="border-amber/45 bg-amber/12 text-amber shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase">
                not live video
              </span>
            </div>
            <p className="text-ink-2 mt-1 text-[10px] leading-snug">{config.caveat}</p>
          </div>
        </>
      )}
    </div>
  );
}
