/**
 * The layer rail: what is drawn, and where each layer came from.
 *
 * Every row carries a provenance badge and a validity time, because a layer
 * toggle that says only "SST" is a control panel, and one that says "SST ·
 * CACHED · yesterday 09:00 · MUR 1 km" is an instrument. That difference is the
 * whole argument ORCA is making.
 *
 * The PFZ row goes further and shows which criteria the derivation could
 * actually apply. When SSHA is missing, rank 3 is unreachable, and the rail says
 * so rather than letting a user assume the absence of rank 3 means "no good
 * fishing" instead of "we could not check".
 */

import { useState } from 'react';
import {
  Activity,
  ChevronDown,
  Download,
  Fish,
  Layers,
  Loader2,
  RefreshCw,
  Thermometer,
  Waves,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { RasterCatalogue, RasterVariable } from '@/lib/types';
import { ProvenanceBadge } from './ProvenanceBadge';
import { relativeAge } from '@/lib/api';

const ICONS: Record<string, typeof Waves> = {
  sst: Thermometer,
  sst_gradient: Activity,
  chlorophyll: Fish,
  pfz_rank: Fish,
  wave_height: Waves,
};

const TITLES: Record<string, string> = {
  sst: 'Sea surface temperature',
  sst_gradient: 'Thermal front strength',
  chlorophyll: 'Chlorophyll-a',
  pfz_rank: 'Potential fishing zones',
};

/** Age of a layer, from its valid_time. */
function ageHours(validTime: string | null | undefined): number | null {
  if (!validTime) return null;
  const t = Date.parse(validTime);
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) / 3_600_000;
}

function Legend({ variable }: { variable: RasterVariable }) {
  const cmap = variable.colormap;
  if (!cmap) return null;

  if (cmap.kind === 'categorical') {
    return (
      <div className="mt-1.5 space-y-0.5">
        {(cmap.classes ?? [])
          .filter((c) => c.value > 0)
          .map((c) => (
            <div key={c.value} className="flex items-center gap-1.5">
              <span
                className="h-2 w-4 shrink-0 rounded-sm"
                style={{ background: `rgba(${c.rgba.join(',')})` }}
              />
              <span className="text-ink-2 text-2xs">rank {c.value}</span>
            </div>
          ))}
      </div>
    );
  }

  // A continuous ramp is drawn from the same domain the server used, so the
  // swatch and the pixels cannot disagree about what a colour means.
  return (
    <div className="mt-1.5">
      <div
        className="h-1.5 w-full rounded-sm"
        style={{
          background: `linear-gradient(to right, ${(cmap.stops ?? [])
            .map((s) => `rgba(${s.rgba.join(',')})`)
            .join(', ')})`,
        }}
      />
      <div className="text-ink-3 mt-0.5 flex justify-between font-mono text-2xs">
        <span>
          {cmap.vmin} {variable.unit}
        </span>
        <span>
          {cmap.vmax} {variable.unit}
        </span>
      </div>
    </div>
  );
}

export function LayerRail({
  catalogue,
  active,
  onToggle,
  onRefresh,
  refreshing,
  opacity,
  onOpacity,
}: {
  catalogue: RasterCatalogue | null;
  active: Set<string>;
  onToggle: (variable: string) => void;
  onRefresh: () => void;
  refreshing: boolean;
  opacity: number;
  onOpacity: (value: number) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const variables = catalogue?.variables ?? [];

  return (
    <div className="glass rounded-lg">
      <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
        <Layers className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">Data layers</span>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="text-ink-2 hover:text-cyan ml-auto flex items-center gap-1 text-2xs transition-colors disabled:opacity-50"
          title="Re-run the ingest job: pull the AOI subsets, derive the fields, rewrite the rasters"
        >
          {refreshing ? (
            <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="h-2.5 w-2.5" aria-hidden />
          )}
          {refreshing ? 'ingesting' : 'refresh'}
        </button>
      </div>

      {variables.length === 0 && (
        <div className="p-3">
          <p className="text-ink-2 text-2xs leading-snug">
            No rasters have been generated yet.
          </p>
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="border-cyan/40 text-cyan hover:bg-cyan/10 mt-2 flex w-full items-center justify-center gap-1.5 rounded border px-2 py-1.5 text-2xs transition-colors disabled:opacity-50"
          >
            <Download className="h-3 w-3" aria-hidden />
            {refreshing ? 'Running ingest…' : 'Run the ingest job'}
          </button>
          <p className="text-ink-3 mt-1.5 text-2xs leading-snug">
            Pulls the AOI subsets from NOAA CoastWatch, derives the fronts and PFZ, and writes
            the colour-mapped rasters. Roughly 30 seconds.
          </p>
        </div>
      )}

      {variables.length > 0 && (
        <>
          <div className="p-1.5">
            {variables.map((variable) => {
              const on = active.has(variable.variable);
              const Icon = ICONS[variable.variable] ?? Layers;
              const age = ageHours(variable.valid_time);
              const isOpen = expanded === variable.variable;

              return (
                <div key={variable.variable} className="mb-0.5">
                  <div
                    className={clsx(
                      'rounded transition-colors',
                      on ? 'bg-cyan/8' : 'hover:bg-abyss-2/70',
                    )}
                  >
                    <div className="flex items-center gap-2 px-2 py-1.5">
                      <button
                        type="button"
                        onClick={() => onToggle(variable.variable)}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                        aria-pressed={on}
                      >
                        <span
                          className={clsx(
                            'flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border',
                            on ? 'border-cyan bg-cyan/25' : 'border-hairline-strong',
                          )}
                        >
                          {on && <span className="bg-cyan h-1.5 w-1.5 rounded-[1px]" />}
                        </span>
                        <Icon
                          className={clsx('h-3 w-3 shrink-0', on ? 'text-cyan' : 'text-ink-2')}
                          aria-hidden
                        />
                        <span
                          className={clsx(
                            'truncate text-xs',
                            on ? 'text-ink-0' : 'text-ink-1',
                          )}
                        >
                          {TITLES[variable.variable] ?? variable.variable}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setExpanded(isOpen ? null : variable.variable)}
                        className="text-ink-3 hover:text-ink-1 shrink-0 transition-colors"
                        aria-label="Layer details"
                      >
                        <ChevronDown
                          className={clsx('h-3 w-3 transition-transform', isOpen && 'rotate-180')}
                          aria-hidden
                        />
                      </button>
                    </div>

                    <div className="flex items-center gap-1.5 px-2 pb-1.5 pl-[1.9rem]">
                      <ProvenanceBadge
                        provenance={variable.provenance}
                        size="xs"
                        title={
                          variable.lineage.length
                            ? `Derived from: ${variable.lineage.join(', ')}`
                            : undefined
                        }
                      />
                      <span className="text-ink-3 data text-2xs">{relativeAge(age)}</span>
                    </div>

                    {isOpen && (
                      <div className="border-hairline mx-2 mb-2 border-t pt-2">
                        <Legend variable={variable} />

                        {variable.statistics && (
                          <div className="text-ink-2 data mt-2 flex gap-3 text-2xs">
                            <span>min {variable.statistics.min}</span>
                            <span>max {variable.statistics.max}</span>
                            <span>
                              {Math.round(
                                (variable.statistics.valid_cells /
                                  variable.statistics.total_cells) *
                                  100,
                              )}
                              % water
                            </span>
                          </div>
                        )}

                        {variable.method && (
                          <p className="text-ink-2 mt-1.5 text-2xs leading-snug">
                            {variable.method}
                          </p>
                        )}

                        {variable.lineage.length > 0 && (
                          <p className="text-ink-3 data mt-1 text-2xs leading-snug">
                            from {variable.lineage.join(' + ')}
                          </p>
                        )}

                        {/* The honest bit: which criteria the PFZ could apply. */}
                        {variable.pfz?.inputs_missing?.length ? (
                          <div className="border-amber/25 bg-amber/8 mt-2 rounded border px-2 py-1.5">
                            <div className="label text-amber mb-0.5">Incomplete derivation</div>
                            <p className="text-ink-1 text-2xs leading-snug">
                              Missing {variable.pfz.inputs_missing.join(' and ')}, so{' '}
                              {variable.pfz.inputs_missing.includes('ssha')
                                ? 'rank 3 is unreachable'
                                : 'some criteria could not be applied'}
                              . An absent rank means ORCA could not check it, not that the water
                              is poor.
                            </p>
                          </div>
                        ) : null}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="border-hairline border-t px-3 py-2">
            <div className="mb-1 flex items-center justify-between">
              <span className="label">Layer opacity</span>
              <span className="data text-ink-1 text-2xs">{Math.round(opacity * 100)}%</span>
            </div>
            <input
              type="range"
              min={0.1}
              max={1}
              step={0.05}
              value={opacity}
              onChange={(event) => onOpacity(Number(event.target.value))}
              className="accent-cyan w-full"
              aria-label="Data layer opacity"
            />
          </div>
        </>
      )}
    </div>
  );
}
