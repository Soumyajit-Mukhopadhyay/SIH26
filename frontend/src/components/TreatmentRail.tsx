/**
 * The treatment picker, with the frame rate and the honesty warning attached.
 *
 * The warning is the point of the component, not an afterthought. ORCA's raster
 * legends are generated from the same lookup tables that colour the pixels, so a
 * colour on the SST layer maps to a temperature and the legend cannot lie about
 * it. A stylistic recolour breaks that. So whenever a treatment other than
 * `standard` is active this rail says the legends no longer apply — and the
 * layer rail hides its legends rather than showing stops that do not match what
 * is on screen.
 */

import { Eye, Gauge, Palette, TriangleAlert } from 'lucide-react';
import { clsx } from 'clsx';
import { TREATMENTS, type TreatmentId } from '@/lib/treatments';

export function TreatmentRail({
  active,
  onChange,
  fps,
  degradedReason,
  open,
  onToggle,
}: {
  active: TreatmentId;
  onChange: (id: TreatmentId) => void;
  fps: number;
  degradedReason: string | null;
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  const current = TREATMENTS.find((t) => t.id === active) ?? TREATMENTS[0];

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => onToggle(true)}
        className="glass pointer-events-auto flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 transition-colors hover:bg-white/5"
        title="Visual treatments"
      >
        <Palette className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">{current.label}</span>
        {fps > 0 && (
          <span
            className={clsx('data text-2xs', fps < 40 ? 'text-amber' : 'text-ink-3')}
            title="Median frame rate over the last second"
          >
            {Math.round(fps)} fps
          </span>
        )}
      </button>
    );
  }

  return (
    <div className="glass pointer-events-auto w-[15rem] rounded-lg" data-orca="treatment-rail">
      <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
        <Palette className="text-cyan h-3.5 w-3.5" aria-hidden />
        <span className="label">Treatment</span>
        <span
          className={clsx(
            'data ml-auto flex items-center gap-1 text-2xs',
            fps > 0 && fps < 40 ? 'text-amber' : 'text-ink-3',
          )}
        >
          <Gauge className="h-2.5 w-2.5" aria-hidden />
          {fps > 0 ? `${Math.round(fps)} fps` : '—'}
        </span>
        <button
          type="button"
          onClick={() => onToggle(false)}
          className="text-ink-3 hover:text-ink-1 text-2xs transition-colors"
        >
          close
        </button>
      </div>

      <div className="p-1.5">
        {TREATMENTS.map((treatment) => {
          const selected = treatment.id === active;
          return (
            <button
              key={treatment.id}
              type="button"
              onClick={() => onChange(treatment.id)}
              className={clsx(
                'flex w-full flex-col gap-0.5 rounded px-2 py-1.5 text-left transition-colors',
                selected ? 'bg-cyan/12' : 'hover:bg-white/4',
              )}
            >
              <span className="flex items-center gap-1.5">
                <span
                  className={clsx(
                    'text-2xs tracking-wider uppercase',
                    selected ? 'text-cyan' : 'text-ink-1',
                  )}
                >
                  {treatment.label}
                </span>
                {treatment.cost === 3 && (
                  <span className="text-ink-3 text-2xs" title="Heaviest to composite">
                    heavy
                  </span>
                )}
                {selected && <Eye className="text-cyan ml-auto h-2.5 w-2.5" aria-hidden />}
              </span>
              <span className="text-ink-3 text-2xs leading-snug">{treatment.blurb}</span>
            </button>
          );
        })}
      </div>

      {current.distortsData && (
        <div className="border-hairline border-t px-3 py-2">
          <p className="text-amber flex items-start gap-1 text-2xs leading-snug">
            <TriangleAlert className="mt-px h-2.5 w-2.5 shrink-0" aria-hidden />
            <span>
              <span className="font-semibold">Layer legends do not apply.</span> ORCA's raster
              colours are generated from the same lookup tables as the legends, so a colour means a
              value. This treatment recolours them, so the legends are hidden until you return to
              Standard.
            </span>
          </p>
        </div>
      )}

      {degradedReason && (
        <div className="border-hairline border-t px-3 py-2">
          <p className="text-ink-2 text-2xs leading-snug">{degradedReason}</p>
        </div>
      )}
    </div>
  );
}
