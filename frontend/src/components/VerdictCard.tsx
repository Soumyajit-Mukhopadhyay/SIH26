/**
 * The safety verdict and the current observations that produced it.
 *
 * This card deliberately shows only present-condition analysis. Forecast-facing
 * suggestions belong in the agent answer, while provenance and source age live
 * in the evidence panel beside this card.
 */

import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronRight,
  HelpCircle,
  MapPin,
} from 'lucide-react';
import { clsx } from 'clsx';
import type {
  GeofenceCheck,
  Proximity,
  RiskComponent,
  RiskResult,
  Verdict,
} from '@/lib/types';

const VERDICT_STYLES: Record<
  Verdict,
  {
    label: string;
    sub: string;
    icon: typeof CheckCircle2;
    text: string;
    ring: string;
    glow: string;
    bar: string;
  }
> = {
  GO: {
    label: 'GO',
    sub: 'Conditions are within your class limits',
    icon: CheckCircle2,
    text: 'text-jade',
    ring: 'border-jade/45',
    glow: 'shadow-[0_0_36px_-8px_rgba(52,211,153,0.42)]',
    bar: 'bg-jade',
  },
  CAUTION: {
    label: 'CAUTION',
    sub: 'Current conditions are marginal for this vessel class',
    icon: AlertTriangle,
    text: 'text-amber',
    ring: 'border-amber/50',
    glow: 'shadow-[0_0_36px_-8px_rgba(245,158,11,0.42)]',
    bar: 'bg-amber',
  },
  'NO-GO': {
    label: 'NO-GO',
    sub: 'At least one hard limit for your vessel class is exceeded',
    icon: Ban,
    text: 'text-red',
    ring: 'border-red/55',
    glow: 'shadow-[0_0_40px_-8px_rgba(239,68,68,0.5)]',
    bar: 'bg-red',
  },
  UNVERIFIABLE: {
    label: 'UNVERIFIABLE',
    sub: 'ORCA could not obtain enough current data to judge; this does not mean safe',
    icon: HelpCircle,
    text: 'text-ink-1',
    ring: 'border-hairline-strong',
    glow: '',
    bar: 'bg-ink-2',
  },
};

const COMPONENT_LABELS: Record<RiskComponent['name'], string> = {
  wave: 'Significant wave height',
  wind: 'Wind speed',
  visibility: 'Visibility',
  lightning: 'Convective-risk proxy',
};

function limitText(component: RiskComponent): string {
  if (component.name === 'visibility') {
    return `minimum ${component.limit} ${component.unit}`;
  }
  if (component.name === 'lightning') {
    return `hard veto at ${component.limit}${component.unit}`;
  }
  return `limit ${component.limit} ${component.unit}`;
}

function CurrentConditions({ result, landPoint = false }: { result: RiskResult; landPoint?: boolean }) {
  const capeEvidence = result.evidence.find(
    (item) => item.variable === 'convective_energy' && typeof item.value === 'number',
  );
  const cape = typeof capeEvidence?.value === 'number' ? capeEvidence.value : null;
  const components = landPoint
    ? result.components.filter((component) => component.name !== 'wave')
    : result.components;

  return (
    <div className="border-hairline border-t px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="label">
          {landPoint ? 'Atmospheric analysis' : 'Current-condition analysis'}
        </span>
        {!landPoint && (
          <span className="text-ink-3 truncate text-right text-2xs">
            {result.boat_class_label}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2">
        {components.map((component) => (
          <div
            key={component.name}
            className={clsx(
              'rounded border px-2.5 py-2',
              !landPoint && component.exceeded
                ? 'border-red/35 bg-red/8'
                : component.value === null
                  ? 'border-amber/25 bg-amber/5'
                  : 'border-hairline bg-abyss-0/35',
            )}
          >
            <div className="text-ink-2 truncate text-2xs uppercase tracking-[0.08em]">
              {COMPONENT_LABELS[component.name]}
            </div>
            <div className="mt-0.5 flex items-baseline justify-between gap-1.5">
              <span
                className={clsx(
                  'data text-sm font-semibold',
                  !landPoint && component.exceeded
                    ? 'text-red'
                    : component.value === null
                      ? 'text-amber'
                      : 'text-ink-0',
                )}
              >
                {component.value === null ? 'NO DATA' : `${component.value} ${component.unit}`}
              </span>
              <span
                className={clsx(
                  'shrink-0 text-[9px] font-semibold uppercase',
                  !landPoint && component.exceeded ? 'text-red' : 'text-jade',
                )}
              >
                {component.value === null
                  ? 'unverified'
                  : landPoint
                    ? 'observed'
                    : component.exceeded
                    ? 'limit exceeded'
                    : 'within limit'}
              </span>
            </div>
            <p className="text-ink-3 mt-1 text-[10px] leading-snug">
              {landPoint ? (
                component.name === 'lightning' && cape !== null ? (
                  <>Derived from CAPE {cape} J/kg; not a detected lightning strike</>
                ) : (
                  <>Atmospheric value at the selected land grid cell</>
                )
              ) : (
                <>
                  Safety score {component.score.toFixed(0)}/100; {limitText(component)}
                  {component.name === 'lightning' && cape !== null
                    ? `; derived from CAPE ${cape} J/kg, not a detected strike`
                    : ''}
                </>
              )}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function BoundaryRow({ proximity }: { proximity: Proximity }) {
  const isIndiaEez = proximity.kind === 'eez' || proximity.fence === 'eez_india';
  const dangerous = isIndiaEez ? !proximity.inside : proximity.kind === 'imbl';
  const label = isIndiaEez
    ? proximity.inside
      ? "Inside India's EEZ"
      : "Outside India's EEZ"
    : proximity.name;

  return (
    <div
      className={clsx(
        'rounded border px-2.5 py-2',
        dangerous ? 'border-red/35 bg-red/8' : 'border-jade/25 bg-jade/5',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className={clsx('text-xs font-medium', dangerous ? 'text-red' : 'text-jade')}>
          {label}
        </span>
        <span className="data text-ink-1 shrink-0 text-2xs">
          {proximity.distance_km.toFixed(1)} km to line
        </span>
      </div>
      <p className="text-ink-2 mt-1 text-[10px] leading-snug">
        {isIndiaEez
          ? dangerous
            ? 'Current-position jurisdiction warning; this is separate from the weather score.'
            : 'Current position is within the indexed Indian maritime jurisdiction polygon.'
          : proximity.narrative}
      </p>
    </div>
  );
}

function CurrentBoundary({ check }: { check: GeofenceCheck | null | undefined }) {
  if (!check) return null;

  const indiaEez = check.proximities.find(
    (item) => item.kind === 'eez' || item.fence === 'eez_india',
  );
  const nearbyImbl = check.proximities.filter(
    (item) =>
      item.kind === 'imbl' &&
      (item.distance_km <= 25 || ['approaching', 'crossed', 'exited'].includes(item.state)),
  );
  const rows = [...(indiaEez ? [indiaEez] : []), ...nearbyImbl];

  if (rows.length === 0) return null;

  return (
    <div className="border-hairline border-t px-4 py-3">
      <div className="label mb-2">Current boundary check</div>
      <div className="space-y-2">
        {rows.map((row) => (
          <BoundaryRow key={row.fence} proximity={row} />
        ))}
      </div>
    </div>
  );
}

export function VerdictCard({
  result,
  geofence,
  landPoint = false,
  onCollapse,
}: {
  result: RiskResult;
  geofence?: GeofenceCheck | null;
  landPoint?: boolean;
  onCollapse?: () => void;
}) {
  const style = VERDICT_STYLES[result.verdict];
  const Icon = landPoint ? MapPin : style.icon;
  const summary = landPoint
    ? 'Atmospheric conditions only — maritime safety, PFZ and EEZ checks do not apply on land'
    : result.verdict === 'NO-GO' && result.vetoes.length === 0
      ? 'The combined current-condition score is below the safe threshold'
      : style.sub;

  return (
    <div
      className={clsx(
        'glass overflow-hidden rounded-lg border',
        landPoint ? 'border-cyan/40' : style.ring,
        !landPoint && style.glow,
      )}
      style={{ animation: 'orca-rise 260ms var(--ease-out-instrument)' }}
    >
      <div className="flex items-start gap-3 p-4 pb-3">
        <Icon
          className={clsx('mt-0.5 h-7 w-7 shrink-0', landPoint ? 'text-cyan' : style.text)}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2.5">
            <span
              className={clsx(
                'data text-2xl leading-none font-bold tracking-tight',
                landPoint ? 'text-cyan' : style.text,
              )}
            >
              {landPoint ? 'LAND POINT' : style.label}
            </span>
            {!landPoint && result.verdict !== 'UNVERIFIABLE' && (
              <span className="data text-ink-1 text-sm">
                {result.index.toFixed(1)}
                <span className="text-ink-3">/100</span>
              </span>
            )}
          </div>
          <p className="text-ink-1 mt-1 text-xs leading-snug">{summary}</p>
        </div>
        <button
          type="button"
          onClick={onCollapse}
          className="text-ink-2 hover:bg-abyss-2/70 hover:text-cyan -mt-1 -mr-1 rounded p-1.5 transition-colors"
          aria-label="Collapse safety verdict horizontally"
          title="Hide this card and expose more of the sea"
        >
          <ChevronRight className="h-4 w-4" aria-hidden />
        </button>
      </div>

      {!landPoint && result.verdict !== 'UNVERIFIABLE' && (
        <div className="px-4 pb-3">
          <div className="bg-abyss-0 relative h-1.5 overflow-hidden rounded-full">
            <div
              className={clsx('h-full rounded-full transition-[width] duration-700', style.bar)}
              style={{ width: `${result.index}%` }}
            />
            <div className="absolute inset-y-0 left-[40%] w-px bg-white/25" title="CAUTION at 40" />
            <div className="absolute inset-y-0 left-[70%] w-px bg-white/25" title="GO at 70" />
          </div>
          <div className="text-ink-3 mt-1 flex justify-between font-mono text-2xs">
            <span>0</span>
            <span>caution 40</span>
            <span>go 70</span>
            <span>100</span>
          </div>
        </div>
      )}

      {!landPoint && result.vetoes.length > 0 && (
        <div className="px-4 py-3">
          <div className="label text-red mb-1.5">
            {result.vetoes.length} hard {result.vetoes.length === 1 ? 'veto' : 'vetoes'}; these
            override the score
          </div>
          <ul className="space-y-1.5">
            {result.vetoes.map((veto) => (
              <li key={veto} className="flex items-start gap-2 text-xs leading-snug">
                <Ban className="text-red mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                <span className="text-ink-0">{veto}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!landPoint && result.escalate && result.escalation_message && (
        <div className="border-amber/25 bg-amber/8 mx-4 mb-3 rounded border px-3 py-2">
          <div className="label text-amber mb-1 flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" aria-hidden />
            Current data is insufficient
          </div>
          <p className="text-ink-1 text-xs leading-snug">{result.escalation_message}</p>
        </div>
      )}

      <CurrentConditions result={result} landPoint={landPoint} />
      {!landPoint && <CurrentBoundary check={geofence} />}
    </div>
  );
}
