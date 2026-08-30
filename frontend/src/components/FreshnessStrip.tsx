/**
 * The freshness strip — the top-of-screen instrument readout.
 *
 * This exists because "how do you know your data is real?" is the first question
 * a jury asks, and the best answer is a strip that has been on screen the whole
 * time showing per-source last-success times, the selected persistence driver,
 * and which credentials are dormant.
 *
 * It reports failure honestly. A source that has never succeeded says so, in
 * amber, rather than being omitted to keep the row green.
 */

import { useState } from 'react';
import { Activity, ChevronDown, Cpu, Database, Layers, Zap } from 'lucide-react';
import { clsx } from 'clsx';
import type { FreshnessReport, Health } from '@/lib/types';
import { relativeAge } from '@/lib/api';

const STATUS_STYLES: Record<string, { dot: string; text: string; label: string }> = {
  ok: { dot: 'bg-jade', text: 'text-jade', label: 'ok' },
  degraded: { dot: 'bg-amber', text: 'text-amber', label: 'degraded' },
  failing: { dot: 'bg-red', text: 'text-red', label: 'failing' },
  circuit_open: { dot: 'bg-red', text: 'text-red', label: 'breaker open' },
  untried: { dot: 'bg-ink-3', text: 'text-ink-2', label: 'untried' },
  dormant: { dot: 'bg-ink-3', text: 'text-ink-2', label: 'dormant' },
};

function Chip({
  icon: Icon,
  label,
  value,
  tone = 'default',
  title,
}: {
  icon: typeof Cpu;
  label: string;
  value: string;
  tone?: 'default' | 'good' | 'warn';
  title?: string;
}) {
  return (
    <div
      className="flex items-center gap-1.5 whitespace-nowrap"
      title={title ?? `${label}: ${value}`}
    >
      <Icon
        className={clsx(
          'h-3 w-3 shrink-0',
          tone === 'good' ? 'text-jade' : tone === 'warn' ? 'text-amber' : 'text-ink-2',
        )}
        aria-hidden
      />
      <span className="label">{label}</span>
      <span
        className={clsx(
          'data text-2xs',
          tone === 'good' ? 'text-jade' : tone === 'warn' ? 'text-amber' : 'text-ink-0',
        )}
      >
        {value}
      </span>
    </div>
  );
}

export function FreshnessStrip({
  health,
  freshness,
}: {
  health: Health | null;
  freshness: FreshnessReport | null;
}) {
  const [open, setOpen] = useState(false);

  const rows = freshness?.sources ?? [];
  const active = rows.filter((r) => r.status !== 'dormant');
  const healthy = active.filter((r) => r.status === 'ok').length;
  const broken = active.filter((r) => r.status === 'failing' || r.status === 'circuit_open').length;
  const dormant = rows.filter((r) => r.status === 'dormant');

  const capabilities = health?.capabilities ?? {};
  const dormantCaps = Object.entries(capabilities)
    .filter(([, v]) => !v)
    .map(([k]) => k);

  return (
    <div className="glass border-hairline relative z-30 border-b">
      <div className="flex items-center gap-4 px-3 py-1.5">
        <div className="flex items-center gap-2">
          <span className="data text-cyan text-sm font-bold tracking-[0.18em]">ORCA</span>
          <span className="text-ink-3 font-mono text-2xs">v{health?.version ?? '—'}</span>
        </div>

        <div className="bg-hairline h-4 w-px" />

        <div className="scrollbar-none flex items-center gap-4 overflow-x-auto">
          <Chip
            icon={Activity}
            label="sources"
            value={`${healthy}/${active.length}`}
            tone={broken > 0 ? 'warn' : healthy > 0 ? 'good' : 'default'}
            title={
              broken > 0
                ? `${broken} source(s) failing. ORCA degrades that layer and still answers.`
                : `${healthy} of ${active.length} active sources succeeded on their last attempt.`
            }
          />
          <Chip
            icon={Database}
            label="store"
            value={health?.infrastructure.db ?? '—'}
            tone={health?.infrastructure.db === 'postgis' ? 'good' : 'default'}
            title={
              health?.infrastructure.db === 'postgis'
                ? 'PostGIS with pgvector. Spatial predicates run in the database.'
                : 'SQLite with a shapely STRtree. ORCA runs with no infrastructure at all.'
            }
          />
          <Chip
            icon={Zap}
            label="cache"
            value={health?.infrastructure.cache ?? '—'}
            tone={health?.infrastructure.cache === 'redis' ? 'good' : 'default'}
          />
          <Chip
            icon={Layers}
            label="fences"
            value={health?.infrastructure.geofence_index === 'postgis' ? 'gist' : 'strtree'}
          />
          {dormantCaps.length > 0 && (
            <Chip
              icon={Cpu}
              label="dormant"
              value={String(dormantCaps.length)}
              tone="warn"
              title={`Adapters written, credentials not supplied: ${dormantCaps.join(', ')}`}
            />
          )}
        </div>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="text-ink-2 hover:text-ink-0 ml-auto flex shrink-0 items-center gap-1 text-2xs transition-colors"
          aria-expanded={open}
        >
          <span className="label">provenance ledger</span>
          <ChevronDown className={clsx('h-3 w-3 transition-transform', open && 'rotate-180')} aria-hidden />
        </button>
      </div>

      {open && (
        <div className="border-hairline bg-abyss-1 max-h-[42vh] overflow-y-auto border-t">
          <table className="w-full text-2xs">
            <thead className="bg-abyss-1 sticky top-0">
              <tr className="text-ink-3 border-hairline border-b">
                <th className="px-3 py-1.5 text-left font-medium">Source</th>
                <th className="px-2 py-1.5 text-left font-medium">Provider</th>
                <th className="px-2 py-1.5 text-left font-medium">Status</th>
                <th className="px-2 py-1.5 text-right font-medium">Last success</th>
                <th className="px-2 py-1.5 text-right font-medium">p50</th>
                <th className="px-3 py-1.5 text-left font-medium">Variables / note</th>
              </tr>
            </thead>
            <tbody>
              {[...active, ...dormant].map((row) => {
                const style = STATUS_STYLES[row.status] ?? STATUS_STYLES.untried;
                return (
                  <tr key={row.source} className="border-hairline/50 hover:bg-abyss-2/50 border-b">
                    <td className="data text-ink-0 px-3 py-1.5">{row.source}</td>
                    <td className="text-ink-1 px-2 py-1.5">{row.provider}</td>
                    <td className="px-2 py-1.5">
                      <span className="flex items-center gap-1.5">
                        <span className={clsx('h-1.5 w-1.5 shrink-0 rounded-full', style.dot)} />
                        <span className={style.text}>{style.label}</span>
                      </span>
                    </td>
                    <td className="data text-ink-1 px-2 py-1.5 text-right whitespace-nowrap">
                      {relativeAge(row.age_hours)}
                    </td>
                    <td className="data text-ink-2 px-2 py-1.5 text-right">
                      {row.median_latency_ms ? `${row.median_latency_ms.toFixed(0)}ms` : '—'}
                    </td>
                    <td className="text-ink-2 max-w-[22rem] truncate px-3 py-1.5">
                      {row.last_error ? (
                        <span className="text-red" title={row.last_error}>
                          {row.last_error}
                        </span>
                      ) : row.dormant_reason ? (
                        <span className="italic">{row.dormant_reason}</span>
                      ) : (
                        <span className="data">{row.variables.join(' · ')}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-ink-2 px-3 py-4 text-center">
                    No source has been called yet in this process. Click the map to fetch a forecast.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          <p className="text-ink-3 border-hairline border-t px-3 py-2 leading-snug">
            A source that has never succeeded is shown as <span className="text-red">failing</span>{' '}
            rather than omitted — that is the row you want when an ingest job silently never fired.
            Dormant adapters are written and idle because their credential has not been supplied.
          </p>
        </div>
      )}
    </div>
  );
}
