/**
 * Frame-rate measurement, and a guard that actually gives something up.
 *
 * An FPS counter that only displays a number is decoration. The plan's
 * requirement is that the console "degrades gracefully" on integrated graphics,
 * and degrading means dropping a feature — so this hook owns the decision and
 * reports what it took away and why.
 *
 * Two details that matter for it to be trustworthy:
 *
 * **The median, not the mean.** A single 400 ms hitch when the SST raster
 * decodes would drag a mean below any threshold and trip the guard on a machine
 * that is perfectly fine. The median over a rolling window ignores it.
 *
 * **A grace period, and hysteresis.** The first second after a treatment is
 * applied is spent building compositor layers and is not representative, so
 * measurement is delayed. And recovery needs a clearly better frame time than
 * the trip point, otherwise a machine sitting exactly at the threshold flickers
 * between treated and untreated forever.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

/** Rolling window. ~1 s at 60 fps: long enough to be stable, short enough to react. */
const WINDOW = 60;

/** Trip above this median frame time. 22 ms ≈ 45 fps. */
const TRIP_MS = 22;

/** Recover below this. The gap is the hysteresis. 15 ms ≈ 66 fps. */
const RECOVER_MS = 15;

/** How long the median must stay bad before anything is taken away. */
const SUSTAIN_MS = 1600;

/** Ignore the first frames after a change — layer setup is not steady state. */
const GRACE_MS = 1200;

export interface FrameRate {
  fps: number;
  medianMs: number;
  /** True while the guard has forced a downgrade. */
  degraded: boolean;
  /** Why, in a sentence, or null. */
  reason: string | null;
  /** Call when the thing being measured changes, to restart the grace period. */
  reset: () => void;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function useFrameRate({
  /** Only guard while this is true — nothing to give up otherwise. */
  active,
  /** Called once when the guard decides to downgrade. */
  onDegrade,
  label = 'this visual treatment',
}: {
  active: boolean;
  onDegrade?: (reason: string) => void;
  label?: string;
}): FrameRate {
  const [fps, setFps] = useState(0);
  const [medianMs, setMedianMs] = useState(0);
  const [degraded, setDegraded] = useState(false);
  const [reason, setReason] = useState<string | null>(null);

  const frames = useRef<number[]>([]);
  const last = useRef(0);
  const startedAt = useRef(0);
  const badSince = useRef<number | null>(null);
  const raf = useRef<number | null>(null);
  const notified = useRef(false);

  const reset = useCallback(() => {
    frames.current = [];
    last.current = 0;
    startedAt.current = performance.now();
    badSince.current = null;
    notified.current = false;
    setDegraded(false);
    setReason(null);
  }, []);

  useEffect(() => {
    reset();
  }, [active, reset]);

  useEffect(() => {
    const tick = (now: number) => {
      if (last.current) {
        const delta = now - last.current;
        // Discard absurd deltas: a backgrounded tab or a devtools pause produces
        // multi-second frames that say nothing about the GPU.
        if (delta < 1000) {
          frames.current.push(delta);
          if (frames.current.length > WINDOW) frames.current.shift();
        }
      }
      last.current = now;

      if (frames.current.length >= 20) {
        const mid = median(frames.current);
        setMedianMs(mid);
        setFps(mid > 0 ? 1000 / mid : 0);

        const settled = now - startedAt.current > GRACE_MS;
        if (active && settled) {
          if (mid > TRIP_MS) {
            badSince.current ??= now;
            if (now - badSince.current > SUSTAIN_MS && !notified.current) {
              notified.current = true;
              const message =
                `${Math.round(1000 / mid)} fps sustained — ORCA turned off ${label} to keep the ` +
                'map responsive. Turn it back on from the rail if you would rather have the look.';
              setDegraded(true);
              setReason(message);
              onDegrade?.(message);
            }
          } else if (mid < RECOVER_MS) {
            badSince.current = null;
          }
        }
      }

      raf.current = requestAnimationFrame(tick);
    };

    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
    };
  }, [active, label, onDegrade]);

  return { fps, medianMs, degraded, reason, reset };
}
