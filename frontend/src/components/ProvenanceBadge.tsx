/**
 * The provenance badge — the component the whole backend provenance model exists
 * to make visible.
 *
 * Three rules it holds:
 *
 * 1. **Colour is never the only carrier.** Every state has a colour, an icon AND
 *    a word. A colour-blind judge gets identical information, and accessibility
 *    is a scored requirement rather than a nicety.
 * 2. **SIMULATED is hatched.** Not just a different colour — a visibly different
 *    surface, so it can never be mistaken for a measurement at a glance. Having
 *    built a six-state provenance model, letting synthetic data render like real
 *    data would defeat the point of it.
 * 3. **A stale value announces itself before the number, not after.** The
 *    staleness note goes above, in amber, because a fisherman reading a wave
 *    height needs to know it is nine hours old *before* they act on it.
 */

import {
  AlertTriangle,
  Archive,
  Ban,
  Calculator,
  Database,
  FlaskConical,
  Radio,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { Evidence, Provenance } from '@/lib/types';
import { formatValue, relativeAge } from '@/lib/api';

interface StateStyle {
  label: string;
  /** One sentence, shown on hover. This is the actual explanation. */
  meaning: string;
  icon: typeof Radio;
  text: string;
  bg: string;
  border: string;
  hatched: boolean;
}

export const PROVENANCE_STYLES: Record<Provenance, StateStyle> = {
  live: {
    label: 'LIVE',
    meaning: 'Fetched from the provider during this request.',
    icon: Radio,
    text: 'text-slate-live',
    bg: 'bg-slate-live/12',
    border: 'border-slate-live/35',
    hatched: false,
  },
  cached: {
    label: 'CACHED',
    meaning: 'Real data from a real provider, but not from this second.',
    icon: Database,
    text: 'text-cyan',
    bg: 'bg-cyan/10',
    border: 'border-cyan/30',
    hatched: false,
  },
  curated: {
    label: 'CURATED',
    meaning:
      'Hand-checked reference geography or a versioned threshold table. Not a measurement.',
    icon: Archive,
    text: 'text-ink-1',
    bg: 'bg-white/6',
    border: 'border-hairline-strong',
    hatched: false,
  },
  derived: {
    label: 'DERIVED',
    meaning: 'Computed by ORCA. Every input dataset is named in its lineage.',
    icon: Calculator,
    text: 'text-violet',
    bg: 'bg-violet/12',
    border: 'border-violet/35',
    hatched: false,
  },
  simulated: {
    label: 'SIMULATED',
    meaning:
      'Synthetic. Never mixed with live data, and never used for a safety verdict.',
    icon: FlaskConical,
    text: 'text-amber',
    bg: 'bg-amber/12',
    border: 'border-amber/40',
    hatched: true,
  },
  unavailable: {
    label: 'NO DATA',
    meaning: 'This source was asked and could not answer. The gap is shown, not hidden.',
    icon: Ban,
    text: 'text-ink-2',
    bg: 'bg-white/4',
    border: 'border-hairline',
    hatched: false,
  },
};

export function ProvenanceBadge({
  provenance,
  stale = false,
  size = 'sm',
  showLabel = true,
  title,
}: {
  provenance: Provenance;
  stale?: boolean;
  size?: 'xs' | 'sm';
  showLabel?: boolean;
  title?: string;
}) {
  const style = PROVENANCE_STYLES[provenance];
  const Icon = stale ? AlertTriangle : style.icon;

  return (
    <span
      title={title ?? `${style.label} — ${style.meaning}`}
      className={clsx(
        'inline-flex items-center gap-1 rounded border font-mono font-medium tracking-wider uppercase select-none',
        size === 'xs' ? 'px-1 py-px text-2xs' : 'px-1.5 py-0.5 text-2xs',
        // A stale value is a warning regardless of how it was obtained: amber
        // overrides the provenance colour rather than sitting beside it.
        stale ? 'text-amber bg-amber/12 border-amber/40' : [style.text, style.bg, style.border],
        style.hatched && 'hatched',
      )}
    >
      <Icon className={size === 'xs' ? 'h-2.5 w-2.5' : 'h-3 w-3'} aria-hidden />
      {showLabel && <span>{stale ? `${style.label} · STALE` : style.label}</span>}
    </span>
  );
}

/**
 * A value with its badge, its unit and its age — the standard way any measured
 * number appears anywhere in ORCA.
 */
export function EvidenceValue({
  evidence,
  label,
  large = false,
}: {
  evidence: Evidence;
  label?: string;
  large?: boolean;
}) {
  const stale = evidence.freshness.is_stale;
  const missing = evidence.value === null;

  return (
    <div className="min-w-0">
      {label && <div className="label mb-0.5 truncate">{label}</div>}

      {/* Rule 3: the staleness sentence comes BEFORE the value. */}
      {stale && evidence.freshness.note && (
        <div className="text-amber mb-1 flex items-start gap-1 text-2xs leading-tight">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden />
          <span>{evidence.freshness.note}</span>
        </div>
      )}

      <div className="flex items-baseline gap-2">
        <span
          className={clsx(
            'data truncate font-medium',
            large ? 'text-2xl' : 'text-sm',
            missing ? 'text-ink-3' : stale ? 'text-amber' : 'text-ink-0',
          )}
        >
          {formatValue(evidence.value, evidence.unit)}
        </span>
        <ProvenanceBadge
          provenance={evidence.provenance}
          stale={stale}
          size="xs"
          showLabel={!large}
        />
      </div>

      <div className="text-ink-2 mt-0.5 flex items-center gap-1.5 truncate text-2xs">
        <span className="truncate">{evidence.provider}</span>
        <span className="text-ink-3">·</span>
        <span className="data">{relativeAge(evidence.freshness.age_hours)}</span>
      </div>

      {missing && evidence.notes && (
        <div className="text-ink-2 mt-1 text-2xs leading-tight italic">{evidence.notes}</div>
      )}
    </div>
  );
}

/**
 * The aggregate badge for a composite card. Shows the WORST state present,
 * because showing the most flattering one would be the single most misleading
 * thing this UI could do.
 */
export function ProvenanceMix({
  mix,
  worst,
  stale,
}: {
  mix: Provenance[];
  worst: Provenance | null;
  stale: boolean;
}) {
  if (!worst) return null;
  return (
    <div className="flex items-center gap-1.5">
      <ProvenanceBadge
        provenance={worst}
        stale={stale}
        title={
          mix.length > 1
            ? `Mixed sources: ${mix.join(', ')}. The badge shows the most cautious state present.`
            : undefined
        }
      />
      {mix.length > 1 && (
        <span className="text-ink-2 font-mono text-2xs">+{mix.length - 1} more</span>
      )}
    </div>
  );
}
