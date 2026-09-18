import { clsx } from 'clsx';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { MapAxes, RegionMap } from './RegionMap';
import type { BuoyStation, BuoyValidation } from './types';
import { ColLabel, KV, Lede, Title, Working } from './ui';

/**
 * Satellite against thermometer.
 *
 * The one comparison in ORCA between an inference and a measurement. The
 * backend returns aggregates only — bias, RMSE, max error over the matched
 * days — so that is what is drawn: four numbers with the hierarchy they
 * deserve, and one error scale built from those same four numbers. No series
 * is plotted because none is returned.
 */

const DEFAULT_PLACE = { lat: 15, lon: 89, label: 'Bay of Bengal (15°N 89°E)' };
const WINDOWS = [30, 60, 90, 180];

const fmt = (v: number | null | undefined, digits = 3) =>
  v === null || v === undefined ? '—' : v.toFixed(digits);
const signed = (v: number | null | undefined, digits = 3) =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`;

export function ValidateView() {
  const [place, setPlace] = useState(DEFAULT_PLACE);
  const [days, setDays] = useState(30);
  const [result, setResult] = useState<BuoyValidation | null>(null);
  const [stations, setStations] = useState<BuoyStation[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void fetch('/api/research/insitu/buoys?west=60&south=0&east=100&north=25')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setStations((d.stations as BuoyStation[]) ?? []))
      .catch(() => {});
  }, []);

  const run = useCallback(async (lat: number, lon: number, window: number) => {
    setBusy(true);
    setResult(null);
    try {
      const r = await fetch(`/api/research/insitu/validate-sst?lat=${lat}&lon=${lon}&days=${window}`);
      setResult((await r.json()) as BuoyValidation);
    } catch {
      setResult({ validated: false, reason: 'The validation request failed.' });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void run(place.lat, place.lon, days);
  }, [place, days, run]);

  const active = result?.validated ? result.station : null;
  const points = useMemo(
    () =>
      stations.map((s) => ({
        id: s.station,
        lat: s.lat,
        lon: s.lon,
        label: `${s.station} · ${s.sst_degc} °C · ${s.observed_at.slice(0, 10)}`,
        active: s.station === active,
      })),
    [stations, active],
  );

  const pick = (station: BuoyStation) =>
    setPlace({ lat: station.lat, lon: station.lon, label: station.station });

  return (
    <div className="mx-auto w-full max-w-[1440px] px-6 py-6 lg:px-10 lg:py-8">
      <Title>Ground truth</Title>
      <Lede>
        ORCA&rsquo;s satellite sea-surface temperature (JPL MUR) compared day by day against NOAA
        PMEL&rsquo;s RAMA moored buoys, the only thermometers in the water this system can reach.
      </Lede>
      <div className="mt-4 max-w-3xl">
        <ColLabel>Validation coverage</ColLabel>
        <p className="text-ink-1 mt-1.5 text-xs leading-relaxed">
          RAMA observations currently available to ORCA are sparse and retrospective. The array
          runs about a month behind, and of 13 moorings inside the Indian EEZ,{' '}
          <span className="data text-ink-0">{stations.length}</span> reported in the last four
          months. This comparison therefore measures historical agreement with those available
          observations, not a live field check.
        </p>
      </div>

      <div className="mt-8 grid gap-10 lg:grid-cols-[380px_minmax(0,1fr)]">
        {/* ------------------------------------------------- moorings */}
        <div>
          <div className="flex items-baseline justify-between">
            <ColLabel>Moorings reporting</ColLabel>
            <span className="data text-ink-2 text-[11px]">NOAA PMEL RAMA · ERDDAP</span>
          </div>
          <RegionMap
            points={points}
            onPick={(id) => {
              const s = stations.find((x) => x.station === id);
              if (s) pick(s);
            }}
            className="border-hairline mt-2 rounded border"
          />
          <MapAxes />

          <table className="mt-4 w-full text-xs">
            <thead>
              <tr className="border-hairline-strong border-b">
                <th className="label py-1.5 text-left font-medium">Station</th>
                <th className="label py-1.5 text-right font-medium">Lat</th>
                <th className="label py-1.5 text-right font-medium">Lon</th>
                <th className="label py-1.5 text-right font-medium">SST °C</th>
                <th className="label py-1.5 text-right font-medium">Last obs</th>
              </tr>
            </thead>
            <tbody className="data">
              {stations.map((s) => {
                const on = s.station === active;
                return (
                  <tr
                    key={s.station}
                    onClick={() => pick(s)}
                    className={clsx(
                      'border-hairline hover:bg-abyss-1 cursor-pointer border-b transition-colors',
                      on && 'bg-abyss-1',
                    )}
                  >
                    <td className={clsx('py-1.5', on ? 'text-cyan' : 'text-ink-0')}>{s.station}</td>
                    <td className="text-ink-1 py-1.5 text-right">{s.lat.toFixed(2)}</td>
                    <td className="text-ink-1 py-1.5 text-right">{s.lon.toFixed(2)}</td>
                    <td className="text-ink-0 py-1.5 text-right">{s.sst_degc.toFixed(2)}</td>
                    <td className="text-ink-1 py-1.5 text-right">{s.observed_at.slice(0, 10)}</td>
                  </tr>
                );
              })}
              {!stations.length ? (
                <tr>
                  <td colSpan={5} className="text-ink-2 py-3 text-xs">
                    No mooring has reported in the window, or the array could not be reached.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>

          <div className="mt-5 flex items-center gap-3">
            <ColLabel>Window</ColLabel>
            <div className="flex gap-1">
              {WINDOWS.map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => setDays(w)}
                  className={clsx(
                    'data h-7 rounded px-2.5 text-xs transition-colors',
                    days === w ? 'bg-abyss-2 text-ink-0' : 'text-ink-1 hover:text-ink-0',
                  )}
                >
                  {w} d
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* -------------------------------------------------- results */}
        <div className="min-w-0">
          {busy ? <MatchingWait days={days} /> : null}

          {!busy && result && !result.validated ? (
            <div>
              <ColLabel>Nearest mooring to {place.label}</ColLabel>
              <p className="text-ink-0 mt-2 text-sm">No comparison possible.</p>
              <p className="text-ink-1 mt-1 max-w-2xl text-xs leading-relaxed">{result.reason}</p>
            </div>
          ) : null}

          {!busy && result?.validated ? <Metrics result={result} /> : null}
        </div>
      </div>
    </div>
  );
}

function MatchingWait({ days }: { days: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div>
      <Working label={`Matching ${days} days of satellite SST to the nearest mooring`} />
      <p className="text-ink-2 mt-2 max-w-md text-xs leading-relaxed">
        Each matched day is a separate satellite fetch, so this usually takes about a minute.
        {elapsed > 0 ? <span className="data text-ink-1"> {elapsed}s</span> : null}
      </p>
    </div>
  );
}

function Metrics({ result }: { result: BuoyValidation }) {
  const pairs = result.matched_pairs ?? 0;
  const bias = result.bias_degc ?? null;
  const rmse = result.rmse_degc ?? null;
  const maxErr = result.max_abs_error_degc ?? null;
  const diff =
    result.product_mean_degc != null && result.buoy_mean_degc != null
      ? result.product_mean_degc - result.buoy_mean_degc
      : null;

  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="data text-ink-0 text-lg font-medium">{result.station}</span>
        <span className="data text-ink-1 text-xs">
          {result.lat?.toFixed(2)}°N {result.lon?.toFixed(2)}°E
        </span>
        <span className="text-ink-2 text-xs">
          satellite <span className="data text-ink-1">{result.product}</span> minus buoy
        </span>
      </div>

      {pairs === 0 ? (
        <div className="mt-6">
          <p className="text-ink-0 text-sm">No matched days.</p>
          <p className="text-ink-1 mt-1 max-w-2xl text-xs leading-relaxed">{result.note}</p>
        </div>
      ) : (
        <>
          <div className="border-hairline-strong mt-6 grid grid-cols-2 border-y md:grid-cols-4">
            <Metric label="Bias" value={signed(bias)} unit="°C" hint="mean of satellite − buoy" />
            <Metric label="RMSE" value={fmt(rmse)} unit="°C" hint="typical daily error" />
            <Metric label="Max error" value={fmt(maxErr)} unit="°C" hint="worst single day" />
            <Metric
              label="Matched days"
              value={String(pairs)}
              unit={`of ${result.days_requested}`}
              hint="both sources reported"
              last
            />
          </div>

          {bias != null && rmse != null && maxErr != null ? (
            <ErrorScale bias={bias} rmse={rmse} maxErr={maxErr} />
          ) : null}

          <KV
            className="mt-8"
            rows={[
              {
                k: 'Buoy mean',
                v: <span className="data">{fmt(result.buoy_mean_degc)} °C</span>,
              },
              {
                k: 'Satellite mean',
                v: <span className="data">{fmt(result.product_mean_degc)} °C</span>,
              },
              {
                k: 'Difference',
                v: <span className="data">{signed(diff)} °C</span>,
              },
            ]}
          />

          <p className="text-ink-2 mt-8 max-w-2xl text-xs leading-relaxed">{result.note}</p>
        </>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  unit,
  hint,
  last,
}: {
  label: string;
  value: string;
  unit: string;
  hint: string;
  last?: boolean;
}) {
  return (
    <div className={clsx('border-hairline py-4 pr-4 md:pl-4 md:first:pl-0', !last && 'md:border-r')}>
      <ColLabel>{label}</ColLabel>
      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span className="data text-ink-0 text-[26px] leading-none font-medium">{value}</span>
        <span className="data text-ink-1 text-xs">{unit}</span>
      </div>
      <div className="text-ink-2 mt-1.5 text-xs">{hint}</div>
    </div>
  );
}

/**
 * The four returned numbers on one axis of °C. Bias is where the satellite sits
 * relative to the thermometer; the RMSE band is the typical spread around it;
 * the max-error tick is the worst day. Everything drawn is a returned value.
 */
function ErrorScale({ bias, rmse, maxErr }: { bias: number; rmse: number; maxErr: number }) {
  const span = Math.max(0.5, Math.ceil(Math.max(Math.abs(bias) + rmse, maxErr) * 2) / 2);
  const x = (v: number) => ((v + span) / (2 * span)) * 100;
  const ticks: number[] = [];
  const step = span <= 0.5 ? 0.25 : span <= 1 ? 0.5 : span <= 2 ? 1 : Math.ceil(span / 2);
  for (let t = -span; t <= span + 1e-9; t += step) ticks.push(Number(t.toFixed(2)));

  return (
    <div className="mt-8">
      <div className="flex items-baseline justify-between">
        <ColLabel>Error scale</ColLabel>
        <div className="text-ink-2 flex gap-4 text-[11px]">
          <span className="flex items-center gap-1.5">
            <span className="bg-cyan inline-block h-2 w-0.5" aria-hidden /> bias
          </span>
          <span className="flex items-center gap-1.5">
            <span className="bg-cyan/20 inline-block h-2 w-3" aria-hidden /> ± RMSE
          </span>
          <span className="flex items-center gap-1.5">
            <span className="bg-amber inline-block h-2 w-0.5" aria-hidden /> ± max |error|
          </span>
        </div>
      </div>
      <div className="relative mt-3 h-10">
        {/* axis */}
        <div className="bg-hairline-strong absolute top-5 left-0 h-px w-full" />
        {/* zero */}
        <div
          className="bg-ink-1 absolute top-3 h-4 w-px"
          style={{ left: `${x(0)}%` }}
          aria-hidden
        />
        {/* rmse band */}
        <div
          className="bg-cyan/20 absolute top-3.5 h-3"
          style={{ left: `${x(bias - rmse)}%`, width: `${x(bias + rmse) - x(bias - rmse)}%` }}
        />
        {/* bias */}
        <div className="bg-cyan absolute top-2 h-6 w-0.5" style={{ left: `${x(bias)}%` }} />
        {/* max |error|: the API reports a magnitude, not a sign, so mark both */}
        {[maxErr, -maxErr].map((v) => (
          <div
            key={v}
            className="bg-amber absolute top-3 h-4 w-0.5"
            style={{ left: `${x(v)}%` }}
          />
        ))}
        {/* ticks */}
        {ticks.map((t) => (
          <div
            key={t}
            className="data text-ink-2 absolute top-7 -translate-x-1/2 text-[10px]"
            style={{ left: `${x(t)}%` }}
          >
            {t > 0 ? '+' : ''}
            {t}
          </div>
        ))}
      </div>
      <div className="text-ink-2 mt-3 text-xs">
        Satellite − buoy, °C. Positive means the satellite analysis reads warm.
      </div>
    </div>
  );
}
