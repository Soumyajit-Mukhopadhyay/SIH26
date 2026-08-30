/**
 * The alert centre — the only surface in ORCA that appears without being asked.
 *
 * Two things it has to get right.
 *
 * **Every alert shows what changed, not what is.** "NO-GO off Chennai" is a
 * status line and the verdict card already says it. "GO → NO-GO, index 92 → 24,
 * lightning probability crossed 60%" is an alert, because it tells you something
 * you did not have a moment ago. So the before/after pair is rendered inline, on
 * every row, and a row without one would be a bug worth noticing.
 *
 * **It says out loud that nothing is sent anywhere.** ORCA has no authority to
 * contact a fisherman, and a prototype that quietly acquires an SMS channel is a
 * prototype that can spam a real person. Alerts live in the console and the
 * footer says so, so nobody demonstrates this feature as something it is not.
 */

import { useState } from 'react';
import {
  AlertOctagon,
  BellRing,
  Check,
  Eye,
  Info,
  Loader2,
  RefreshCw,
  TriangleAlert,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { AlertSeverity, MonitorStatus, OrcaAlert } from '@/hooks/useAlerts';

const TONE: Record<AlertSeverity, { text: string; bg: string; border: string; Icon: typeof Info }> =
  {
    critical: {
      text: 'text-red',
      bg: 'bg-red/10',
      border: 'border-red/40',
      Icon: AlertOctagon,
    },
    warning: {
      text: 'text-amber',
      bg: 'bg-amber/10',
      border: 'border-amber/40',
      Icon: TriangleAlert,
    },
    advisory: {
      text: 'text-cyan',
      bg: 'bg-cyan/8',
      border: 'border-cyan/30',
      Icon: BellRing,
    },
    info: { text: 'text-jade', bg: 'bg-jade/8', border: 'border-jade/30', Icon: Info },
  };

/** Render a before/after pair compactly, whatever keys it carries. */
function Delta({ before, after }: { before: unknown; after: unknown }) {
  const pairs = (() => {
    if (!before || !after || typeof before !== 'object' || typeof after !== 'object') return [];
    const b = before as Record<string, unknown>;
    const a = after as Record<string, unknown>;
    return (
      Object.keys(a)
        .filter((key) => key in b)
        .map((key) => ({ key, from: b[key], to: a[key] }))
        .filter(({ from, to }) => JSON.stringify(from) !== JSON.stringify(to))
        // Two lists of the same length render as "vetoes 2 → 2", which says
        // nothing — and worse, it reads as "unchanged" next to an alert that
        // exists precisely because something changed. The detail line already
        // names which limit was newly exceeded, so drop the row.
        .filter(
          ({ from, to }) =>
            !(Array.isArray(from) && Array.isArray(to) && from.length === to.length),
        )
    );
  })();

  if (pairs.length === 0) return null;

  return (
    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
      {pairs.map(({ key, from, to }) => (
        <span key={key} className="data text-2xs">
          <span className="text-ink-3">{key.replace(/_/g, ' ')} </span>
          <span className="text-ink-3">{format(from)}</span>
          <span className="text-ink-3"> → </span>
          <span className="text-ink-0">{format(to)}</span>
        </span>
      ))}
    </div>
  );
}

function format(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(1);
  if (Array.isArray(value)) return value.length ? `${value.length}` : 'none';
  return String(value);
}

export function AlertRail({
  alerts,
  status,
  connected,
  unseen,
  onOpen,
  onAcknowledge,
  onCheckNow,
  watching,
  onWatchToggle,
  canWatch,
  open,
  onToggle,
}: {
  alerts: OrcaAlert[];
  status: MonitorStatus | null;
  connected: boolean;
  unseen: number;
  onOpen: () => void;
  onAcknowledge: (id: string) => void;
  onCheckNow: () => Promise<{ raised: OrcaAlert[]; note: string | null }>;
  watching: boolean;
  onWatchToggle: () => void;
  canWatch: boolean;
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  const [checking, setChecking] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const check = async () => {
    setChecking(true);
    setNote(null);
    try {
      const result = await onCheckNow();
      setNote(
        result.raised.length
          ? `${result.raised.length} transition(s) raised.`
          : (result.note ?? 'Nothing crossed a line since the last check.'),
      );
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : 'The check failed.');
    } finally {
      setChecking(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => {
          onToggle(true);
          onOpen();
        }}
        className="glass pointer-events-auto relative flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 transition-colors hover:bg-white/5"
        title="Alert centre"
      >
        <BellRing
          className={clsx('h-3.5 w-3.5', unseen > 0 ? 'text-red' : 'text-ink-2')}
          aria-hidden
        />
        <span className="label">Alerts</span>
        {unseen > 0 && (
          <span className="bg-red/25 text-red data rounded-full px-1.5 text-2xs">{unseen}</span>
        )}
        {!connected && <WifiOff className="text-amber h-2.5 w-2.5" aria-hidden />}
      </button>
    );
  }

  return (
    <div className="glass pointer-events-auto flex max-h-[26rem] w-[23rem] flex-col rounded-lg" data-orca="alert-rail">
      <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
        <BellRing className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">Alert centre</span>
        {/* The title goes on a wrapper: lucide icons do not accept one, and an
            icon whose only meaning is its colour needs the tooltip. */}
        <span title={connected ? 'Alert stream connected' : 'Alert stream disconnected'}>
          {connected ? (
            <Wifi className="text-jade h-2.5 w-2.5" aria-hidden />
          ) : (
            <WifiOff className="text-amber h-2.5 w-2.5" aria-hidden />
          )}
        </span>
        <button
          type="button"
          onClick={() => onToggle(false)}
          className="text-ink-3 hover:text-ink-1 ml-auto text-2xs transition-colors"
        >
          close
        </button>
      </div>

      <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-1.5">
        <button
          type="button"
          onClick={onWatchToggle}
          disabled={!canWatch}
          className={clsx(
            'flex items-center gap-1 rounded border px-1.5 py-0.5 text-2xs transition-colors disabled:opacity-30',
            watching
              ? 'border-jade/45 bg-jade/15 text-jade'
              : 'border-hairline text-ink-2 hover:text-ink-0',
          )}
          title={
            canWatch
              ? 'Monitor this position and alert on changes'
              : 'Pick a point on the map first'
          }
        >
          <Eye className="h-2.5 w-2.5" aria-hidden />
          {watching ? 'watching this point' : 'watch this point'}
        </button>
        <button
          type="button"
          onClick={check}
          // Gated on the request being in flight only. Gating on a watch count
          // meant a stale snapshot could disable the button after the user had
          // explicitly asked for a position to be watched; with no watches the
          // endpoint reports "checked 0", which is a useful answer.
          disabled={checking}
          className="border-hairline text-ink-2 hover:text-ink-0 ml-auto flex items-center gap-1 rounded border px-1.5 py-0.5 text-2xs transition-colors disabled:opacity-30"
          title="Run one monitor cycle now — the same code path the timer uses"
        >
          {checking ? (
            <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="h-2.5 w-2.5" aria-hidden />
          )}
          check now
        </button>
      </div>

      {note && (
        <div className="border-hairline border-b px-3 py-1.5">
          <p className="text-ink-2 text-2xs leading-snug">{note}</p>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {alerts.length === 0 ? (
          <p className="text-ink-3 px-1.5 py-2 text-2xs leading-snug">
            Nothing yet. ORCA alerts on <span className="text-ink-1">transitions</span>, not on
            state — a job that reports the current verdict every cycle produces a feed, and a feed
            is something people learn to ignore.
            {status?.watches
              ? ` Watching ${status.watches} position(s), re-checked every ${Math.round(status.poll_seconds / 60)} min.`
              : ' Pick a point and press "watch this point" to start.'}
          </p>
        ) : (
          alerts.map((alert) => {
            const tone = TONE[alert.severity];
            return (
              <div
                key={alert.id}
                className={clsx(
                  'mb-1.5 rounded border px-2 py-1.5',
                  tone.border,
                  tone.bg,
                  alert.acknowledged && 'opacity-45',
                )}
                style={{ animation: 'orca-rise 220ms var(--ease-out-instrument)' }}
              >
                <div className="flex items-start gap-1.5">
                  <tone.Icon className={clsx('mt-px h-3 w-3 shrink-0', tone.text)} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-1.5">
                      <span className={clsx('text-2xs font-semibold', tone.text)}>
                        {alert.headline}
                      </span>
                      <span className="data text-ink-3 ml-auto shrink-0 text-2xs">
                        {new Date(alert.at).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    </div>
                    <p className="text-ink-1 mt-0.5 text-2xs leading-snug">{alert.detail}</p>
                    <Delta before={alert.before} after={alert.after} />
                  </div>
                  {!alert.acknowledged && (
                    <button
                      type="button"
                      onClick={() => onAcknowledge(alert.id)}
                      className="text-ink-3 hover:text-ink-0 shrink-0 transition-colors"
                      aria-label="Acknowledge"
                      title="Mark as seen"
                    >
                      <Check className="h-3 w-3" aria-hidden />
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      <div className="border-hairline border-t px-3 py-1.5">
        <p className="text-ink-3 text-2xs leading-snug">
          Alerts stay in this console. ORCA sends no SMS, push or email — it has no authority to
          contact anyone, and a prototype that quietly acquires a notification channel is one that
          can spam a real fisherman.
        </p>
      </div>
    </div>
  );
}
