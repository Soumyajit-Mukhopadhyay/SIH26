/**
 * The freshness strip — the top-of-screen instrument readout.
 *
 * Prototype layout: brand + live sources count, then the primary tool tabs,
 * then the provenance ledger. Infrastructure chips (store / cache / fences)
 * stay out of the chrome so the strip stays about what the user can do.
 */

import { useState, type ReactNode } from 'react';
import { Activity, Anchor, BookMarked, ChevronDown } from 'lucide-react';
import { clsx } from 'clsx';
import type { FreshnessReport } from '@/lib/types';
import { relativeAge } from '@/lib/api';

const STATUS_STYLES: Record<string, { dot: string; text: string; label: string }> = {
  ok: { dot: 'bg-jade', text: 'text-jade', label: 'ok' },
  degraded: { dot: 'bg-amber', text: 'text-amber', label: 'degraded' },
  failing: { dot: 'bg-red', text: 'text-red', label: 'failing' },
  circuit_open: { dot: 'bg-red', text: 'text-red', label: 'breaker open' },
  untried: { dot: 'bg-ink-3', text: 'text-ink-2', label: 'untried' },
  dormant: { dot: 'bg-ink-3', text: 'text-ink-2', label: 'dormant' },
};

export function FreshnessStrip({
  freshness,
  tools,
  onOpenResearch,
  onOpenHarbours,
}: {
  freshness: FreshnessReport | null;
  /** Primary tool tabs (Look / Intel / SAR / Passage / Sea view). */
  tools?: ReactNode;
  /** Opens the researcher workspace — the second audience the PS names. */
  onOpenResearch?: () => void;
  /** Opens the harbour advisory board — coastal authorities and disaster
   *  management, who need a coastline rather than a point. */
  onOpenHarbours?: () => void;
}) {
  const [open, setOpen] = useState(false);

  const rows = freshness?.sources ?? [];
  const active = rows.filter((r) => r.status !== 'dormant');
  const healthy = active.filter((r) => r.status === 'ok').length;
  const broken = active.filter((r) => r.status === 'failing' || r.status === 'circuit_open').length;
  const dormant = rows.filter((r) => r.status === 'dormant');

  return (
    <div className="glass border-hairline relative z-30 border-b">
      <div className="flex items-center gap-3 px-3 py-1.5">
        <div className="flex shrink-0 items-center gap-2">
          <span className="data text-cyan text-sm font-bold tracking-[0.18em]">ORCA</span>
        </div>

        <div className="bg-hairline h-4 w-px shrink-0" />

        <div
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap"
          title={
            broken > 0
              ? `${broken} source(s) failing. ORCA degrades that layer and still answers.`
              : `${healthy} of ${active.length} active sources succeeded on their last attempt.`
          }
        >
          <Activity
            className={clsx(
              'h-3 w-3 shrink-0',
              broken > 0 ? 'text-amber' : healthy > 0 ? 'text-jade' : 'text-ink-2',
            )}
            aria-hidden
          />
          <span className="label">sources</span>
          <span
            className={clsx(
              'data text-2xs',
              broken > 0 ? 'text-amber' : healthy > 0 ? 'text-jade' : 'text-ink-0',
            )}
          >
            {healthy}/{active.length}
          </span>
        </div>

        {tools && (
          <>
            <div className="bg-hairline h-4 w-px shrink-0" />
            <div className="scrollbar-none min-w-0 flex-1 overflow-x-auto">{tools}</div>
          </>
        )}

        {/* Two entry points for two audiences, both in the masthead rather than
            in the tool tabs: neither is another tool for the fisherman holding
            the phone, and both take over the screen when opened. The harbour
            board is the only view in ORCA that is about a coastline rather than
            a point, which is precisely why it cannot live in a side panel. */}
        {onOpenHarbours && (
          <button
            type="button"
            onClick={onOpenHarbours}
            className={clsx(
              'border-hairline text-ink-2 hover:text-cyan hover:border-cyan/40 flex shrink-0 items-center gap-1 rounded border px-2 py-0.5 text-2xs transition-colors',
              !onOpenResearch && !onOpenHarbours && 'ml-auto',
            )}
            title="Harbour advisory board — which stretches of coast are unsafe today, and for whom"
          >
            <Anchor className="h-3 w-3" aria-hidden />
            <span className="label">harbours</span>
          </button>
        )}

        {onOpenResearch && (
          <button
            type="button"
            onClick={onOpenResearch}
            className={clsx(
              'border-hairline text-ink-2 hover:text-cyan hover:border-cyan/40 flex shrink-0 items-center gap-1 rounded border px-2 py-0.5 text-2xs transition-colors',
              !onOpenHarbours && 'ml-auto',
            )}
            title="Datasets, subsetting and the learned models"
          >
            <BookMarked className="h-3 w-3" aria-hidden />
            <span className="label">researcher</span>
          </button>
        )}

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className={clsx(
            'text-ink-2 hover:text-ink-0 flex shrink-0 items-center gap-1 text-2xs transition-colors',
            !onOpenResearch && 'ml-auto',
          )}
          aria-expanded={open}
        >
          <span className="label">provenance ledger</span>
          <ChevronDown
            className={clsx('h-3 w-3 transition-transform', open && 'rotate-180')}
            aria-hidden
          />
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
