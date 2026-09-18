import { clsx } from 'clsx';
import { useEffect, useMemo, useState } from 'react';

import { api } from '@/lib/api';
import type { FenceCollection } from '@/lib/types';

/**
 * A flat equirectangular map of ORCA's working area (60–100°E, 0–25°N) drawn
 * from geometry the backend already serves: the Indian EEZ and the maritime
 * boundary lines. No basemap tiles, no coastline we do not have — the EEZ
 * outline is enough to place a bounding box or a mooring by eye, and it is a
 * real, cited geometry rather than decoration.
 */

export type LonLatBox = readonly [number, number, number, number];

export interface MapPoint {
  id: string;
  lat: number;
  lon: number;
  label?: string;
  active?: boolean;
  muted?: boolean;
}

const EXTENT: LonLatBox = [60, 0, 100, 25];

let fenceCache: Promise<FenceCollection | null> | null = null;
function loadFences() {
  fenceCache ??= api.fences(0.08).catch(() => null);
  return fenceCache;
}

type Ring = [number, number][];

function ringsOf(geometry: { type: string; coordinates: unknown }): Ring[] {
  const c = geometry.coordinates;
  switch (geometry.type) {
    case 'LineString':
      return [c as Ring];
    case 'MultiLineString':
      return c as Ring[];
    case 'Polygon':
      return c as Ring[];
    case 'MultiPolygon':
      return (c as Ring[][]).flat();
    default:
      return [];
  }
}

function pathOf(ring: Ring) {
  return ring.map(([lon, lat], i) => `${i ? 'L' : 'M'}${lon.toFixed(3)} ${(-lat).toFixed(3)}`).join('');
}

export function RegionMap({
  box,
  points,
  onPick,
  className,
  extent = EXTENT,
}: {
  box?: LonLatBox | null;
  points?: MapPoint[];
  onPick?: (id: string) => void;
  className?: string;
  extent?: LonLatBox;
}) {
  const [fences, setFences] = useState<FenceCollection | null>(null);
  useEffect(() => {
    let live = true;
    void loadFences().then((f) => live && setFences(f));
    return () => {
      live = false;
    };
  }, []);

  const [w, s, e, n] = extent;
  const paths = useMemo(() => {
    const out: { d: string; kind: string }[] = [];
    for (const feature of fences?.features ?? []) {
      for (const ring of ringsOf(feature.geometry)) {
        if (ring.length > 1) out.push({ d: pathOf(ring), kind: feature.properties.kind });
      }
    }
    return out;
  }, [fences]);

  const meridians = [];
  for (let lon = Math.ceil(w / 10) * 10; lon <= e; lon += 10) meridians.push(lon);
  const parallels = [];
  for (let lat = Math.ceil(s / 10) * 10; lat <= n; lat += 10) parallels.push(lat);

  return (
    <svg
      viewBox={`${w} ${-n} ${e - w} ${n - s}`}
      preserveAspectRatio="xMidYMid meet"
      className={clsx('bg-abyss-1 block h-auto w-full', className)}
      role="img"
      aria-label="Map of the Indian EEZ"
    >
      <defs>
        <clipPath id="region-map-clip">
          <rect x={w} y={-n} width={e - w} height={n - s} />
        </clipPath>
      </defs>
      <g clipPath="url(#region-map-clip)">
        {meridians.map((lon) => (
          <line
            key={`m${lon}`}
            x1={lon}
            x2={lon}
            y1={-n}
            y2={-s}
            stroke="rgb(255 255 255 / 0.06)"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {parallels.map((lat) => (
          <line
            key={`p${lat}`}
            x1={w}
            x2={e}
            y1={-lat}
            y2={-lat}
            stroke="rgb(255 255 255 / 0.06)"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {paths.map((p, i) => (
          <path
            key={i}
            d={p.d}
            fill={p.kind === 'eez' ? 'rgb(34 211 238 / 0.06)' : 'none'}
            stroke={p.kind === 'imbl' ? 'rgb(245 158 11 / 0.55)' : 'rgb(148 168 187 / 0.5)'}
            strokeWidth={1}
            strokeDasharray={p.kind === 'imbl' ? '3 2' : undefined}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {box ? (
          <rect
            x={box[0]}
            y={-box[3]}
            width={box[2] - box[0]}
            height={box[3] - box[1]}
            fill="rgb(34 211 238 / 0.10)"
            stroke="#22d3ee"
            strokeWidth={1.25}
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
        {points?.map((p) => (
          <g
            key={p.id}
            onClick={onPick ? () => onPick(p.id) : undefined}
            className={onPick ? 'cursor-pointer' : undefined}
          >
            <circle
              cx={p.lon}
              cy={-p.lat}
              r={p.active ? 0.7 : 0.45}
              fill={p.active ? '#22d3ee' : p.muted ? 'rgb(91 111 131 / 0.7)' : '#e8f0f7'}
              stroke={p.active ? '#04090f' : 'none'}
              strokeWidth={0.15}
            />
            {p.active ? (
              <circle
                cx={p.lon}
                cy={-p.lat}
                r={1.4}
                fill="none"
                stroke="#22d3ee"
                strokeWidth={0.12}
              />
            ) : null}
            {p.label ? <title>{p.label}</title> : null}
          </g>
        ))}
      </g>
      {/* frame */}
      <rect
        x={w}
        y={-n}
        width={e - w}
        height={n - s}
        fill="none"
        stroke="rgb(255 255 255 / 0.14)"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/** Graticule tick labels for the standard extent, rendered outside the SVG so
 *  they keep a fixed pixel size. */
export function MapAxes({ extent = EXTENT }: { extent?: LonLatBox }) {
  const [w, s, e, n] = extent;
  return (
    <div className="data text-ink-2 mt-1 flex justify-between text-[10px]">
      <span>
        {w}°E
      </span>
      <span>
        {s}–{n}°N
      </span>
      <span>{e}°E</span>
    </div>
  );
}
