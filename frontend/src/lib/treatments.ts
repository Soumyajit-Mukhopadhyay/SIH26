/**
 * The seven visual treatments, and an honest note about how they are done.
 *
 * These are **compositor-side colour transforms** — SVG filter primitives plus
 * a blend overlay — applied to the map layer. They are not a GLSL post-process
 * pass over the map's framebuffer, and the reason is measured rather than
 * assumed: the console draws two stacked WebGL canvases (MapLibre's basemap and
 * deck.gl's overlay), and sampling those into a shader means reading both back
 * every frame. Doing that already produces `GPU stall due to ReadPixels` in this
 * app's own browser logs at 1680×945, and it would cost exactly the frame budget
 * the FPS guard exists to protect.
 *
 * SVG filters run in the compositor on the GPU, cost nothing on the JS thread,
 * and `feColorMatrix` + `feComponentTransfer` + `feTurbulence` between them cover
 * everything these treatments actually need: arbitrary 5×4 colour matrices,
 * per-channel transfer curves (including `discrete`, which gives genuine
 * posterised contour bands), and band-limited noise. The one thing they cannot do
 * is a spatially-varying warp driven by scene data, and none of the seven wants
 * one.
 *
 * ## What a treatment is NOT allowed to do
 *
 * **A treatment must never look like a data colormap.** ORCA's raster colormaps
 * mean something — a colour on the SST layer maps to a temperature through a
 * published lookup table, and the legend is generated from that same table so it
 * cannot lie. A stylistic recolour that fought with that would break the one
 * promise the map makes. So every treatment other than `standard` sets
 * `distortsData`, the UI says so while one is active, and the legend hides
 * itself rather than showing stops that no longer match the pixels.
 */

import type { CSSProperties } from 'react';

export type TreatmentId =
  | 'standard'
  | 'thermal'
  | 'night-vision'
  | 'radar'
  | 'bathymetric'
  | 'crt'
  | 'noir';

export interface Treatment {
  id: TreatmentId;
  label: string;
  /** One line, shown in the rail. Says what it is FOR, not what it looks like. */
  blurb: string;
  /** The SVG filter to apply, or null for no filter. */
  filter: string | null;
  /** Extra CSS on the map wrapper — blend overlays live in `overlay`. */
  style?: CSSProperties;
  /** A non-interactive layer drawn over the map, for scanlines and sweeps. */
  overlay?: CSSProperties;
  /**
   * Whether the treatment changes pixel colours in a way that makes the data
   * legends wrong. True for everything but `standard`.
   */
  distortsData: boolean;
  /** Rough compositor cost, 1 (free) to 3 (a full-frame blend plus noise). */
  cost: 1 | 2 | 3;
}

export const TREATMENTS: Treatment[] = [
  {
    id: 'standard',
    label: 'Standard',
    blurb: 'Everyday map. Use this when you need the colours to match the legends.',
    filter: null,
    distortsData: false,
    cost: 1,
  },
  {
    id: 'thermal',
    label: 'Thermal',
    blurb: 'Use this to pick out warm and cool water at a glance.',
    filter: 'url(#orca-thermal)',
    distortsData: true,
    cost: 2,
  },
  {
    id: 'night-vision',
    label: 'Night vision',
    blurb: 'Use this on a dark bridge so the screen stays easy on the eyes.',
    filter: 'url(#orca-nightvision)',
    overlay: {
      // A vignette, because every intensifier tube has one. Radial rather than a
      // box shadow so it survives the map being any aspect ratio.
      background:
        'radial-gradient(ellipse at center, rgba(0,0,0,0) 42%, rgba(0,0,0,0.55) 78%, rgba(0,0,0,0.85) 100%)',
      mixBlendMode: 'multiply',
    },
    distortsData: true,
    cost: 3,
  },
  {
    id: 'radar',
    label: 'Radar',
    blurb: 'Use this when you want range rings around the point you picked.',
    filter: 'url(#orca-radar)',
    overlay: radarOverlayStyle(50, 50),
    distortsData: true,
    cost: 3,
  },
  {
    id: 'bathymetric',
    label: 'Bathymetric',
    blurb: 'Use this to read the sea in simple depth bands.',
    filter: 'url(#orca-bathymetric)',
    distortsData: true,
    cost: 2,
  },
  {
    id: 'crt',
    label: 'CRT',
    blurb: 'Use this for a harbour office monitor look.',
    filter: 'url(#orca-crt)',
    overlay: {
      backgroundImage:
        'repeating-linear-gradient(to bottom, rgba(0,0,0,0.34) 0 1px, rgba(0,0,0,0) 1px 3px)',
      mixBlendMode: 'multiply',
    },
    distortsData: true,
    cost: 2,
  },
  {
    id: 'noir',
    label: 'Noir',
    blurb: 'Use this when colour is a distraction and you only need the shape of the coast.',
    filter: 'url(#orca-noir)',
    overlay: {
      background:
        'radial-gradient(ellipse at center, rgba(0,0,0,0) 45%, rgba(0,0,0,0.5) 85%, rgba(0,0,0,0.78) 100%)',
      mixBlendMode: 'multiply',
    },
    distortsData: true,
    cost: 2,
  },
];

/** Range rings and sweep, centred on a map point (percent of the map box). */
export function radarOverlayStyle(xPct: number, yPct: number): CSSProperties {
  const x = `${xPct.toFixed(2)}%`;
  const y = `${yPct.toFixed(2)}%`;
  return {
    backgroundImage: [
      `repeating-radial-gradient(circle at ${x} ${y}, rgba(126,255,170,0.30) 0 1.5px, rgba(0,0,0,0) 1.5px 110px)`,
      `conic-gradient(from var(--orca-sweep, 0deg) at ${x} ${y}, rgba(170,255,200,0.55) 0deg, rgba(126,255,170,0.22) 18deg, rgba(74,222,128,0.07) 44deg, rgba(0,0,0,0) 78deg, rgba(0,0,0,0) 360deg)`,
    ].join(','),
    mixBlendMode: 'screen',
    animation: 'orca-radar-sweep 4.2s linear infinite',
  };
}

export const TREATMENT_BY_ID: Record<TreatmentId, Treatment> = Object.fromEntries(
  TREATMENTS.map((t) => [t.id, t]),
) as Record<TreatmentId, Treatment>;

/**
 * Sample points for a transfer table, from a list of colour stops.
 *
 * `feComponentTransfer` with `type="table"` interpolates linearly between
 * whatever values it is given, so a palette is expressed as one table per
 * channel. Generating them from stops rather than hand-writing three lists of
 * numbers is what keeps a palette editable — an ironbow written out as 3×9
 * literals is a palette nobody will ever adjust again.
 */
export function transferTables(stops: [number, number, number][]): {
  r: string;
  g: string;
  b: string;
} {
  return {
    r: stops.map((s) => (s[0] / 255).toFixed(4)).join(' '),
    g: stops.map((s) => (s[1] / 255).toFixed(4)).join(' '),
    b: stops.map((s) => (s[2] / 255).toFixed(4)).join(' '),
  };
}

/** Ironbow: black → indigo → magenta → red → orange → yellow → white. */
export const IRONBOW: [number, number, number][] = [
  [0, 0, 12],
  [22, 8, 74],
  [76, 12, 122],
  [140, 20, 116],
  [196, 44, 74],
  [231, 90, 30],
  [248, 152, 12],
  [253, 212, 60],
  [255, 248, 214],
];

/** Image-intensifier green, dark to blooming highlight. */
export const INTENSIFIER: [number, number, number][] = [
  [0, 6, 2],
  [2, 34, 12],
  [6, 74, 26],
  [14, 122, 44],
  [34, 176, 66],
  [96, 218, 108],
  [190, 245, 190],
];

/** Bathymetric bands: abyss to shoal. Used with a `discrete` transfer. */
export const BATHYMETRIC: [number, number, number][] = [
  [3, 12, 34],
  [8, 32, 66],
  [12, 58, 100],
  [16, 92, 134],
  [34, 132, 164],
  [92, 178, 190],
  [168, 214, 214],
];
