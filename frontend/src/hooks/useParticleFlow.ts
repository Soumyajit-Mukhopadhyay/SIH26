/**
 * Animated flow particles over a real u/v field.
 *
 * Advection runs on the CPU and the result is drawn with deck.gl's `LineLayer`.
 * That is a deliberate choice over a GPU ping-pong particle system: at the ~9k
 * particles this needs to look convincing, CPU advection is a few hundred
 * thousand operations a second, comfortably inside a frame budget, and it is
 * code that can be read, tested and trusted. The GPU version buys headroom for
 * 60k particles that a basin-scale flow map does not need, at the cost of the
 * most fragile plumbing in the codebase.
 *
 * The field comes from the server as an RGBA PNG — R is the eastward component,
 * G the northward, B the magnitude, and **A is a data mask where 0 means NO
 * DATA, not zero flow**. A particle reaching a masked cell is respawned rather
 * than advected, or land slowly accumulates a crust of stalled particles.
 *
 * **The animation is time-compressed, and that is not a bug.** Real surface
 * water moves about 0.01 degrees per hour. Rendered at true speed, nothing on
 * screen would appear to move at all, and the streaks would be a fraction of a
 * pixel long — the first version of this drew 180-metre segments at a zoom where
 * one pixel is four kilometres, so the layer was mathematically correct and
 * completely invisible. So motion is scaled to be legible, exactly as every
 * production flow map does.
 *
 * What follows from that: **a particle's path is not a trajectory.** The vectors
 * are real forecast values with a provenance badge, but the animation is a
 * rendering of a field, not a prediction of where a drifting object goes. SAR
 * drift is a separate deterministic Monte-Carlo computation, and conflating a
 * decorative advection with a search-and-rescue prediction is precisely what the
 * provenance model exists to prevent. The layer rail says so too.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

export interface FlowParticle {
  /** Tail of the drawn streak, [lon, lat]. */
  from: [number, number];
  /** Head of the streak — the particle's actual position, [lon, lat]. */
  to: [number, number];
  /** Speed at the head in the field's unit, for colour and width. */
  speed: number;
}

export interface FlowFieldSpec {
  bounds: [number, number, number, number];
  /** From the sidecar's `encoding.max_abs`, in the field's unit. */
  maxAbs: number;
  /** From the sidecar's `particle_speed` — a per-field legibility multiplier. */
  particleSpeed: number;
}

/**
 * Degrees per second of screen motion for a particle travelling at the field's
 * maximum encoded speed.
 *
 * Chosen for legibility at basin zoom, where the AOI's 40 degrees spans roughly
 * a thousand pixels: 1.1 deg/s is visible motion without being frantic, and a
 * particle crosses a few degrees over its lifetime.
 */
const MAX_DEG_PER_SECOND = 1.1;

/**
 * Streak length, in SECONDS of displacement.
 *
 * Measured rather than estimated. At basin zoom the AOI's 40 degrees spans about
 * a thousand pixels, so one pixel is ~0.04 degrees. Typical surface current here
 * is 0.3 m/s, which renders at ~0.15 deg/s — so 0.22 s of displacement is 0.03
 * degrees, under one pixel, and the layer drew 8,700 invisible segments. 1.4 s
 * puts slow water at ~5 px and the fastest at ~35 px, which reads as flow.
 */
const TRAIL_SECONDS = 1.4;

/** A decoded u/v field, sampleable in lon/lat. */
class VectorField {
  private readonly data: Uint8ClampedArray;
  private readonly width: number;
  private readonly height: number;
  private readonly west: number;
  private readonly south: number;
  private readonly east: number;
  private readonly north: number;
  private readonly maxAbs: number;

  constructor(image: ImageBitmap, spec: FlowFieldSpec) {
    this.width = image.width;
    this.height = image.height;
    [this.west, this.south, this.east, this.north] = spec.bounds;
    this.maxAbs = spec.maxAbs;

    // Read the pixels once, up front. An ImageBitmap cannot be sampled directly,
    // and doing this per frame would be the actual bottleneck.
    const canvas = document.createElement('canvas');
    canvas.width = this.width;
    canvas.height = this.height;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) throw new Error('no 2D context available to decode the flow field');
    context.drawImage(image, 0, 0);
    this.data = context.getImageData(0, 0, this.width, this.height).data;
  }

  /** Bilinear sample. Returns null where there is no data. */
  sample(lon: number, lat: number): { u: number; v: number; speed: number } | null {
    const fx = ((lon - this.west) / (this.east - this.west)) * (this.width - 1);
    // Row 0 is the NORTH edge, matching image row order and the server's grid.
    const fy = ((this.north - lat) / (this.north - this.south)) * (this.height - 1);
    if (!(fx >= 0 && fy >= 0 && fx <= this.width - 1 && fy <= this.height - 1)) return null;

    const x0 = Math.floor(fx);
    const y0 = Math.floor(fy);
    const x1 = Math.min(x0 + 1, this.width - 1);
    const y1 = Math.min(y0 + 1, this.height - 1);
    const tx = fx - x0;
    const ty = fy - y0;

    // If ANY corner is masked, treat the whole sample as no-data. Interpolating
    // across a coastline would advect particles a half-cell onto land.
    let u = 0;
    let v = 0;
    for (const [x, y, w] of [
      [x0, y0, (1 - tx) * (1 - ty)],
      [x1, y0, tx * (1 - ty)],
      [x0, y1, (1 - tx) * ty],
      [x1, y1, tx * ty],
    ] as const) {
      const i = (y * this.width + x) * 4;
      if (this.data[i + 3] < 128) return null;
      u += ((this.data[i] - 128) / 127) * this.maxAbs * w;
      v += ((this.data[i + 1] - 128) / 127) * this.maxAbs * w;
    }
    return { u, v, speed: Math.hypot(u, v) };
  }

  randomPoint(): [number, number] {
    return [
      this.west + Math.random() * (this.east - this.west),
      this.south + Math.random() * (this.north - this.south),
    ];
  }
}

export function useParticleFlow(
  image: ImageBitmap | null,
  spec: FlowFieldSpec | null,
  {
    count = 9000,
    enabled = true,
    /** Mean lifetime in seconds, randomised per particle so the basin does not
     *  blink in unison. */
    lifetimeSeconds = 2.6,
  }: { count?: number; enabled?: boolean; lifetimeSeconds?: number } = {},
): FlowParticle[] {
  const [particles, setParticles] = useState<FlowParticle[]>([]);
  const frame = useRef<number>(0);

  const field = useMemo(() => {
    if (!image || !spec) return null;
    try {
      return new VectorField(image, spec);
    } catch {
      return null;
    }
  }, [image, spec]);

  useEffect(() => {
    if (!field || !spec || !enabled) {
      setParticles([]);
      return;
    }

    // State in plain typed arrays, mutated in place: allocating 9000 objects a
    // frame would dominate the cost of the animation.
    const lon = new Float64Array(count);
    const lat = new Float64Array(count);
    const remaining = new Float64Array(count);

    const respawn = (i: number) => {
      const [x, y] = field.randomPoint();
      lon[i] = x;
      lat[i] = y;
      // +/-50% so respawns spread across frames rather than pulsing.
      remaining[i] = lifetimeSeconds * (0.5 + Math.random());
    };
    for (let i = 0; i < count; i += 1) respawn(i);

    // Degrees of screen motion per second, per unit of field speed.
    const degPerUnitPerSecond = (MAX_DEG_PER_SECOND * spec.particleSpeed) / spec.maxAbs;

    let running = true;
    let last = performance.now();

    const tick = (now: number) => {
      if (!running) return;
      // Clamped: a backgrounded tab returns a huge delta and would teleport
      // every particle across the basin on the first frame back.
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;

      const output: FlowParticle[] = [];

      for (let i = 0; i < count; i += 1) {
        const sampled = field.sample(lon[i], lat[i]);
        if (!sampled) {
          respawn(i);
          continue;
        }

        // A degree of longitude shrinks towards the pole; without the cosine a
        // particle at 20 N would drift east faster than the water does.
        const cosLat = Math.max(Math.cos((lat[i] * Math.PI) / 180), 0.25);
        const stepLon = (sampled.u * degPerUnitPerSecond) / cosLat;
        const stepLat = sampled.v * degPerUnitPerSecond;

        // The streak trails BEHIND the head by a fixed number of seconds of
        // motion, so its length encodes speed and is independent of frame rate.
        const from: [number, number] = [
          lon[i] - stepLon * TRAIL_SECONDS,
          lat[i] - stepLat * TRAIL_SECONDS,
        ];

        lon[i] += stepLon * dt;
        lat[i] += stepLat * dt;
        remaining[i] -= dt;

        const escaped =
          lon[i] < spec.bounds[0] ||
          lon[i] > spec.bounds[2] ||
          lat[i] < spec.bounds[1] ||
          lat[i] > spec.bounds[3];
        if (remaining[i] <= 0 || escaped) {
          respawn(i);
          continue;
        }

        output.push({ from, to: [lon[i], lat[i]], speed: sampled.speed });
      }

      setParticles(output);
      frame.current = requestAnimationFrame(tick);
    };

    frame.current = requestAnimationFrame(tick);
    return () => {
      running = false;
      cancelAnimationFrame(frame.current);
    };
  }, [field, spec, count, enabled, lifetimeSeconds]);

  return particles;
}
