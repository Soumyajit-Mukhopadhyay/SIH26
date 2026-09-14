/**
 * Distress mode: someone is in the water, and this is the next ninety seconds.
 *
 * The design brief is different from every other panel in ORCA. Elsewhere the
 * reader is browsing; here they are already holding a phone and the interface's
 * only job is to get four things in front of them in the order they need them:
 *
 *   1. The number to dial, in the largest type on the screen.
 *   2. Which centre that reaches, and how far away it is.
 *   3. How to actually run the search — spacing, pattern, hours, odds.
 *   4. How much worse it gets while they wait.
 *
 * Two deliberate refusals carried over from the SAR panel and made louder here:
 *
 * The datum is never the headline. It is the centre of a distribution, and a
 * search that concentrates on it because software offered a point is worse than
 * no software. It appears once, small, under the areas.
 *
 * Nothing is sent. The panel assembles the call; a human makes it. There is no
 * button here that contacts anyone, and the copy says so, because a system that
 * telephones a Coast Guard station on a misclicked map is a worse failure than
 * one that does nothing.
 */

import { useState } from "react";
import {
  AlertTriangle,
  CircleAlert,
  Clock,
  Loader2,
  Mail,
  Phone,
  Radio,
  Route,
  Siren,
  Target,
  TrendingUp,
} from "lucide-react";
import { clsx } from "clsx";
import { ApiError, api } from "@/lib/api";
import type { DistressResponse } from "@/lib/types";

/** Hours since last known. Beyond 48 the forcing runs out and the area outgrows
 *  any search that could be mounted, so the API refuses it too. */
const ELAPSED_CHOICES = [0.5, 1, 2, 3, 6, 12, 24];

/** Offered units. A small boat is fastest to the scene and first to be beaten
 *  back by the sea, and the plan changes materially between them — eye height
 *  moves the sweep-width column, which moves the track spacing. */
const UNITS = [
  { code: "ICG-IB", short: "Interceptor" },
  { code: "ICG-FPV", short: "Patrol vessel" },
  { code: "ICG-OPV", short: "Offshore OPV" },
  { code: "FISHING-FLEET", short: "Fishing fleet" },
];

function Stat({
  label,
  value,
  sub,
  tone = "normal",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "normal" | "warn" | "alarm";
}) {
  return (
    <div className="rounded-lg border border-slate-700/60 bg-slate-900/60 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">
        {label}
      </div>
      <div
        className={clsx(
          "font-mono text-lg leading-tight",
          tone === "alarm" && "text-rose-300",
          tone === "warn" && "text-amber-300",
          tone === "normal" && "text-slate-100",
        )}
      >
        {value}
      </div>
      {sub ? (
        <div className="mt-0.5 text-[11px] leading-snug text-slate-400">
          {sub}
        </div>
      ) : null}
    </div>
  );
}

export function DistressPanel({
  origin,
  objectClass,
  classes,
  open,
  onToggle,
  onResult,
}: {
  origin: { lat: number; lon: number; label?: string | null } | null;
  objectClass: string;
  classes: { code: string; label: string; note: string }[];
  open: boolean;
  onToggle: (open: boolean) => void;
  /** Lets the map draw the search rings and the transit track. */
  onResult?: (result: DistressResponse | null) => void;
}) {
  const [elapsed, setElapsed] = useState(1);
  const [unitCode, setUnitCode] = useState("ICG-FPV");
  const [units, setUnits] = useState(1);
  const [result, setResult] = useState<DistressResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const raise = async () => {
    if (!origin) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.distressAlert({
        lat: origin.lat,
        lon: origin.lon,
        hoursSince: elapsed,
        objectClass,
        unitCode,
        units,
      });
      setResult(next);
      onResult?.(next);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? `${cause.status === 0 ? "The API is unreachable" : `HTTP ${cause.status}`}: ${cause.detail || cause.message}`
          : "The distress response could not be assembled.",
      );
      setResult(null);
      onResult?.(null);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => onToggle(true)}
        className="glass pointer-events-auto flex w-full items-center gap-2 rounded-lg border border-rose-800/60 bg-rose-950/30 px-3 py-2 text-left text-sm text-rose-200 transition hover:border-rose-600 hover:bg-rose-950/50"
      >
        <Siren className="h-4 w-4" />
        <span className="font-medium">Person or vessel in distress</span>
        <span className="ml-auto text-xs text-rose-400/80">open</span>
      </button>
    );
  }

  const plan = result?.search_plan;
  const transit = result?.transit;
  const growth = result?.datum_growth;

  return (
    <section
      className="glass pointer-events-auto max-h-[calc(100vh-6rem)] w-[23rem] overflow-y-auto rounded-lg border border-rose-900/60 p-3"
      data-orca="distress-panel"
    >
      <header className="mb-3 flex items-center gap-2">
        <Siren className="h-4 w-4 text-rose-400" />
        <h2 className="text-sm font-semibold text-rose-100">
          Distress response
        </h2>
        <button
          type="button"
          onClick={() => onToggle(false)}
          className="ml-auto text-xs text-slate-500 hover:text-slate-300"
        >
          close
        </button>
      </header>

      {!origin ? (
        <p className="rounded-lg border border-slate-700/60 bg-slate-900/50 px-3 py-2 text-xs text-slate-400">
          Click the map, or search a place, to set the last known position.
        </p>
      ) : (
        <>
          <div className="mb-2 font-mono text-[11px] text-slate-400">
            Last known {origin.lat.toFixed(4)}, {origin.lon.toFixed(4)}
            {origin.label ? ` · ${origin.label}` : ""}
          </div>

          <div className="mb-2">
            <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">
              Time since last known
            </div>
            <div className="flex flex-wrap gap-1">
              {ELAPSED_CHOICES.map((hours) => (
                <button
                  key={hours}
                  type="button"
                  onClick={() => setElapsed(hours)}
                  className={clsx(
                    "rounded-md border px-2 py-1 font-mono text-xs transition",
                    elapsed === hours
                      ? "border-rose-600 bg-rose-950/60 text-rose-100"
                      : "border-slate-700 text-slate-400 hover:border-slate-500",
                  )}
                >
                  {hours < 1 ? `${hours * 60}m` : `${hours}h`}
                </button>
              ))}
            </div>
          </div>

          <div className="mb-2">
            <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">
              Responding unit
            </div>
            <div className="flex flex-wrap gap-1">
              {UNITS.map((unit) => (
                <button
                  key={unit.code}
                  type="button"
                  onClick={() => setUnitCode(unit.code)}
                  className={clsx(
                    "rounded-md border px-2 py-1 text-xs transition",
                    unitCode === unit.code
                      ? "border-sky-600 bg-sky-950/50 text-sky-100"
                      : "border-slate-700 text-slate-400 hover:border-slate-500",
                  )}
                >
                  {unit.short}
                </button>
              ))}
              <label className="ml-auto flex items-center gap-1 text-xs text-slate-400">
                units
                <input
                  type="number"
                  min={1}
                  max={12}
                  value={units}
                  onChange={(event) =>
                    setUnits(
                      Math.min(
                        12,
                        Math.max(1, Number(event.target.value) || 1),
                      ),
                    )
                  }
                  className="w-12 rounded border border-slate-700 bg-slate-900 px-1 py-0.5 text-center font-mono text-xs text-slate-200"
                />
              </label>
            </div>
          </div>

          <p className="mb-2 text-[11px] text-slate-500">
            Object:{" "}
            {classes.find((c) => c.code === objectClass)?.label ?? objectClass}
          </p>

          <button
            type="button"
            onClick={raise}
            disabled={busy}
            className="mb-3 flex w-full items-center justify-center gap-2 rounded-lg bg-rose-700 px-3 py-2 text-sm font-semibold text-white transition hover:bg-rose-600 disabled:opacity-50"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Siren className="h-4 w-4" />
            )}
            {busy ? "Assembling response…" : "Raise distress response"}
          </button>
        </>
      )}

      {error ? (
        <p className="mb-2 rounded-lg border border-rose-800/60 bg-rose-950/40 px-3 py-2 text-xs text-rose-200">
          {error}
        </p>
      ) : null}

      {result ? (
        <div className="space-y-3">
          {/* ---------------- 1. The number to dial ---------------- */}
          <div className="rounded-xl border border-rose-700/70 bg-rose-950/40 p-3">
            <div className="flex items-baseline gap-3">
              <Phone className="h-5 w-5 shrink-0 text-rose-300" />
              <div className="font-mono text-4xl font-bold leading-none text-rose-100">
                {result.first_call.number}
              </div>
              <div className="text-xs text-rose-300/80">
                nationwide maritime distress
                <br />
                toll free, 24 h
              </div>
            </div>
            <p className="mt-2 text-[11px] leading-snug text-rose-200/80">
              {result.first_call.why}
            </p>
          </div>

          {/* ---------------- 2. Who it reaches ---------------- */}
          <div>
            <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500">
              <Radio className="h-3 w-3" />
              Rescue centres · coordinating {result.coordinating_mrcc}
            </div>
            <div className="space-y-1.5">
              {result.notify.map((contact, index) => (
                <div
                  key={contact.name}
                  className={clsx(
                    "rounded-lg border px-2.5 py-2",
                    index === 0
                      ? "border-sky-700/70 bg-sky-950/30"
                      : "border-slate-700/60 bg-slate-900/40",
                  )}
                >
                  <div className="flex items-baseline gap-2">
                    <span
                      className={clsx(
                        "text-sm font-medium",
                        index === 0 ? "text-sky-100" : "text-slate-300",
                      )}
                    >
                      {contact.name}
                    </span>
                    <span className="font-mono text-xs text-slate-400">
                      {contact.distance_km.toFixed(0)} km
                    </span>
                    {index === 0 ? (
                      <span className="rounded bg-sky-800/60 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-sky-200">
                        nearest
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[11px] text-slate-400">
                    {contact.telephone.slice(1).map((number) => (
                      <a
                        key={number}
                        href={`tel:${number.replace(/[^+\d]/g, "")}`}
                        className="hover:text-sky-300"
                      >
                        {number}
                      </a>
                    ))}
                    {contact.inmarsat_c ? (
                      <span title="Inmarsat-C — works when the mobile network does not">
                        SES {contact.inmarsat_c}
                      </span>
                    ) : null}
                  </div>
                  {contact.email[0] ? (
                    <div className="mt-0.5 flex items-center gap-1 text-[10px] text-slate-500">
                      <Mail className="h-2.5 w-2.5" />
                      {contact.email[0]}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>

          {/* ---------------- 3. How to search ---------------- */}
          {plan ? (
            <div>
              <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500">
                <Target className="h-3 w-3" />
                Search plan · {plan.pattern.code} {plan.pattern.name}
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                <Stat
                  label="Track spacing"
                  value={`${plan.track_spacing_km.toFixed(2)} km`}
                  sub={`${plan.track_spacing_nm.toFixed(2)} NM between legs`}
                  tone={plan.track_spacing_floored ? "warn" : "normal"}
                />
                <Stat
                  label="Time to cover"
                  value={`${plan.search_hours.toFixed(1)} h`}
                  sub={`${plan.track_length_nm.toFixed(0)} NM of track, ${plan.units_assigned} unit${plan.units_assigned > 1 ? "s" : ""}`}
                  tone={
                    plan.search_hours > plan.unit.on_scene_endurance_h
                      ? "warn"
                      : "normal"
                  }
                />
                <Stat
                  label="Search area (95%)"
                  value={`${plan.area_km2.toFixed(0)} km²`}
                  sub={`drift ${result.search_area.displacement_km.toFixed(1)} km ${result.search_area.bearing}`}
                />
                <Stat
                  label="Detection odds"
                  value={`${(plan.probability_of_detection * 100).toFixed(0)}%`}
                  sub={`coverage ${plan.coverage_achieved.toFixed(2)}`}
                  tone={plan.probability_of_detection < 0.5 ? "warn" : "normal"}
                />
              </div>

              <p className="mt-1.5 text-[11px] leading-snug text-slate-300">
                {plan.how_to_read}
              </p>

              <div className="mt-1.5 rounded-lg border border-slate-700/50 bg-slate-900/40 px-2.5 py-1.5 text-[10px] leading-snug text-slate-400">
                <span className="text-slate-500">Sweep width</span>{" "}
                <span className="font-mono text-slate-300">
                  {plan.sweep_width.uncorrected_nm.toFixed(2)} NM
                </span>{" "}
                from Table H-19 at {plan.sweep_width.visibility_nm.toFixed(1)}{" "}
                NM visibility, then{" "}
                <span className="font-mono text-slate-300">
                  ×{plan.sweep_width.weather_factor}
                </span>{" "}
                for weather ={" "}
                <span className="font-mono text-slate-200">
                  {plan.sweep_width.corrected_nm.toFixed(2)} NM
                </span>
                . {plan.sweep_width.weather_note}
              </div>

              <p className="mt-1 text-[10px] leading-snug text-slate-500">
                {plan.pattern.why}
              </p>

              {plan.limits.length > 0 ? (
                <ul className="mt-1.5 space-y-1">
                  {plan.limits.map((limit) => (
                    <li
                      key={limit}
                      className="flex gap-1.5 rounded-lg border border-amber-800/50 bg-amber-950/25 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200"
                    >
                      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                      <span>{limit}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1.5 rounded-lg border border-emerald-800/50 bg-emerald-950/25 px-2.5 py-1.5 text-[11px] text-emerald-200">
                  One {plan.unit.label.toLowerCase()} closes this plan within
                  its endurance.
                </p>
              )}

              <p className="mt-1.5 text-[10px] leading-snug text-slate-500">
                {plan.pos_note}
              </p>
            </div>
          ) : null}

          {/* ---------------- 4. Getting there, and the clock ---------------- */}
          {transit ? (
            <div>
              <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500">
                <Route className="h-3 w-3" />
                Transit from {result.first_call.nearest_centre}
              </div>
              <div
                className={clsx(
                  "rounded-lg border px-2.5 py-2",
                  transit.routed
                    ? "border-slate-700/60 bg-slate-900/40"
                    : "border-amber-800/60 bg-amber-950/25",
                )}
              >
                {transit.total_hours != null ? (
                  <div className="flex items-baseline gap-2">
                    <Clock className="h-3.5 w-3.5 text-slate-400" />
                    <span className="font-mono text-xl text-slate-100">
                      {transit.total_hours.toFixed(1)} h
                    </span>
                    <span className="text-[11px] text-slate-400">
                      {transit.distance_nm?.toFixed(1)} NM at{" "}
                      {transit.transit_speed_kn} kn
                      {transit.detour_pct != null && transit.routed
                        ? ` · ${transit.detour_pct.toFixed(0)}% off the direct line`
                        : ""}
                    </span>
                  </div>
                ) : null}
                <p
                  className={clsx(
                    "mt-1 text-[10px] leading-snug",
                    transit.routed ? "text-slate-400" : "text-amber-200",
                  )}
                >
                  {transit.eta_note}
                </p>
                {!transit.routed ? (
                  <p className="mt-1 text-[10px] leading-snug text-amber-300/80">
                    {transit.reason}
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          {growth?.estimated ? (
            <div className="flex gap-1.5 rounded-lg border border-orange-800/50 bg-orange-950/25 px-2.5 py-2 text-[11px] leading-snug text-orange-200">
              <TrendingUp className="mt-0.5 h-3 w-3 shrink-0" />
              <span>{growth.why_it_matters}</span>
            </div>
          ) : null}

          {/* The datum: small, last, and labelled. Never the headline. */}
          <div className="rounded-lg border border-slate-800 bg-slate-900/30 px-2.5 py-1.5">
            <div className="font-mono text-[11px] text-slate-400">
              datum {result.incident.datum.lat.toFixed(4)},{" "}
              {result.incident.datum.lon.toFixed(4)}
            </div>
            <p className="mt-0.5 text-[10px] leading-snug text-slate-500">
              {result.incident.datum_note}
            </p>
          </div>

          <p className="flex gap-1.5 rounded-lg border border-slate-700/50 bg-slate-900/40 px-2.5 py-2 text-[10px] leading-snug text-slate-400">
            <CircleAlert className="mt-0.5 h-3 w-3 shrink-0 text-slate-500" />
            <span>{result.not_a_dispatch}</span>
          </p>

          <p className="text-[10px] leading-snug text-slate-600">
            {plan?.aircraft_note}
          </p>
        </div>
      ) : null}
    </section>
  );
}
