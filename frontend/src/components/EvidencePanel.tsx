/**
 * The evidence panel — "how do you know that?", answered.
 *
 * Every value at the selected point, each with its provenance, its age and its
 * dataset. Two things it does that a plain readout would not:
 *
 * - **Cross-validation is surfaced.** Model SST and 1 km satellite SST are two
 *   independent measurements of the same variable, so their agreement is
 *   computed and shown. That is what makes an agreement badge meaningful rather
 *   than decorative.
 * - **Missing values keep their row.** A source that could not answer is shown
 *   greyed with its reason, because a silently absent row and a zero look
 *   identical to a reader and mean opposite things.
 */

import { ExternalLink, GitBranch, Scale } from 'lucide-react';
import { clsx } from 'clsx';
import type { PointForecast } from '@/lib/types';
import { EvidenceValue, ProvenanceMix } from './ProvenanceBadge';

/** Display order: what a mariner reads first, not alphabetical. */
const ORDER = [
  'wave_height',
  'wave_period',
  'swell_height',
  'wave_direction',
  'wind_speed',
  'wind_gust',
  'wind_direction',
  'visibility',
  'convective_energy',
  'convective_inhibition',
  'precipitation',
  'sst',
  'sst_satellite',
  'sst_uncertainty',
  'sea_surface_current',
  'sea_surface_current_direction',
];

const LABELS: Record<string, string> = {
  wave_height: 'Significant wave height',
  wave_period: 'Peak period',
  swell_height: 'Swell height',
  wave_direction: 'Wave direction',
  wind_speed: 'Wind speed',
  wind_gust: 'Gusts',
  wind_direction: 'Wind direction',
  visibility: 'Visibility',
  convective_energy: 'CAPE (thunderstorm proxy)',
  convective_inhibition: 'CIN',
  precipitation: 'Precipitation',
  sst: 'Sea surface temp (model)',
  sst_satellite: 'Sea surface temp (satellite, 1 km)',
  sst_uncertainty: 'SST analysis error',
  sea_surface_current: 'Surface current',
  sea_surface_current_direction: 'Current direction',
};

function Agreement({ forecast }: { forecast: PointForecast }) {
  const model = forecast.evidence.sst;
  const satellite = forecast.evidence.sst_satellite;
  if (!model?.value || !satellite?.value) return null;
  if (typeof model.value !== 'number' || typeof satellite.value !== 'number') return null;

  const delta = Math.abs(model.value - satellite.value);
  // 0.5 degC is roughly the combined uncertainty of a model analysis and an
  // L4 satellite product, so agreement inside that is genuinely agreement.
  const agrees = delta <= 0.5;

  return (
    <div
      className={clsx(
        'rounded border px-3 py-2',
        agrees ? 'border-jade/25 bg-jade/8' : 'border-amber/30 bg-amber/8',
      )}
    >
      <div className={clsx('label mb-1 flex items-center gap-1', agrees ? 'text-jade' : 'text-amber')}>
        <Scale className="h-3 w-3" aria-hidden />
        {agrees ? 'Sources agree' : 'Sources disagree'}
      </div>
      <p className="text-ink-1 text-xs leading-snug">
        Model SST <span className="data text-ink-0">{model.value.toFixed(2)}</span> vs satellite{' '}
        <span className="data text-ink-0">{satellite.value.toFixed(2)}</span> °C — a difference of{' '}
        <span className="data text-ink-0">{delta.toFixed(2)}</span> °C.{' '}
        {agrees
          ? 'Two independent measurements within combined uncertainty.'
          : 'Larger than combined uncertainty; treat SST-derived products with caution here.'}
      </p>
    </div>
  );
}

export function EvidencePanel({ forecast }: { forecast: PointForecast | null }) {
  if (!forecast) {
    return (
      <div className="text-ink-2 p-4 text-xs leading-snug">
        Click anywhere on the sea to pull live conditions and their provenance.
      </div>
    );
  }

  const entries = Object.entries(forecast.evidence).sort(([a], [b]) => {
    const ia = ORDER.indexOf(a);
    const ib = ORDER.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });

  const derived = entries.filter(([, e]) => e.lineage.length > 0);

  return (
    <div className="space-y-3 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="data text-ink-1 text-xs">
          {forecast.lat.toFixed(3)}°N {forecast.lon.toFixed(3)}°E
        </div>
        <ProvenanceMix
          mix={forecast.summary.mix}
          worst={forecast.summary.provenance}
          stale={forecast.summary.stale}
        />
      </div>

      <Agreement forecast={forecast} />

      <div className="grid grid-cols-2 gap-x-3 gap-y-3">
        {entries.map(([key, evidence]) => (
          <EvidenceValue key={key} evidence={evidence} label={LABELS[key] ?? key} />
        ))}
      </div>

      {derived.length > 0 && (
        <div className="border-hairline border-t pt-2">
          <div className="label text-violet mb-1.5 flex items-center gap-1">
            <GitBranch className="h-3 w-3" aria-hidden />
            Lineage of derived values
          </div>
          {derived.map(([key, evidence]) => (
            <div key={key} className="text-ink-2 mb-1 text-2xs leading-snug">
              <span className="data text-ink-1">{key}</span> ← {evidence.lineage.join(', ')}
              {evidence.method && <span className="italic"> via {evidence.method}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Citations, deduplicated. A jury following one of these is the point. */}
      <div className="border-hairline border-t pt-2">
        <div className="label mb-1.5">Sources</div>
        <div className="space-y-1">
          {[
            ...new Map(
              entries
                .flatMap(([, e]) => e.citations)
                .map((c) => [c.label + c.identifier, c] as const),
            ).values(),
          ].map((citation) => (
            <a
              key={citation.label + citation.identifier}
              href={citation.url ?? '#'}
              target="_blank"
              rel="noreferrer"
              className="text-ink-1 hover:text-cyan flex items-start gap-1.5 text-2xs leading-snug transition-colors"
            >
              <ExternalLink className="mt-0.5 h-2.5 w-2.5 shrink-0" aria-hidden />
              <span>
                {citation.label}
                {citation.identifier && (
                  <span className="text-ink-3 data"> · {citation.identifier}</span>
                )}
              </span>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
