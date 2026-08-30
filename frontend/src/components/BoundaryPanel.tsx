/**
 * Maritime boundaries: which side you are on, and when you cross.
 *
 * The design point: a boundary readout that says "11.4 km" makes the reader do
 * arithmetic while steering. This leads with the *time*, because that is the
 * number a skipper acts on, and it leads with the *consequence* rather than the
 * geometry — "fishing there without a Sri Lanka licence risks arrest" is the
 * thing worth knowing, not the name of a treaty line.
 *
 * The heading control exists because time-to-cross is meaningless without one.
 * With no heading set, the panel says distance only and does not invent a
 * crossing time.
 */

import {
  AlertTriangle,
  Compass,
  Eye,
  EyeOff,
  FileText,
  Gauge,
  Landmark,
  ShieldAlert,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { GeofenceCheck, Proximity } from '@/lib/types';
import { ProvenanceBadge } from './ProvenanceBadge';

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

function FenceRow({ proximity }: { proximity: Proximity }) {
  const style = STATE_STYLES[proximity.state] ?? STATE_STYLES.outside;
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
            <span className="text-ink-0 truncate text-xs">{proximity.name}</span>
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

          <p className="text-ink-3 mt-1 text-2xs leading-snug italic">{proximity.authority}</p>
        </div>
      </div>
    </div>
  );
}

export function BoundaryPanel({
  check,
  heading,
  speed,
  onHeading,
  onSpeed,
  capUrl,
  visible,
  onToggleVisible,
}: {
  check: GeofenceCheck | null;
  heading: number | null;
  speed: number;
  onHeading: (value: number | null) => void;
  onSpeed: (value: number) => void;
  capUrl: string | null;
  visible: boolean;
  onToggleVisible: () => void;
}) {
  return (
    <div className="glass rounded-lg">
      <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
        <Compass className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">Maritime boundaries</span>
        <div className="ml-auto flex items-center gap-2">
          {check && <ProvenanceBadge provenance={check.provenance} size="xs" />}
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

      {/* Heading and speed. Time-to-cross is meaningless without them, so the
          control sits here rather than in a settings panel. */}
      <div className="border-hairline space-y-2 border-b px-3 py-2">
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="label flex items-center gap-1">
              <Compass className="h-2.5 w-2.5" aria-hidden />
              Heading
            </span>
            <span className="data text-ink-1 text-2xs">
              {heading === null ? 'not set' : `${heading.toFixed(0)}°`}
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={359}
            step={1}
            value={heading ?? 0}
            onChange={(event) => onHeading(Number(event.target.value))}
            className="accent-cyan w-full"
            aria-label="True heading in degrees"
          />
          {heading !== null && (
            <button
              type="button"
              onClick={() => onHeading(null)}
              className="text-ink-3 hover:text-ink-1 mt-0.5 text-2xs transition-colors"
            >
              clear heading
            </button>
          )}
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="label flex items-center gap-1">
              <Gauge className="h-2.5 w-2.5" aria-hidden />
              Speed
            </span>
            <span className="data text-ink-1 text-2xs">{speed.toFixed(1)} kn</span>
          </div>
          <input
            type="range"
            min={0}
            max={25}
            step={0.5}
            value={speed}
            onChange={(event) => onSpeed(Number(event.target.value))}
            className="accent-cyan w-full"
            aria-label="Speed over ground in knots"
          />
        </div>

        {heading === null && (
          <p className="text-ink-3 text-2xs leading-snug">
            Set a heading to get time-to-cross. Without one ORCA reports distance only rather
            than inventing a crossing time.
          </p>
        )}
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

      {capUrl && (
        <div className="border-hairline border-t px-3 py-2">
          <a
            href={capUrl}
            target="_blank"
            rel="noreferrer"
            className="text-ink-2 hover:text-cyan flex items-center gap-1.5 text-2xs transition-colors"
          >
            <FileText className="h-3 w-3" aria-hidden />
            Download this advisory as CAP 1.2 XML
          </a>
          <p className="text-ink-3 mt-1 text-2xs leading-snug">
            The OASIS standard NDMA SACHET and IMD publish in, so an ORCA advisory can be
            consumed by systems that already exist.
          </p>
        </div>
      )}
    </div>
  );
}
