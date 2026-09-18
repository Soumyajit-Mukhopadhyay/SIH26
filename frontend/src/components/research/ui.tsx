import { Check, Copy, Loader2 } from 'lucide-react';
import { clsx } from 'clsx';
import { useState, type ReactNode } from 'react';

import type { DatasetKind, ResearchDataset } from './types';

/* ------------------------------------------------------------ typography */

/** A section title: the largest text in the workspace, and still only 15px. */
export function Title({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <h2 className={clsx('text-ink-0 text-[15px] font-semibold tracking-tight', className)}>
      {children}
    </h2>
  );
}

/** One quiet line under a title. Where the old UI wrote a paragraph. */
export function Lede({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={clsx('text-ink-1 mt-1 text-xs leading-relaxed', className)}>{children}</p>;
}

/** Column header / field label in the instrument small-caps idiom. */
export function ColLabel({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <div className={clsx('label', className)} title={title}>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------- buttons */

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  busy?: boolean;
  kind?: 'primary' | 'secondary' | 'ghost';
  type?: 'button' | 'submit';
  className?: string;
  title?: string;
};

export function Button({
  children,
  onClick,
  disabled,
  busy,
  kind = 'secondary',
  type = 'button',
  className,
  title,
}: ButtonProps) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || busy}
      title={title}
      className={clsx(
        'inline-flex h-8 items-center gap-1.5 rounded px-3 text-xs font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-40',
        kind === 'primary' && 'bg-cyan text-abyss-0 hover:bg-cyan/85',
        kind === 'secondary' &&
          'border-hairline-strong text-ink-0 hover:bg-abyss-2 border bg-transparent',
        kind === 'ghost' && 'text-ink-1 hover:text-ink-0 hover:bg-abyss-2',
        className,
      )}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
      {children}
    </button>
  );
}

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      kind="ghost"
      className="h-7 px-2"
      onClick={() => {
        void navigator.clipboard.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? (
        <Check className="text-cyan h-3.5 w-3.5" aria-hidden />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden />
      )}
      {done ? 'Copied' : label}
    </Button>
  );
}

/* ------------------------------------------------------------- status */

/**
 * The single tag treatment in the workspace. Nothing is coloured unless the
 * colour carries meaning: amber = costs the reader something (an account, a
 * key), cyan = ORCA can hand the data over itself.
 */
export function Tag({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode;
  tone?: 'neutral' | 'cyan' | 'amber' | 'red' | 'muted';
  className?: string;
}) {
  return (
    <span
      className={clsx(
        'inline-flex h-[18px] items-center rounded-sm px-1.5 text-[10.5px] leading-none font-medium whitespace-nowrap',
        tone === 'neutral' && 'bg-abyss-2 text-ink-1',
        tone === 'muted' && 'text-ink-2 border-hairline border',
        tone === 'cyan' && 'bg-cyan/12 text-cyan',
        tone === 'amber' && 'bg-amber/12 text-amber',
        tone === 'red' && 'bg-red/12 text-red',
        className,
      )}
    >
      {children}
    </span>
  );
}

/** A 6px status dot: the quietest way to say ready / not / partial. */
export function Dot({
  tone,
  className,
}: {
  tone: 'cyan' | 'amber' | 'red' | 'muted';
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={clsx(
        'inline-block h-1.5 w-1.5 shrink-0 rounded-full',
        tone === 'cyan' && 'bg-cyan',
        tone === 'amber' && 'bg-amber',
        tone === 'red' && 'bg-red',
        tone === 'muted' && 'bg-ink-3',
        className,
      )}
    />
  );
}

export function accessLabel(dataset: Pick<ResearchDataset, 'access' | 'servable'>) {
  if (dataset.servable) return { text: 'Integrated', tone: 'cyan' as const };
  if (dataset.access === 'free-signup') return { text: 'Free account', tone: 'amber' as const };
  if (dataset.access === 'credentialed') return { text: 'Credentials', tone: 'amber' as const };
  if (dataset.access === 'unavailable') return { text: 'Unavailable', tone: 'muted' as const };
  return { text: 'Indexed', tone: 'neutral' as const };
}

/* ------------------------------------------------------- key/value list */

export function KV({
  rows,
  className,
  dense,
}: {
  rows: { k: string; v: ReactNode }[];
  className?: string;
  dense?: boolean;
}) {
  return (
    <dl
      className={clsx(
        'grid grid-cols-[max-content_1fr] gap-x-5',
        dense ? 'gap-y-1' : 'gap-y-1.5',
        className,
      )}
    >
      {rows.map((row) => (
        <div key={row.k} className="contents">
          <dt className="label self-baseline pt-px">{row.k}</dt>
          <dd className="text-ink-1 self-baseline text-xs leading-snug">{row.v}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------------- notices */

export function Notice({
  tone = 'amber',
  children,
  className,
}: {
  tone?: 'amber' | 'red' | 'muted';
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        'border-l-2 pl-3 text-xs leading-relaxed',
        tone === 'amber' && 'border-amber text-amber',
        tone === 'red' && 'border-red text-red',
        tone === 'muted' && 'border-hairline-strong text-ink-1',
        className,
      )}
    >
      {children}
    </div>
  );
}

/* --------------------------------------------------------------- swatch */

/**
 * Colour ramps by physical quantity. These are legend swatches — the ramp a
 * gridded field of that variable is normally drawn with — not pictures of
 * data. Where ORCA has actually generated a raster the caller passes `src`
 * and the real image is shown instead.
 */
const RAMPS: Record<string, string[]> = {
  sst: ['#1d2b6b', '#2f5fb3', '#3fb1d6', '#f2d16b', '#ef8a3c', '#b8322b'],
  chl: ['#0b1e3a', '#0f3c5d', '#0e6b6b', '#2a9d5a', '#8fd35a', '#f3f27a'],
  wind: ['#0f1a2e', '#243a5c', '#3f6e8f', '#6ea6b8', '#a9d3d3', '#e8f4f1'],
  wave: ['#0e1a2b', '#1a3d5c', '#25688a', '#3e93a8', '#7fc1c0', '#d3ecdf'],
  rad: ['#1a1230', '#4a2a6b', '#8b3a7a', '#c95a5a', '#e99b45', '#f7e07a'],
  front: ['#0a1220', '#0a1220', '#1e3350', '#3aa7d6', '#7fe3ff', '#ffffff'],
  pfz: ['#0a1220', '#12324a', '#1f6f5c', '#3ba36a', '#7fd68a', '#d6f5b5'],
  model: ['#0a1220', '#1a2440', '#2c3f6e', '#4a6aa0', '#6f9bd0', '#a9c8f0'],
  neutral: ['#0f1622', '#18212f', '#22303f', '#2d3f50', '#3a5063', '#4a6478'],
};

function rampFor(dataset: Pick<ResearchDataset, 'id' | 'kind' | 'variables'>): string[] {
  const names = dataset.variables.map((v) => v.name.toLowerCase()).join(' ');
  if (dataset.id === 'orca_pfz') return RAMPS.pfz;
  if (dataset.kind === 'model') return RAMPS.model;
  if (/front|gradient/.test(names)) return RAMPS.front;
  if (/sst|temperature/.test(names)) return RAMPS.sst;
  if (/chl|chlorophyll/.test(names)) return RAMPS.chl;
  if (/wave|swell|period/.test(names)) return RAMPS.wave;
  if (/wind|gust/.test(names)) return RAMPS.wind;
  if (/radiation|shortwave|solar/.test(names)) return RAMPS.rad;
  return RAMPS.neutral;
}

export function Swatch({
  dataset,
  src,
  className,
}: {
  dataset: Pick<ResearchDataset, 'id' | 'kind' | 'variables'>;
  src?: string;
  className?: string;
}) {
  const ramp = rampFor(dataset);
  return (
    <div
      className={clsx(
        'border-hairline relative h-7 w-11 shrink-0 overflow-hidden rounded-[3px] border',
        className,
      )}
      title={src ? 'Latest ORCA raster for this field' : 'Colour ramp used for this quantity'}
    >
      {src ? (
        <img src={src} alt="" className="h-full w-full object-cover" loading="lazy" />
      ) : (
        <div className="flex h-full w-full">
          {ramp.map((c, i) => (
            <span key={i} className="h-full flex-1" style={{ background: c }} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ spinners */

export function Working({ label }: { label: string }) {
  return (
    <div className="text-ink-1 flex items-center gap-2 text-xs">
      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      {label}
    </div>
  );
}

export const KIND_ORDER: DatasetKind[] = ['observation', 'forecast', 'reanalysis', 'derived', 'model'];

export const KIND_LABEL: Record<DatasetKind, string> = {
  observation: 'Observation',
  forecast: 'Forecast',
  reanalysis: 'Reanalysis',
  derived: 'Derived',
  model: 'Learned model',
};

/** "0.01°" for a grid spacing; the catalogue stores it as degrees. */
export function fmtDeg(deg: number) {
  if (deg < 0.01) return `${deg.toFixed(4)}°`;
  if (deg < 0.1) return `${deg.toFixed(2)}°`;
  return `${Number.isInteger(deg) ? deg.toFixed(0) : deg.toFixed(2)}°`;
}

export function fmtBox(box: readonly [number, number, number, number]) {
  const [w, s, e, n] = box;
  const lon = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'W' : 'E'}`;
  const lat = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'S' : 'N'}`;
  return `${lon(w)}–${lon(e)} · ${lat(s)}–${lat(n)}`;
}
