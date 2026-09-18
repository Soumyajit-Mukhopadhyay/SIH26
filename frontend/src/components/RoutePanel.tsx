/**
 * Passage planning: pick a destination, get a route the rule engine cleared.
 *
 * The panel's job is to make one distinction impossible to miss: **a refusal is
 * an answer, not a failure.** "No safe passage for an 8.2 m boat today, and here
 * are the six cells that block it" is the most valuable output this feature has,
 * and it is the one a conventional UI would render as a red error toast and hide.
 * So a refusal gets the same weight as a route — a heading, the reason, and the
 * specific blocking conditions, listed.
 *
 * The second thing it insists on is the detour figure. A route 8% longer than the
 * great circle that avoids a 3.1 m sea is worth taking; one 180% longer is the
 * planner telling you the honest answer was "not today". The user sees the
 * percentage and decides, rather than being handed a line on a map.
 */

import { useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  Clock,
  Crosshair,
  Loader2,
  Navigation,
  Route as RouteIcon,
  Ruler,
  ShieldAlert,
  X,
} from 'lucide-react';
import { clsx } from 'clsx';
import { ApiError, api } from '@/lib/api';
import type { RoutePlan, Verdict } from '@/lib/types';

const VERDICT_TONE: Record<Verdict, string> = {
  GO: 'text-jade',
  CAUTION: 'text-amber',
  'NO-GO': 'text-red',
  UNVERIFIABLE: 'text-ink-2',
};

function Stat({
  icon,
  label,
  value,
  unit,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit?: string;
  tone?: string;
}) {
  return (
    <div>
      <div className="label flex items-center gap-1 text-2xs">
        {icon}
        {label}
      </div>
      <div className="flex items-baseline gap-1">
        <span className={clsx('data text-sm', tone ?? 'text-ink-0')}>{value}</span>
        {unit && <span className="text-ink-3 text-2xs">{unit}</span>}
      </div>
    </div>
  );
}

export function RoutePanel({
  origin,
  destination,
  onPickDestination,
  pickingDestination,
  boatClassCode,
  loaM,
  speedKn,
  plan,
  onPlan,
  onClose,
}: {
  origin: { lat: number; lon: number; label?: string | null } | null;
  destination: { lat: number; lon: number } | null;
  onPickDestination: () => void;
  pickingDestination: boolean;
  boatClassCode?: string | null;
  loaM: number | null;
  speedKn: number;
  plan: RoutePlan | null;
  onPlan: (plan: RoutePlan | null) => void;
  onClose?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showBlockers, setShowBlockers] = useState(false);

  const run = async () => {
    if (!origin || !destination) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.planRoute({
        fromLat: origin.lat,
        fromLon: origin.lon,
        toLat: destination.lat,
        toLon: destination.lon,
        loaM,
        boatClassCode,
        speedKn,
      });
      onPlan(result);
    } catch (cause) {
      // A transport failure is genuinely different from a refusal to route, and
      // conflating them would teach the user to distrust the refusals.
      setError(
        cause instanceof ApiError
          ? `${cause.status === 0 ? 'The API is unreachable' : `HTTP ${cause.status}`}: ${cause.message}`
          : 'The route request failed.',
      );
      onPlan(null);
    } finally {
      setBusy(false);
    }
  };

  const blockers = plan?.what_would_change_it ?? plan?.blocked_by ?? [];
  const refused = plan?.refused_on_direct_line ?? [];

  return (
    <div className="glass pointer-events-auto flex flex-col gap-2.5 rounded-lg px-3 py-2.5">
      <div className="flex items-center gap-1.5">
        <RouteIcon className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">Safe passage</span>
        {plan?.lattice && (
          <span className="data text-ink-3 text-2xs ml-1">{plan.lattice.step_deg}° lattice</span>
        )}
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close safe passage"
            title="Close"
            className="text-ink-3 hover:text-ink-0 hover:bg-abyss-2 -mr-1 ml-auto rounded p-1 transition-colors"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        )}
      </div>

      <div className="border-hairline divide-hairline divide-y rounded border">
        <div className="flex items-center gap-2 px-2 py-1.5 text-2xs">
          <span className="text-ink-3 w-9 shrink-0 tracking-wide uppercase">from</span>
          <span
            className={clsx(
              'min-w-0 flex-1 truncate',
              origin ? 'data text-ink-0' : 'text-ink-3',
            )}
          >
            {origin
              ? (origin.label ?? `${origin.lat.toFixed(3)}°N ${origin.lon.toFixed(3)}°E`)
              : 'Pick a point on the map'}
          </span>
        </div>
        <div className="flex items-center gap-2 px-2 py-1.5 text-2xs">
          <span className="text-ink-3 w-9 shrink-0 tracking-wide uppercase">to</span>
          {destination ? (
            <span className="data text-ink-0 min-w-0 flex-1 truncate">
              {destination.lat.toFixed(3)}°N {destination.lon.toFixed(3)}°E
            </span>
          ) : (
            <span className="text-ink-3 min-w-0 flex-1">Not set</span>
          )}
          <button
            type="button"
            onClick={onPickDestination}
            aria-label={pickingDestination ? 'Cancel picking a destination' : 'Set destination'}
            className={clsx(
              'flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 transition-colors',
              pickingDestination
                ? 'border-cyan/60 bg-cyan/20 text-cyan'
                : 'border-hairline text-ink-2 hover:border-hairline-strong hover:text-ink-0',
            )}
          >
            <Crosshair className="h-2.5 w-2.5" aria-hidden />
            {pickingDestination ? 'Click the sea' : destination ? 'Change' : 'Set'}
          </button>
        </div>
      </div>

      <button
        type="button"
        onClick={run}
        disabled={!origin || !destination || busy}
        className="border-cyan/50 bg-cyan/15 text-cyan hover:bg-cyan/25 flex items-center justify-center gap-1.5 rounded border px-2 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-30"
      >
        {busy ? (
          <>
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            Sampling the corridor
          </>
        ) : (
          <>
            <Navigation className="h-3 w-3" aria-hidden />
            Plan the passage
          </>
        )}
      </button>
      {!destination && !busy && (
        <p className="text-ink-3 -mt-1 text-center text-[10px]">
          Set a destination to enable planning
        </p>
      )}

      {error && (
        <p className="text-red flex items-start gap-1 text-2xs leading-snug">
          <Ban className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
          {error}
        </p>
      )}

      {plan?.ok && (
        <>
          <div className="border-hairline grid grid-cols-3 gap-2 border-t pt-2">
            <Stat
              icon={<Ruler className="h-2.5 w-2.5" aria-hidden />}
              label="Distance"
              value={String(plan.distance_nm)}
              unit="nm"
            />
            <Stat
              icon={<Clock className="h-2.5 w-2.5" aria-hidden />}
              label="At"
              value={String(plan.duration_h)}
              unit={`h @ ${plan.speed_kn} kn`}
            />
            <Stat
              icon={<ArrowRight className="h-2.5 w-2.5" aria-hidden />}
              label="Detour"
              value={`${(plan.detour_pct ?? 0) > 0 ? '+' : ''}${plan.detour_pct}`}
              unit={`% of ${plan.direct_nm} nm`}
              tone={(plan.detour_pct ?? 0) > 40 ? 'text-amber' : undefined}
            />
          </div>

          <div className="flex items-center gap-1.5">
            <span className="label text-2xs">worst on route</span>
            <span
              className={clsx(
                'data text-2xs',
                VERDICT_TONE[plan.worst_verdict ?? 'UNVERIFIABLE'],
              )}
            >
              {plan.worst_verdict} {plan.worst_index !== null && `· ${plan.worst_index}/100`}
            </span>
            <span className="text-ink-3 ml-auto text-2xs">{plan.waypoints?.length ?? 0} waypoints</span>
          </div>

          <p className="text-ink-2 text-2xs leading-snug">{plan.why_this_route}</p>

          {plan.goal_note && plan.goal_note.includes('closest sea') && (
            <p className="text-amber flex items-start gap-1 text-2xs leading-snug">
              <AlertTriangle className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
              Destination {plan.goal_note}.
            </p>
          )}
        </>
      )}

      {plan && !plan.ok && (
        <div className="border-red/40 bg-red/8 space-y-1.5 rounded border px-2 py-1.5">
          <div className="flex items-center gap-1.5">
            <ShieldAlert className="text-red h-3 w-3" aria-hidden />
            <span className="label text-red">No safe passage</span>
          </div>
          <p className="text-ink-1 text-2xs leading-snug">{plan.reason}</p>
          <p className="text-ink-3 text-2xs leading-snug">
            {plan.lattice.note}, for a {plan.boat_class}.
          </p>
          {blockers.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => setShowBlockers((value) => !value)}
                className="text-ink-2 hover:text-ink-0 text-2xs underline decoration-dotted transition-colors"
              >
                {showBlockers ? 'hide' : 'show'} what blocks it ({blockers.length})
              </button>
              {showBlockers && (
                <ul className="space-y-0.5">
                  {blockers.map((reason, index) => (
                    <li key={index} className="text-ink-1 flex items-start gap-1 text-2xs">
                      <span className="bg-red mt-1 h-1 w-1 shrink-0 rounded-full" />
                      <span>{reason}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}

      {plan?.degraded && (
        <p className="text-amber flex items-start gap-1 text-2xs leading-snug">
          <AlertTriangle className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
          {plan.degraded}
        </p>
      )}

      {refused.length > 0 && plan?.ok && (
        <p className="text-ink-3 text-2xs leading-snug">
          {refused.length} cell(s) on the direct line were refused; they are outlined in red on the
          map.
        </p>
      )}
    </div>
  );
}
