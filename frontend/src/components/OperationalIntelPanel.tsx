import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Database, Loader2, Radio, Satellite, ShieldCheck, Ship } from 'lucide-react';
import { clsx } from 'clsx';
import { api } from '@/lib/api';
import type {
  AisSnapshot,
  CrossValidationResponse,
  FishingEffortResponse,
  NasaGranuleSearch,
  OverpassResponse,
  SentinelCatalogueSearch,
  ValidationStatus,
} from '@/lib/types';

interface Point {
  lat: number;
  lon: number;
}

interface Props {
  point: Point | null;
}

const STATUS_CLASS: Record<ValidationStatus, string> = {
  agree: 'text-green',
  disagree: 'text-red',
  inconclusive: 'text-amber',
  unavailable: 'text-ink-3',
};

function when(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(new Date(value));
}

export function OperationalIntelPanel({ point }: Props) {
  const pointKey = point ? `${point.lat}:${point.lon}` : '';
  const currentPointKey = useRef(pointKey);
  currentPointKey.current = pointKey;
  const [open, setOpen] = useState(false);
  const [overpasses, setOverpasses] = useState<OverpassResponse | null>(null);
  const [validation, setValidation] = useState<CrossValidationResponse | null>(null);
  const [ais, setAis] = useState<AisSnapshot | null>(null);
  const [fishing, setFishing] = useState<FishingEffortResponse | null>(null);
  const [nasa, setNasa] = useState<NasaGranuleSearch | null>(null);
  const [sentinel, setSentinel] = useState<SentinelCatalogueSearch | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setOverpasses(null);
    setValidation(null);
    setAis(null);
    setFishing(null);
    setNasa(null);
    setSentinel(null);
    setError(null);
    if (!point) return () => undefined;
    setBusy('overpass');
    void api
      .overpasses(point.lat, point.lon)
      .then((response) => {
        if (active) setOverpasses(response);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Overpass request failed');
      })
      .finally(() => {
        if (active) setBusy(null);
      });
    return () => {
      active = false;
    };
  }, [point?.lat, point?.lon]);

  const next = overpasses?.passes[0] ?? null;

  async function validate() {
    if (!point) return;
    const requestPointKey = pointKey;
    setBusy('validation');
    setError(null);
    try {
      const response = await api.validatePoint(point.lat, point.lon, true);
      if (currentPointKey.current === requestPointKey) setValidation(response);
    } catch (cause) {
      if (currentPointKey.current === requestPointKey) {
        setError(cause instanceof Error ? cause.message : 'Validation request failed');
      }
    } finally {
      if (currentPointKey.current === requestPointKey) setBusy(null);
    }
  }

  async function scanAis() {
    if (!point) return;
    const requestPointKey = pointKey;
    setBusy('ais');
    setError(null);
    try {
      const response = await api.aisSnapshot(point.lat, point.lon);
      if (currentPointKey.current === requestPointKey) setAis(response);
    } catch (cause) {
      if (currentPointKey.current === requestPointKey) {
        setError(cause instanceof Error ? cause.message : 'AIS request failed');
      }
    } finally {
      if (currentPointKey.current === requestPointKey) setBusy(null);
    }
  }

  async function loadFishing() {
    if (!point) return;
    const requestPointKey = pointKey;
    setBusy('fishing');
    setError(null);
    try {
      const response = await api.fishingEffort(point.lat, point.lon);
      if (currentPointKey.current === requestPointKey) setFishing(response);
    } catch (cause) {
      if (currentPointKey.current === requestPointKey) {
        setError(cause instanceof Error ? cause.message : 'Fishing-effort request failed');
      }
    } finally {
      if (currentPointKey.current === requestPointKey) setBusy(null);
    }
  }

  async function searchArchives() {
    if (!point) return;
    const requestPointKey = pointKey;
    setBusy('archives');
    setError(null);
    const [nasaResult, sentinelResult] = await Promise.allSettled([
      api.nasaCatalogue(point.lat, point.lon),
      api.sentinelCatalogue(point.lat, point.lon),
    ]);
    if (currentPointKey.current !== requestPointKey) return;
    if (nasaResult.status === 'fulfilled') setNasa(nasaResult.value);
    if (sentinelResult.status === 'fulfilled') setSentinel(sentinelResult.value);
    const failures = [nasaResult, sentinelResult]
      .filter((result) => result.status === 'rejected')
      .map((result) => String((result as PromiseRejectedResult).reason));
    if (failures.length) setError(failures.join(' · '));
    setBusy(null);
  }

  return (
    <div className="glass pointer-events-auto w-[22rem] rounded-lg">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        disabled={!point}
      >
        <Satellite className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">Orbital &amp; vessel intelligence</span>
        <span className="text-ink-2 ml-auto max-w-40 truncate text-2xs">
          {!point
            ? 'select sea'
            : busy === 'overpass'
              ? 'calculating…'
              : next
                ? `${next.satellite} · ${when(next.closest_time)}`
                : overpasses?.unavailable_satellites.length
                  ? 'orbit data incomplete'
                : 'no pass in 48 h'}
        </span>
        <ChevronDown className={clsx('h-3 w-3 transition-transform', open && 'rotate-180')} />
      </button>

      {open && point && (
        <div className="border-hairline max-h-[34rem] space-y-3 overflow-y-auto border-t p-3">
          <section>
            <div className="text-ink-1 mb-1 flex items-center gap-1.5 text-2xs font-semibold">
              <Satellite className="h-3 w-3" /> Next nominal swath crossings
            </div>
            {overpasses?.passes.slice(0, 3).map((pass) => (
              <div key={`${pass.norad_id}-${pass.closest_time}`} className="border-hairline border-b py-1.5 text-2xs">
                <div className="text-ink-0 flex justify-between gap-2">
                  <span>{pass.satellite}</span>
                  <span className="data">{pass.closest_distance_km.toFixed(0)} km</span>
                </div>
                <div className="text-ink-2 mt-0.5">
                  {when(pass.closest_time)} · {pass.daylight_at_target ? 'daylight' : 'night'} · TLE{' '}
                  {pass.tle_age_hours.toFixed(1)} h old
                </div>
              </div>
            ))}
            {overpasses &&
              !overpasses.passes.length &&
              !overpasses.unavailable_satellites.length && (
              <p className="text-ink-2 text-2xs">No nominal swath crossing in this 48-hour window.</p>
            )}
            {overpasses?.unavailable_satellites.map((failure) => (
              <p key={failure} className="text-red mt-1 text-[10px] leading-snug">
                {failure}
              </p>
            ))}
            <p className="text-ink-3 mt-1.5 text-[10px] leading-snug">
              Opportunity only—not proof of tasking, acquisition, or cloud-free imagery.
            </p>
          </section>

          <section>
            <button type="button" onClick={() => void validate()} disabled={busy !== null} className="text-cyan flex items-center gap-1.5 text-2xs">
              {busy === 'validation' ? <Loader2 className="h-3 w-3 animate-spin" /> : <ShieldCheck className="h-3 w-3" />}
              Cross-validate SST, wind and waves
            </button>
            {validation?.checks.map((check) => (
              <div key={check.variable} className="mt-1.5 text-2xs">
                <span className={STATUS_CLASS[check.status]}>{check.status.toUpperCase()}</span>{' '}
                <span className="text-ink-1">{check.variable.replaceAll('_', ' ')}</span>
                <p className="text-ink-3 mt-0.5 leading-snug">{check.message}</p>
              </div>
            ))}
          </section>

          <section className="grid grid-cols-2 gap-2">
            <button type="button" onClick={() => void scanAis()} disabled={busy !== null} className="border-hairline text-cyan flex items-center justify-center gap-1 rounded border px-2 py-1.5 text-2xs">
              {busy === 'ais' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Radio className="h-3 w-3" />} Live AIS scan
            </button>
            <button type="button" onClick={() => void loadFishing()} disabled={busy !== null} className="border-hairline text-cyan flex items-center justify-center gap-1 rounded border px-2 py-1.5 text-2xs">
              {busy === 'fishing' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Ship className="h-3 w-3" />} GFW history
            </button>
          </section>
          {ais?.connected && (
            <p className="text-ink-2 text-2xs leading-snug">
              AISStream: {ais.vessels.length} vessel{ais.vessels.length === 1 ? '' : 's'} in {ais.duration_seconds.toFixed(1)} s. {ais.vessels.length === 0 && 'Sparse coverage means zero is not proof of empty water.'}
            </p>
          )}
          {ais && !ais.connected && (
            <p className="text-red text-2xs leading-snug">
              AISStream unavailable: {ais.error ?? ais.coverage_note}
            </p>
          )}
          {fishing && (
            <p className="text-ink-2 text-2xs leading-snug">
              GFW:{' '}
              {fishing.available
                ? `${fishing.total_apparent_fishing_hours.toFixed(1)} apparent fishing h from ${fishing.vessel_count} vessels · through ${fishing.end_date}.`
                : `unavailable · ${fishing.error ?? fishing.caveat}`}
            </p>
          )}

          <section>
            <button type="button" onClick={() => void searchArchives()} disabled={busy !== null} className="text-cyan flex items-center gap-1.5 text-2xs">
              {busy === 'archives' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Database className="h-3 w-3" />}
              Search NASA &amp; Sentinel archives
            </button>
            {(nasa || sentinel) && (
              <p className="text-ink-2 mt-1 text-2xs">
                NASA CMR: {nasa?.hits ?? 'failed'} matches · Sentinel Hub: {sentinel?.returned ?? 'failed'} returned
              </p>
            )}
          </section>

          {error && <p className="text-red text-2xs leading-snug">{error}</p>}
        </div>
      )}
    </div>
  );
}
