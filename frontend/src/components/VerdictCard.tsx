/**
 * The verdict card. The most important surface in ORCA.
 *
 * What it has to communicate, in order of priority:
 *
 * 1. The verdict, unmissable.
 * 2. The vetoes, verbatim. "Hs 2.4 m is at or over the 1.5 m limit for your 8.2 m
 *    boat" is the most valuable string in the system: it teaches the user
 *    something true and shows that the reasoning is a computation.
 * 3. What would change it — the part a fisherman actually acts on.
 * 4. The arithmetic, on demand.
 *
 * UNVERIFIABLE is a first-class verdict here, rendered distinctly from NO-GO.
 * "We cannot check" and "it is dangerous" are different messages and collapsing
 * them would be a safety bug, not a UI simplification.
 */

import { useState } from 'react';
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  HelpCircle,
  Info,
  Radio,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { RiskResult, Verdict } from '@/lib/types';
import { ProvenanceBadge } from './ProvenanceBadge';

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
    sub: 'Marginal — go only with a shortened window and a watched forecast',
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
    sub: 'ORCA could not obtain enough data to judge — this is not the same as safe',
    icon: HelpCircle,
    text: 'text-ink-1',
    ring: 'border-hairline-strong',
    glow: '',
    bar: 'bg-ink-2',
  },
};

const COMPONENT_LABELS: Record<string, string> = {
  wave: 'Significant wave height',
  wind: 'Wind speed',
  visibility: 'Visibility',
  lightning: 'Convective potential (CAPE proxy)',
};

export function VerdictCard({ result, compact = false }: { result: RiskResult; compact?: boolean }) {
  const [showMath, setShowMath] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const style = VERDICT_STYLES[result.verdict];
  const Icon = style.icon;

  return (
    <div
      className={clsx('glass overflow-hidden rounded-lg border', style.ring, style.glow)}
      style={{ animation: 'orca-rise 260ms var(--ease-out-instrument)' }}
    >
      {/* ---- verdict ---- */}
      <div className="flex items-start gap-3 p-4 pb-3">
        <Icon className={clsx('mt-0.5 h-7 w-7 shrink-0', style.text)} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2.5">
            <span className={clsx('data text-2xl leading-none font-bold tracking-tight', style.text)}>
              {style.label}
            </span>
            {result.verdict !== 'UNVERIFIABLE' && (
              <span className="data text-ink-1 text-sm">
                {result.index.toFixed(1)}
                <span className="text-ink-3">/100</span>
              </span>
            )}
          </div>
          <p className="text-ink-1 mt-1 text-xs leading-snug">{style.sub}</p>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          className="text-ink-2 hover:bg-abyss-2/70 hover:text-cyan -mt-1 -mr-1 rounded p-1.5 transition-colors"
          aria-expanded={!collapsed}
          aria-label={collapsed ? 'Expand safety verdict' : 'Collapse safety verdict'}
          title={collapsed ? 'Expand safety verdict' : 'Collapse safety verdict'}
        >
          <ChevronDown
            className={clsx('h-4 w-4 transition-transform', !collapsed && 'rotate-180')}
            aria-hidden
          />
        </button>
      </div>

      {!collapsed && (
        <>
      {/* ---- index bar ---- */}
      {result.verdict !== 'UNVERIFIABLE' && (
        <div className="px-4 pb-3">
          <div className="bg-abyss-0 relative h-1.5 overflow-hidden rounded-full">
            <div
              className={clsx('h-full rounded-full transition-[width] duration-700', style.bar)}
              style={{ width: `${result.index}%` }}
            />
            {/* The GO and CAUTION thresholds, marked so the score has meaning. */}
            <div className="absolute inset-y-0 left-[40%] w-px bg-white/25" title="CAUTION ≥ 40" />
            <div className="absolute inset-y-0 left-[70%] w-px bg-white/25" title="GO ≥ 70" />
          </div>
          <div className="text-ink-3 mt-1 flex justify-between font-mono text-2xs">
            <span>0</span>
            <span>caution 40</span>
            <span>go 70</span>
            <span>100</span>
          </div>
        </div>
      )}

      {/* ---- vetoes: the most valuable strings in the system ---- */}
      {result.vetoes.length > 0 && (
        <div className="px-4 py-3">
          <div className="label text-red mb-1.5">
            {result.vetoes.length} hard {result.vetoes.length === 1 ? 'veto' : 'vetoes'} — these
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

      {/* ---- the refusal to guess ---- */}
      {result.escalate && result.escalation_message && (
        <div className="border-amber/25 bg-amber/8 mx-4 mb-3 rounded border px-3 py-2">
          <div className="label text-amber mb-1 flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" aria-hidden />
            Low confidence — escalating rather than guessing
          </div>
          <p className="text-ink-1 text-xs leading-snug">{result.escalation_message}</p>
        </div>
      )}

      {/* ---- what would change it ---- */}
      {result.what_would_change_it.length > 0 && (
        <div className="border-hairline border-t px-4 py-3">
          <div className="label mb-1.5">What would change this</div>
          <ul className="space-y-1">
            {result.what_would_change_it.map((item) => (
              <li key={item} className="text-ink-1 flex items-start gap-2 text-xs leading-snug">
                <span className="text-cyan mt-1.5 h-1 w-1 shrink-0 rounded-full bg-current" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ---- components, with the arithmetic on demand ---- */}
      {!compact && (
        <div className="border-hairline border-t">
          <button
            type="button"
            onClick={() => setShowMath((v) => !v)}
            className="hover:bg-abyss-2/60 flex w-full items-center gap-2 px-4 py-2 text-left transition-colors"
            aria-expanded={showMath}
          >
            <span className="label flex-1">
              Components · {result.boat_class_label}
            </span>
            <ChevronDown
              className={clsx(
                'text-ink-2 h-3.5 w-3.5 transition-transform',
                showMath && 'rotate-180',
              )}
              aria-hidden
            />
          </button>

          {showMath && (
            <div className="px-4 pb-3">
              <table className="w-full text-2xs">
                <thead>
                  <tr className="text-ink-3 border-hairline border-b">
                    <th className="py-1 text-left font-medium">Input</th>
                    <th className="py-1 text-right font-medium">Value</th>
                    <th className="py-1 text-right font-medium">Limit</th>
                    <th className="py-1 text-right font-medium">Score</th>
                    <th className="py-1 text-right font-medium">×w</th>
                  </tr>
                </thead>
                <tbody className="data">
                  {result.components.map((c) => (
                    <tr key={c.name} className="border-hairline/60 border-b last:border-0">
                      <td
                        className={clsx('py-1.5 pr-2', c.exceeded ? 'text-red' : 'text-ink-1')}
                        title={COMPONENT_LABELS[c.name]}
                      >
                        {c.exceeded && <span aria-hidden>⚠ </span>}
                        {c.name}
                      </td>
                      <td className={clsx('py-1.5 text-right', c.exceeded ? 'text-red' : 'text-ink-0')}>
                        {c.value === null ? '—' : `${c.value} ${c.unit}`}
                      </td>
                      <td className="text-ink-2 py-1.5 text-right">
                        {c.limit} {c.unit}
                      </td>
                      <td className="text-ink-0 py-1.5 text-right">{c.score.toFixed(0)}</td>
                      <td className="text-cyan py-1.5 text-right">{c.contribution.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* The formulas. This is the difference between a number and an
                  explanation, and it is cheap to show. */}
              <div className="border-hairline mt-2 space-y-0.5 border-t pt-2">
                {result.components.map((c) => (
                  <div key={c.name} className="text-ink-3 data flex gap-2 text-2xs">
                    <span className="w-16 shrink-0">{c.name}</span>
                    <span className="truncate" title={c.formula}>
                      {c.formula}
                    </span>
                  </div>
                ))}
                <div className="text-ink-1 data flex gap-2 pt-1 text-2xs">
                  <span className="w-16 shrink-0">index</span>
                  <span>
                    {result.components.map((c) => c.contribution.toFixed(1)).join(' + ')} ={' '}
                    <span className="text-cyan">{result.index.toFixed(1)}</span>
                  </span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ---- footer: evidence count, data age, the standing disclaimer ---- */}
      <div className="border-hairline bg-abyss-0/50 border-t px-4 py-2">
        <div className="flex items-center gap-2 text-2xs">
          <Radio className="text-slate-live h-3 w-3 shrink-0" aria-hidden />
          <span className="text-ink-2">
            <span className="data text-ink-1">{result.evidence.length}</span> evidence items
          </span>
          <span className="text-ink-3">·</span>
          <span className="text-ink-2">
            {result.data_age_hours < 0.05 ? (
              <>
                data <span className="data text-ink-1">current</span>
              </>
            ) : (
              <>
                data <span className="data text-ink-1">{result.data_age_hours.toFixed(1)} h</span> old
              </>
            )}
          </span>
          <ProvenanceBadge
            provenance={result.confidence === 'high' ? 'live' : 'cached'}
            size="xs"
            showLabel={false}
            title={`Confidence: ${result.confidence}`}
          />
        </div>
        <p className="text-ink-3 mt-1.5 flex items-start gap-1 text-2xs leading-tight">
          <Info className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
          <span>{result.disclaimer}</span>
        </p>
      </div>
        </>
      )}
    </div>
  );
}
