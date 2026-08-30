/**
 * Turning a general-purpose basemap into a marine chart.
 *
 * OpenFreeMap's dark style is built for looking at cities. ORCA is looking at
 * water, and the default emphasis is exactly backwards: land carries every
 * label and the sea renders nearly black, so the surface the user cares about is
 * the least legible thing on screen. Hanoi and Ho Chi Minh City are noise in a
 * console about the Bay of Bengal.
 *
 * So we rewrite the style at load:
 *
 * - **Land recedes.** Flat, near-black, no fills competing with data layers.
 * - **The sea advances.** A deep blue that is visibly not land, bright enough
 *   that a cyan PFZ polygon and an amber warning both read against it.
 * - **The coastline is the strongest line on the map**, because for a fisherman
 *   it is the most important one.
 * - **Inland detail goes away**: roads, buildings, landuse, city POIs. Coastal
 *   place names stay, since they are how people describe where they are.
 *
 * Done as a transform over the fetched style rather than a hand-authored style
 * so OpenFreeMap can update their tiles without us maintaining a fork.
 */

import type { StyleSpecification, LayerSpecification } from 'maplibre-gl';

export const OCEAN = {
  /** Deep water. The page ground is #04090f, so this is visibly "sea, not void". */
  deep: '#071a2e',
  shelf: '#0a2340',
  shallow: '#0d2d4f',
  land: '#080d13',
  landHigh: '#0b1219',
  coast: '#2b5f7d',
  coastGlow: '#3d7fa3',
  border: '#1c3348',
  label: '#7f96ab',
  labelHalo: '#04090f',
  marineLabel: '#5f89a6',
} as const;

/** Layer id fragments that are pure inland clutter for a marine console. */
const DROP_PATTERNS = [
  'building',
  'landuse',
  'landcover',
  'road',
  'bridge',
  'tunnel',
  'highway',
  'motorway',
  'rail',
  'transit',
  'aeroway',
  'airport',
  'poi',
  'housenumber',
  'park',
  'pitch',
  'golf',
  'cemetery',
  'hospital',
  'school',
  'university',
  'ferry',
  'pier',
  'aerodrome',
];

/** Place labels we keep: coastal and marine orientation, not urban geography. */
const KEEP_PLACE_CLASSES = ['country', 'state', 'sea', 'ocean', 'island', 'city'];

const has = (id: string, fragments: string[]) =>
  fragments.some((fragment) => id.toLowerCase().includes(fragment));

export function toOrcaDeep(style: StyleSpecification): StyleSpecification {
  const layers: LayerSpecification[] = [];

  for (const layer of style.layers) {
    const id = layer.id.toLowerCase();

    // Natural Earth's shaded relief raster makes land look like terrain, which
    // is the opposite of what we want it to do.
    if (id.includes('ne2') || id.includes('shaded') || id.includes('hillshade')) continue;

    if (has(id, DROP_PATTERNS)) continue;

    const next = structuredClone(layer) as LayerSpecification;

    // ---- background: the void behind everything ----
    if (next.type === 'background') {
      next.paint = { ...next.paint, 'background-color': OCEAN.land };
      layers.push(next);
      continue;
    }

    // ---- water: the surface the whole product is about ----
    if (id.includes('water') || id.includes('ocean') || id.includes('sea')) {
      if (next.type === 'fill') {
        next.paint = {
          ...next.paint,
          // Slightly lighter when zoomed in, so a harbour reads as distinct from
          // the open ocean without needing a separate layer.
          'fill-color': [
            'interpolate',
            ['linear'],
            ['zoom'],
            2,
            OCEAN.deep,
            6,
            OCEAN.shelf,
            10,
            OCEAN.shallow,
          ] as unknown as string,
          'fill-opacity': 1,
        };
      }
      if (next.type === 'line') {
        next.paint = { ...next.paint, 'line-color': OCEAN.coast, 'line-opacity': 0.5 };
      }
      layers.push(next);
      continue;
    }

    // ---- land: flat and quiet ----
    if (next.type === 'fill' && (id.includes('land') || id.includes('earth'))) {
      next.paint = { ...next.paint, 'fill-color': OCEAN.land, 'fill-opacity': 1 };
      layers.push(next);
      continue;
    }

    // ---- boundaries: present but subordinate ----
    if (id.includes('boundary') || id.includes('admin') || id.includes('border')) {
      if (next.type === 'line') {
        next.paint = {
          ...next.paint,
          'line-color': OCEAN.border,
          'line-opacity': ['interpolate', ['linear'], ['zoom'], 2, 0.3, 6, 0.5] as unknown as number,
          'line-width': 0.7,
        };
      }
      layers.push(next);
      continue;
    }

    // ---- labels ----
    if (next.type === 'symbol') {
      const keep = KEEP_PLACE_CLASSES.some((cls) => id.includes(cls)) || id.includes('place');
      if (!keep) continue;

      next.paint = {
        ...next.paint,
        'text-color': id.includes('sea') || id.includes('ocean') ? OCEAN.marineLabel : OCEAN.label,
        'text-halo-color': OCEAN.labelHalo,
        'text-halo-width': 1.4,
        'text-opacity': id.includes('city') ? 0.55 : 0.85,
      };
      next.layout = {
        ...next.layout,
        'text-letter-spacing': 0.08,
        // Cities only once the user has zoomed in far enough to be asking about
        // a specific harbour; at ocean-basin zoom they are noise.
        ...(id.includes('city') ? { 'text-size': 10 } : {}),
      };
      if (id.includes('city')) {
        next.minzoom = Math.max(next.minzoom ?? 0, 6);
      }
      layers.push(next);
      continue;
    }

    layers.push(next);
  }

  // A coastline drawn on top of everything: the single most important line on a
  // marine chart, and OpenFreeMap does not give us one with enough presence.
  const waterSource = findWaterSource(style);
  if (waterSource) {
    layers.push({
      id: 'orca-coastline-glow',
      type: 'line',
      source: waterSource.source,
      'source-layer': waterSource.sourceLayer,
      paint: {
        'line-color': OCEAN.coastGlow,
        'line-width': ['interpolate', ['linear'], ['zoom'], 2, 0.6, 6, 1.6, 10, 2.6],
        'line-opacity': 0.42,
        'line-blur': 2.4,
      },
    } as LayerSpecification);
    layers.push({
      id: 'orca-coastline',
      type: 'line',
      source: waterSource.source,
      'source-layer': waterSource.sourceLayer,
      paint: {
        'line-color': OCEAN.coast,
        'line-width': ['interpolate', ['linear'], ['zoom'], 2, 0.35, 6, 0.7, 10, 1.1],
        'line-opacity': 0.85,
      },
    } as LayerSpecification);
  }

  return { ...style, layers };
}

/**
 * Find the vector source and source-layer that holds water polygons.
 *
 * Read off the fetched style rather than hardcoded, because OpenMapTiles schema
 * names have changed before and a hardcoded 'water' would silently produce a map
 * with no coastline — a failure that looks like a design choice.
 */
function findWaterSource(
  style: StyleSpecification,
): { source: string; sourceLayer: string } | null {
  for (const layer of style.layers) {
    if (
      layer.type === 'fill' &&
      'source' in layer &&
      'source-layer' in layer &&
      typeof layer['source-layer'] === 'string' &&
      layer['source-layer'].toLowerCase().includes('water') &&
      !layer.id.toLowerCase().includes('name')
    ) {
      return { source: layer.source as string, sourceLayer: layer['source-layer'] };
    }
  }
  return null;
}
