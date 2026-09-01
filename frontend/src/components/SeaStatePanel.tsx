/**
 * "Sea view": the forecast, rendered as the sea it describes.
 *
 * The panel exists because of a specific gap in the rest of the console. Every
 * other surface here is honest and numeric — 2.74 m, limit 1.5 m, veto — and a
 * fisherman standing at a harbour wall does not think in decimals about a sea
 * they can see. This renders the same numbers as water, with the vessel drawn to
 * its real length overall and the class limit drawn as a plane the crests break
 * through.
 *
 * Two rules the panel keeps:
 *
 * 1. **Nothing is rendered that was not measured.** If wave height, period or
 *    direction is missing, the panel says which one and renders nothing. A sea
 *    surface invented from defaults would look exactly as convincing as a real
 *    one, which makes it the most dangerous thing this file could do.
 * 2. **The gap between what was forecast and what is drawn is stated.** Gerstner
 *    waves self-intersect above a steepness of 1, so a very steep sea is drawn
 *    gentler than it is. When that clamp engages the panel says so.
 *
 * The canvas is paused when collapsed. A WebGL context animating behind a closed
 * panel costs the same as one you can see, and this page already runs a MapLibre
 * globe and a deck.gl overlay.
 */

import { useMemo, useState } from 'react';
import { AlertTriangle, Maximize2, Minimize2, Satellite, Waves } from 'lucide-react';
import { clsx } from 'clsx';
import type { PointForecast, ThresholdClass } from '@/lib/types';
import { SeaStateScene } from '@/scenes/SeaState';
import { seaStateFrom, wavelength } from '@/scenes/waves';
import { ProvenanceBadge } from '@/components/ProvenanceBadge';
import {
  SatelliteObservation,
  type SatelliteView,
} from '@/components/SatelliteObservation';

type ViewMode = 'forecast' | SatelliteView;

const VIEW_OPTIONS: Array<{ mode: ViewMode; short: string; title: string }> = [
  { mode: 'forecast', short: 'Forecast', title: 'Forecast sea simulation' },
  { mode: 'sentinel3', short: 'S3 Ocean', title: 'Copernicus Sentinel-3 OLCI · 300 m' },
  { mode: 'nasa', short: 'NASA NRT', title: 'NASA VIIRS/MODIS near-real-time browse imagery' },
  { mode: 'sentinel2', short: 'S2 10 m', title: 'Copernicus Sentinel-2 L2A · 10 m' },
];

/** A numeric evidence value, or null when it is not a usable number. */
function numeric(forecast: PointForecast | null, variable: string): number | null {
  const value = forecast?.evidence[variable]?.value;
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function Readout({
  label,
  value,
  unit,
  forecast,
  variable,
}: {
  label: string;
  value: string;
  unit: string;
  forecast: PointForecast | null;
  variable: string;
}) {
  const evidence = forecast?.evidence[variable];
  return (
    <div className="min-w-0">
      <div className="label text-2xs">{label}</div>
      <div className="flex items-baseline gap-1">
        <span className="data text-ink-0 text-sm">{value}</span>
        <span className="text-ink-3 text-2xs">{unit}</span>
      </div>
      {evidence && (
        <ProvenanceBadge
          provenance={evidence.provenance}
          stale={evidence.freshness.is_stale}
          size="xs"
        />
      )}
    </div>
  );
}

export function SeaStatePanel({
  forecast,
  boatClass,
  loaM,
  open,
  onToggle,
}: {
  forecast: PointForecast | null;
  boatClass: ThresholdClass | undefined;
  loaM: number;
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('forecast');

  const waveHeightM = numeric(forecast, 'wave_height');
  const periodS = numeric(forecast, 'wave_period');
  const directionFromDeg = numeric(forecast, 'wave_direction');
  const windSpeedKn = numeric(forecast, 'wind_speed') ?? 0;
  const limitM = boatClass?.max_wave_m ?? 1.5;

  const missing = [
    waveHeightM === null && 'significant wave height',
    periodS === null && 'peak wave period',
    directionFromDeg === null && 'wave direction',
  ].filter(Boolean) as string[];

  const inputs = useMemo(
    () =>
      waveHeightM !== null && periodS !== null && directionFromDeg !== null
        ? { waveHeightM, periodS, directionFromDeg, windSpeedKn, limitM, loaM }
        : null,
    [waveHeightM, periodS, directionFromDeg, windSpeedKn, limitM, loaM],
  );

  const model = useMemo(() => (inputs ? seaStateFrom(inputs) : null), [inputs]);
  const overLimit = waveHeightM !== null && waveHeightM >= limitM;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => onToggle(true)}
        className="glass pointer-events-auto flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 transition-colors hover:bg-white/5"
      >
        <Waves className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">Sea view</span>
        {waveHeightM !== null && (
          <span className={clsx('data text-2xs', overLimit ? 'text-red' : 'text-ink-2')}>
            {waveHeightM.toFixed(2)} m
          </span>
        )}
      </button>
    );
  }

  return (
    <div
      className={clsx(
        'glass pointer-events-auto flex flex-col overflow-hidden rounded-lg',
        // Bounded by the gap between the chat panel and the verdict rail: at
        // 54rem the expanded panel slid underneath the verdict card, and the
        // verdict is the one thing on this page nothing may cover.
        expanded ? 'h-[34rem] w-[38rem]' : 'h-[23rem] w-[30rem]',
      )}
      style={{ transition: 'width 220ms var(--ease-out-instrument), height 220ms var(--ease-out-instrument)' }}
      data-orca="sea-state-panel"
    >
      <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
        {viewMode === 'forecast' ? (
          <Waves className="text-cyan h-3.5 w-3.5" aria-hidden />
        ) : (
          <Satellite className="text-cyan h-3.5 w-3.5" aria-hidden />
        )}
        <span className="label">Sea view</span>
        {model && viewMode === 'forecast' && (
          <span className="data text-ink-3 text-2xs">
            {model.wavelength.toFixed(0)} m wavelength · {loaM} m hull
          </span>
        )}
        {viewMode !== 'forecast' && (
          <span className="text-ink-3 truncate text-2xs">
            {VIEW_OPTIONS.find((option) => option.mode === viewMode)?.title}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="text-ink-3 hover:text-ink-1 transition-colors"
            aria-label={expanded ? 'Shrink' : 'Expand'}
          >
            {expanded ? (
              <Minimize2 className="h-3 w-3" aria-hidden />
            ) : (
              <Maximize2 className="h-3 w-3" aria-hidden />
            )}
          </button>
          <button
            type="button"
            onClick={() => onToggle(false)}
            className="text-ink-3 hover:text-ink-1 text-2xs transition-colors"
            aria-label="Close sea view"
          >
            close
          </button>
        </div>
      </div>

      <div className="border-hairline bg-abyss-0/45 grid grid-cols-4 gap-1 border-b p-1">
        {VIEW_OPTIONS.map((option) => (
          <button
            key={option.mode}
            type="button"
            onClick={() => setViewMode(option.mode)}
            disabled={option.mode !== 'forecast' && !forecast}
            className={clsx(
              'flex min-w-0 items-center justify-center gap-1 rounded px-1.5 py-1.5 text-[9px] whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-40',
              viewMode === option.mode
                ? 'bg-cyan/12 text-cyan'
                : 'text-ink-2 hover:bg-white/4 hover:text-ink-0',
            )}
            aria-pressed={viewMode === option.mode}
            aria-label={option.title}
            title={option.title}
          >
            {option.mode === 'forecast' ? (
              <Waves className="h-3 w-3 shrink-0" aria-hidden />
            ) : (
              <Satellite className="h-3 w-3 shrink-0" aria-hidden />
            )}
            <span className="truncate">{option.short}</span>
          </button>
        ))}
      </div>

      <div className="relative min-h-0 flex-1">
        {viewMode !== 'forecast' ? (
          <SatelliteObservation forecast={forecast} view={viewMode} />
        ) : inputs ? (
          <>
            <SeaStateScene inputs={inputs} />

            {/* The limit, called out on the canvas, because an amber plane with
                no label is decoration. */}
            <div className="pointer-events-none absolute top-2 left-2 space-y-1">
              <div className="bg-abyss-0/70 rounded px-1.5 py-0.5 backdrop-blur-sm">
                <span className="text-amber text-2xs">
                  amber plane · red contour = {limitM.toFixed(1)} m limit
                  {boatClass ? ` · ${boatClass.label}` : ''}
                </span>
              </div>
              {overLimit && (
                <div className="bg-red/20 border-red/40 rounded border px-1.5 py-0.5 backdrop-blur-sm">
                  <span className="text-red text-2xs">
                    the red contour is your {limitM.toFixed(1)} m limit — this is the veto
                  </span>
                </div>
              )}
            </div>

            {model?.steepnessClamped && (
              <div className="bg-abyss-0/80 pointer-events-none absolute right-2 bottom-2 max-w-[18rem] rounded px-1.5 py-1 backdrop-blur-sm">
                <p className="text-amber flex items-start gap-1 text-2xs leading-snug">
                  <AlertTriangle className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
                  <span>
                    Drawn at {model.renderedHeight.toFixed(2)} m rather than{' '}
                    {model.forecastHeight.toFixed(2)} m: above a steepness of 1 the wave
                    parameterisation folds through itself. The sea is worse than this render.
                  </span>
                </p>
              </div>
            )}
          </>
        ) : (
          <div className="flex h-full items-center justify-center px-6">
            <p className="text-ink-2 max-w-sm text-center text-xs leading-relaxed">
              No sea surface drawn: {missing.join(', ')}{' '}
              {missing.length === 1 ? 'is' : 'are'} missing for this point. A surface invented from
              defaults would look exactly as convincing as a measured one, which is why there is
              nothing here.
            </p>
          </div>
        )}
      </div>

      <div className="border-hairline grid grid-cols-4 gap-3 border-t px-3 py-2">
        <Readout
          label="Wave height"
          value={waveHeightM === null ? '—' : waveHeightM.toFixed(2)}
          unit="m"
          forecast={forecast}
          variable="wave_height"
        />
        <Readout
          label="Peak period"
          value={periodS === null ? '—' : periodS.toFixed(1)}
          unit="s"
          forecast={forecast}
          variable="wave_period"
        />
        <Readout
          label="From"
          value={directionFromDeg === null ? '—' : directionFromDeg.toFixed(0)}
          unit="°"
          forecast={forecast}
          variable="wave_direction"
        />
        <Readout
          label="Wind"
          value={windSpeedKn.toFixed(1)}
          unit="kn"
          forecast={forecast}
          variable="wind_speed"
        />
      </div>

      <div className="border-hairline border-t px-3 py-1.5">
        {viewMode === 'forecast' ? (
          <p className="text-ink-3 text-2xs leading-snug">
            Amplitude from Hs, wavelength from the deep-water dispersion relation L = gT²/2π
            {periodS
              ? ` (${wavelength(periodS).toFixed(0)} m at ${periodS.toFixed(1)} s)`
              : ''}
            , travel direction from the reported "from" bearing. Foam, sky and chop period are
            texture, not measurement.
          </p>
        ) : viewMode === 'sentinel3' ? (
          <p className="text-ink-3 text-2xs leading-snug">
            Optical observation from the most recent Sentinel-3 OLCI overpass in the last seven
            days. It shows what the satellite saw then—not present wave height or live video.
          </p>
        ) : viewMode === 'nasa' ? (
          <p className="text-ink-3 text-2xs leading-snug">
            NASA GIBS selects the newest available NOAA-21/NOAA-20 VIIRS or Aqua MODIS daily
            corrected-reflectance view. The exact satellite and native resolution appear on-image.
          </p>
        ) : (
          <p className="text-ink-3 text-2xs leading-snug">
            Sentinel-2 provides 10 m coastal detail. ORCA selects the newest scene at or below 40%
            catalogue cloud cover when possible and always shows its acquisition time and cloud
            percentage.
          </p>
        )}
      </div>
    </div>
  );
}
