/**
 * Maritime boundaries: which side you are on, and when you cross.
 *
 * The design point: a boundary readout that says "11.4 km" makes the reader do
 * arithmetic while steering. This leads with the *time*, because that is the
 * number a skipper acts on, and it leads with the *consequence* rather than the
 * geometry — "fishing there without a Sri Lanka licence risks arrest" is the
 * thing worth knowing, not the name of a treaty line.
 *
 * Without a heading, the panel reports distance only and does not invent a
 * crossing time.
 */

import {
  AlertTriangle,
  Compass,
  Eye,
  EyeOff,
  Landmark,
  ShieldAlert,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { GeofenceCheck, Proximity } from '@/lib/types';

const STATE_STYLES: Record<string, { text: string; label: string; bg: string }> = {
  inside: { text: 'text-jade', label: 'inside', bg: 'bg-jade/10' },
  outside: { text: 'text-ink-2', label: 'outside', bg: 'bg-white/4' },
  approaching: { text: 'text-amber', label: 'approaching', bg: 'bg-amber/12' },
  crossed: { text: 'text-red', label: 'crossed', bg: 'bg-red/12' },
  exited: { text: 'text-red', label: 'exited', bg: 'bg-red/12' },
};

/** Minutes to a readable interval. 53 minutes, not 0.88 hours. */
function interval(minutes: number): string {
  if (minutes < 90) return `${minutes.toFixed(0)} min`;
  const hours = minutes / 60;
  if (hours < 24) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} days`;
}

function lineFenceLabel(state: Proximity['state']): { text: string; label: string } {
  // A treaty / 200 NM line has no interior. "Outside" on those rows never
  // flipped and looked broken. Distance is the fact; this is just the side.
  if (state === 'approaching') return { text: 'text-amber', label: 'near' };
  if (state === 'crossed' || state === 'exited') return { text: 'text-red', label: state };
  if (state === 'inside') return { text: 'text-jade', label: 'india side' };
  return { text: 'text-ink-2', label: 'clear' };
}

function displayName(proximity: Proximity): string {
  if (proximity.kind === 'imbl' && proximity.name.includes(' - ')) {
    return `${proximity.name.replace(' - ', '–')} boundary`;
  }
  return proximity.name;
}

function FenceRow({ proximity }: { proximity: Proximity }) {
  const isLine = proximity.kind === 'imbl' || proximity.kind === 'eez_outer';
  const style = isLine
    ? { ...STATE_STYLES[proximity.state], ...lineFenceLabel(proximity.state) }
    : (STATE_STYLES[proximity.state] ?? STATE_STYLES.outside);
  const urgent = proximity.state === 'crossed' || proximity.state === 'exited';
  const soon = proximity.time_to_cross_min !== null && proximity.time_to_cross_min < 60;

  return (
    <div
      className={clsx(
        'rounded border px-2.5 py-2',
        urgent ? 'border-red/40 bg-red/8' : soon ? 'border-amber/30 bg-amber/6' : 'border-hairline',
      )}
    >
      <div className="flex items-start gap-2">
        {proximity.kind === 'eez' ? (
          <Landmark className="text-ink-2 mt-0.5 h-3 w-3 shrink-0" aria-hidden />
        ) : (
          <ShieldAlert
            className={clsx('mt-0.5 h-3 w-3 shrink-0', urgent || soon ? 'text-amber' : 'text-ink-2')}
            aria-hidden
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-ink-0 truncate text-xs" title={
              proximity.kind === 'imbl'
                ? 'One treaty line. The two country names are the parties, not a travel direction.'
                : proximity.name
            }>
              {displayName(proximity)}
            </span>
            <span className={clsx('data shrink-0 text-2xs uppercase', style.text)}>
              {style.label}
            </span>
          </div>

          {/* Time first when we have it: that is the number a skipper acts on. */}
          {proximity.time_to_cross_min !== null ? (
            <div className="mt-1 flex items-baseline gap-2">
              <span className={clsx('data text-lg leading-none', soon ? 'text-amber' : 'text-ink-0')}>
                {interval(proximity.time_to_cross_min)}
              </span>
              <span className="text-ink-2 text-2xs">
                on your heading · {proximity.distance_km.toFixed(1)} km {proximity.compass}
              </span>
            </div>
          ) : (
            <div className="mt-1 flex items-baseline gap-2">
              <span className="data text-ink-0 text-sm">
                {proximity.distance_km.toFixed(1)} km
              </span>
              <span className="text-ink-2 text-2xs">
                {proximity.compass}
                {proximity.closing === false && ' · opening'}
                {proximity.closing === true && ' · closing'}
              </span>
            </div>
          )}

          {/* The consequence, not the geometry. */}
          {(urgent || soon || proximity.state === 'approaching') && proximity.consequence && (
            <p className="text-ink-1 mt-1.5 text-2xs leading-snug">{proximity.consequence}</p>
          )}
        </div>
      </div>
    </div>
  );
}

export function BoundaryPanel({
  check,
  visible,
  onToggleVisible,
}: {
  check: GeofenceCheck | null;
  visible: boolean;
  onToggleVisible: () => void;
}) {
  return (
    <div className="glass rounded-lg">
      <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
        <Compass className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">Maritime boundaries</span>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={onToggleVisible}
            className="text-ink-2 hover:text-cyan transition-colors"
            aria-pressed={visible}
            title={visible ? 'Hide boundaries on the map' : 'Show boundaries on the map'}
          >
            {visible ? (
              <Eye className="h-3 w-3" aria-hidden />
            ) : (
              <EyeOff className="h-3 w-3" aria-hidden />
            )}
          </button>
        </div>
      </div>

      <div className="max-h-64 space-y-1.5 overflow-y-auto p-2">
        {!check && (
          <p className="text-ink-2 px-1 text-2xs leading-snug">
            Pick a point on the map to check it against the EEZ and the IMBL treaty lines.
          </p>
        )}

        {check?.transitions.length ? (
          <div className="border-amber/40 bg-amber/10 mb-2 rounded border px-2.5 py-2">
            <div className="label text-amber mb-1 flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" aria-hidden />
              {check.transitions.length} boundary event
              {check.transitions.length === 1 ? '' : 's'}
            </div>
            <p className="text-ink-1 text-2xs leading-snug">
              These fired on a state <em>change</em>, not on proximity — a boat holding position
              near a line does not re-alert.
            </p>
          </div>
        ) : null}

        {check?.proximities.map((proximity) => (
          <FenceRow key={proximity.fence} proximity={proximity} />
        ))}

        {check && check.proximities.length === 0 && (
          <p className="text-ink-2 px-1 text-2xs leading-snug">
            No maritime boundary within 150 km of this position.
          </p>
        )}
      </div>
    </div>
  );
}
