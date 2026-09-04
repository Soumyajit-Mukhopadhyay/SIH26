/**
 * The layer rail: what is drawn on the map.
 *
 * Provenance and age live in the Evidence panel, not on every toggle.
 */

import {
  Activity,
  Download,
  Fish,
  Layers,
  Leaf,
  Loader2,
  RefreshCw,
  Thermometer,
  Waves,
  Wind,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { RasterCatalogue } from '@/lib/types';

const ICONS: Record<string, typeof Waves> = {
  sst: Thermometer,
  sst_gradient: Activity,
  chlorophyll: Leaf,
  pfz_rank: Fish,
  wave_height: Waves,
  wind_uv: Wind,
  current_uv: Waves,
};

const TITLES: Record<string, string> = {
  sst: 'Sea surface temperature',
  sst_gradient: 'Thermal front strength',
  chlorophyll: 'Chlorophyll',
  pfz_rank: 'Potential fishing zones',
  wind_uv: 'Wind flow',
  current_uv: 'Surface current flow',
};

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
            {[...variables]
              .sort((a, b) => {
                const rank = (v: string) => (v.endsWith('_uv') ? 1 : 0);
                return rank(a.variable) - rank(b.variable);
              })
              .map((variable) => {
              const on = active.has(variable.variable);
              const Icon = ICONS[variable.variable] ?? Layers;

              return (
                <div key={variable.variable} className="mb-0.5">
                  <div
                    className={clsx(
                      'rounded transition-colors',
                      on ? 'bg-cyan/8' : 'hover:bg-abyss-2/70',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => onToggle(variable.variable)}
                      className="flex w-full min-w-0 items-center gap-2 px-2 py-1.5 text-left"
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
