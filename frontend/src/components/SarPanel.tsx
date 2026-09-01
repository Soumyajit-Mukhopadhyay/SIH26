/**
 * SAR mode: a drift search area from a last-known position.
 *
 * The interface is built around one refusal. It never shows a single predicted
 * position as *the* answer, even though the model produces a mean position and
 * it would be the easiest number to put in large type. A coordinated search that
 * concentrates on one point because software offered one is a worse outcome than
 * no software at all, so the mean is shown small, next to the 50% and 95% areas,
 * and labelled as the centre of a distribution rather than a location.
 *
 * The second thing it insists on is the error budget. The panel says which term
 * is setting the size of the area — for a person in the water it is the current
 * analysis's own error, not the object's leeway — because a coordinator who knows
 * that can decide whether a better current field would change their plan.
 */

import { useState } from 'react';
import {
  Clock,
  Compass,
  LifeBuoy,
  Loader2,
  Ruler,
  Search,
  ShieldAlert,
  TriangleAlert,
} from 'lucide-react';
import { clsx } from 'clsx';
import { ApiError, api } from '@/lib/api';
import type { DriftPlan } from '@/lib/types';

/** Hours worth offering. Beyond 48 the forcing runs out and the area outgrows
 *  any search that could be mounted, so the API refuses it too. */
const HOUR_CHOICES = [1, 3, 6, 12, 24];

export function SarPanel({
  origin,
  hours,
  onHours,
  objectClass,
  onObjectClass,
  classes,
  plan,
  onPlan,
  open,
  onToggle,
}: {
  origin: { lat: number; lon: number; label?: string | null } | null;
  hours: number;
  onHours: (hours: number) => void;
  objectClass: string;
  onObjectClass: (code: string) => void;
  classes: { code: string; label: string; note: string }[];
  plan: DriftPlan | null;
  onPlan: (plan: DriftPlan | null) => void;
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (!origin) return;
    setBusy(true);
    setError(null);
    try {
      onPlan(
        await api.sarDrift({
          lat: origin.lat,
          lon: origin.lon,
          hours,
          objectClass,
        }),
      );
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? `${cause.status === 0 ? 'The API is unreachable' : `HTTP ${cause.status}`}: ${cause.message}`
          : 'The drift simulation failed.',
      );
      onPlan(null);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => onToggle(true)}
        className="glass pointer-events-auto flex h-10 w-10 items-center justify-center rounded-lg p-0 transition-colors hover:bg-white/5"
        title="Search and rescue drift"
        aria-label="Search and rescue drift"
      >
        <LifeBuoy className="text-red h-3.5 w-3.5" aria-hidden />
        <span className="sr-only">SAR</span>
      </button>
    );
  }

  const selected = classes.find((c) => c.code === objectClass);
  const wide = plan?.areas?.[plan.areas.length - 1];
  const tight = plan?.areas?.[0];

  return (
    <div className="glass pointer-events-auto w-[21rem] rounded-lg" data-orca="sar-panel">
      <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
        <LifeBuoy className="text-red h-3.5 w-3.5" aria-hidden />
        <span className="label">Search and rescue</span>
        <button
          type="button"
          onClick={() => onToggle(false)}
          className="text-ink-3 hover:text-ink-1 ml-auto text-2xs transition-colors"
        >
          close
        </button>
      </div>

      <div className="space-y-2 px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-2xs">
          <span className="text-ink-3 shrink-0 uppercase">LKP</span>
          <span className="data text-ink-1 min-w-0 flex-1 truncate">
            {origin
              ? (origin.label ?? `${origin.lat.toFixed(3)}°N ${origin.lon.toFixed(3)}°E`)
              : 'click the last known position on the map'}
          </span>
        </div>

        <div>
          <div className="label mb-1 text-2xs">Object</div>
          <select
            value={objectClass}
            onChange={(event) => {
              onObjectClass(event.target.value);
              onPlan(null);
            }}
            className="bg-abyss-0 border-hairline text-ink-1 focus:border-cyan/50 w-full rounded border px-1.5 py-1 text-2xs outline-none"
          >
            {classes.map((option) => (
              <option key={option.code} value={option.code}>
                {option.label}
              </option>
            ))}
          </select>
          {selected?.note && (
            <p className="text-ink-3 mt-1 text-2xs leading-snug">{selected.note}</p>
          )}
        </div>

        <div>
          <div className="label mb-1 text-2xs">Time since LKP</div>
          <div className="flex gap-1">
            {HOUR_CHOICES.map((choice) => (
              <button
                key={choice}
                type="button"
                onClick={() => {
                  onHours(choice);
                  onPlan(null);
                }}
                className={clsx(
                  'flex-1 rounded border px-1 py-1 text-2xs transition-colors',
                  choice === hours
                    ? 'border-red/50 bg-red/15 text-red'
                    : 'border-hairline text-ink-2 hover:text-ink-0',
                )}
              >
                {choice} h
              </button>
            ))}
          </div>
        </div>

        <button
          type="button"
          onClick={run}
          disabled={!origin || busy}
          className="border-red/45 bg-red/15 text-red hover:bg-red/25 flex w-full items-center justify-center gap-1.5 rounded border px-2 py-1.5 text-2xs transition-colors disabled:opacity-30"
        >
          {busy ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              advecting 2000 particles…
            </>
          ) : (
            <>
              <Search className="h-3 w-3" aria-hidden />
              compute the search area
            </>
          )}
        </button>

        {error && (
          <p className="text-red flex items-start gap-1 text-2xs leading-snug">
            <ShieldAlert className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
            {error}
          </p>
        )}

        {plan && (
          <>
            <div className="border-hairline grid grid-cols-2 gap-2 border-t pt-2">
              <div>
                <div className="label flex items-center gap-1 text-2xs">
                  <Search className="h-2.5 w-2.5" aria-hidden />
                  95% area
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="data text-red text-sm">{wide?.area_km2}</span>
                  <span className="text-ink-3 text-2xs">km²</span>
                </div>
              </div>
              <div>
                <div className="label flex items-center gap-1 text-2xs">
                  <Search className="h-2.5 w-2.5" aria-hidden />
                  50% area
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="data text-amber text-sm">{tight?.area_km2}</span>
                  <span className="text-ink-3 text-2xs">km²</span>
                </div>
              </div>
              <div>
                <div className="label flex items-center gap-1 text-2xs">
                  <Ruler className="h-2.5 w-2.5" aria-hidden />
                  Drifted
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="data text-ink-0 text-sm">{plan.displacement_nm}</span>
                  <span className="text-ink-3 text-2xs">nm</span>
                </div>
              </div>
              <div>
                <div className="label flex items-center gap-1 text-2xs">
                  <Compass className="h-2.5 w-2.5" aria-hidden />
                  Toward
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="data text-ink-0 text-sm">{plan.bearing}</span>
                  <span className="text-ink-3 text-2xs">{plan.bearing_deg}°</span>
                </div>
              </div>
            </div>

            {/* The mean position, deliberately small. It is the centre of a
                distribution, not a place to send a boat. */}
            <p className="text-ink-3 text-2xs leading-snug">
              Distribution centre {plan.mean_position[1].toFixed(3)}°N{' '}
              {plan.mean_position[0].toFixed(3)}°E — the middle of the cloud, not a position.
              Search the areas.
            </p>

            {plan.spread_sources && (
              <p className="text-ink-2 flex items-start gap-1 text-2xs leading-snug">
                <Clock className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
                <span>
                  The area's size is set by{' '}
                  <span className="text-ink-0">{plan.spread_sources.dominant}</span> (
                  {plan.spread_sources.current_field_error_ms} m/s of current error is{' '}
                  {plan.spread_sources.current_field_error_km_1sigma} km over {plan.hours} h).{' '}
                  {plan.diagnostics.field_samples_taken} field samples along the track.
                </span>
              </p>
            )}

            {plan.diagnostics.warning && (
              <p className="text-amber flex items-start gap-1 text-2xs leading-snug">
                <TriangleAlert className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
                {plan.diagnostics.warning}
              </p>
            )}

            <p className="text-red/85 border-red/30 bg-red/6 rounded border px-2 py-1.5 text-2xs leading-snug">
              A search area, never a position. Supplements and never replaces the Indian Coast
              Guard's own SAR planning — call <span className="data">1554</span> or the nearest
              MRCC.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
