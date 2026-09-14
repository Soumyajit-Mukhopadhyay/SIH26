/**
 * The operational globe: MapLibre v5 in globe projection with a deck.gl overlay.
 *
 * Decisions worth knowing:
 *
 * - **OpenFreeMap dark**, no API key, no quota, verified reachable. A demo that
 *   dies because a basemap tier ran out is a self-inflicted wound.
 * - **Globe projection**, because the AOI spans 40 degrees of longitude and a
 *   Mercator sheet makes the Indian EEZ look like a rectangle rather than a sea.
 * - **The overlay is an interleaved MapboxOverlay**, so deck.gl layers sit in the
 *   same depth buffer as the basemap and a polygon can be occluded by terrain
 *   rather than floating over it.
 * - **`flyToBox` exists for the causal link.** When the agent answers, the map
 *   moves to the bounding box the answer is about, so a judge sees that the
 *   chat and the map are one system and not two demos side by side.
 */

import { useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import maplibregl, { type Map as MapLibreMap, type StyleSpecification } from 'maplibre-gl';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Layer } from '@deck.gl/core';
import 'maplibre-gl/dist/maplibre-gl.css';
import { OCEAN, toOrcaDeep } from '@/lib/basemap';

/** OpenFreeMap: free forever, no key, no quota. Verified 200. */
const BASEMAP_STYLE = 'https://tiles.openfreemap.org/styles/dark';

/** The AOI: the Indian EEZ envelope, 60-100E / 0-25N. One definition. */
export const AOI = { west: 60, south: 0, east: 100, north: 25 } as const;

/** Default camera: the Bay of Bengal off Tamil Nadu, where the demo lives. */
export const HOME_VIEW = { center: [82.5, 12.5] as [number, number], zoom: 4.6, pitch: 0, bearing: 0 };

export interface OceanMapHandle {
  map: () => MapLibreMap | null;
  flyTo: (lon: number, lat: number, zoom?: number) => void;
  /** Move to a bbox — the visible causal link between an answer and the map. */
  flyToBox: (west: number, south: number, east: number, north: number) => void;
  resetView: () => void;
  /** Selected lon/lat as percent of the map box — Look ranging centres here. */
  projectPct: (lon: number, lat: number) => { x: number; y: number } | null;
}

export type MapSurface = 'water' | 'land' | 'unknown';

export function OceanMap({
  ref,
  layers = [],
  onClick,
  onReady,
  onViewChange,
  className,
}: {
  ref?: React.Ref<OceanMapHandle>;
  layers?: Layer[];
  onClick?: (lon: number, lat: number, surface: MapSurface) => void;
  onReady?: () => void;
  onViewChange?: () => void;
  className?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;
  const viewChangeRef = useRef(onViewChange);
  viewChangeRef.current = onViewChange;

  // The current layers, held in a ref so the overlay can adopt them the moment
  // it exists. Without this the two are ordering-dependent: the style is fetched
  // and transformed before the map is constructed, so `map.on('load')` fires
  // well after React has settled, the last setProps runs against a null overlay,
  // and `layers` never changes again — deck ends up mounted with zero layers and
  // renders nothing, silently. That is exactly the bug adding the basemap
  // transform introduced.
  const pendingLayers = useRef<Layer[]>(layers);
  pendingLayers.current = layers;

  const [failed, setFailed] = useState<string | null>(null);

  useImperativeHandle(
    ref,
    () => ({
      map: () => mapRef.current,
      flyTo: (lon, lat, zoom = 7) => {
        mapRef.current?.flyTo({ center: [lon, lat], zoom, duration: 1600, essential: true });
      },
      flyToBox: (west, south, east, north) => {
        mapRef.current?.fitBounds(
          [
            [west, south],
            [east, north],
          ],
          { padding: 120, duration: 1800, maxZoom: 9 },
        );
      },
      resetView: () => {
        mapRef.current?.flyTo({ ...HOME_VIEW, duration: 1600 });
      },
      projectPct: (lon, lat) => {
        const map = mapRef.current;
        if (!map) return null;
        const point = map.project([lon, lat]);
        const box = map.getContainer();
        if (box.clientWidth < 1 || box.clientHeight < 1) return null;
        return {
          x: (point.x / box.clientWidth) * 100,
          y: (point.y / box.clientHeight) * 100,
        };
      },
    }),
    [],
  );

  useEffect(() => {
    if (!container.current || mapRef.current) return;

    // WebGL2 is required by both MapLibre v5 and deck.gl. Probe rather than
    // crash: the plan calls for a graceful fallback message, not a white screen.
    const probe = document.createElement('canvas').getContext('webgl2');
    if (!probe) {
      setFailed('This device has no WebGL2 support, which the globe requires.');
      return;
    }

    let cancelled = false;
    let map: MapLibreMap | null = null;

    // The style is fetched and rewritten into a marine chart before MapLibre
    // ever sees it — see lib/basemap.ts for why the default emphasis is wrong
    // for an ocean console.
    void (async () => {
      let style: StyleSpecification | string = BASEMAP_STYLE;
      try {
        const response = await fetch(BASEMAP_STYLE);
        if (response.ok) {
          style = toOrcaDeep((await response.json()) as StyleSpecification);
        }
      } catch {
        // A transform failure must not cost us the map: fall back to the
        // untransformed style URL, which is merely less pretty.
      }
      if (cancelled || !container.current) return;
      map = buildMap(container.current, style);
    })();

    function buildMap(node: HTMLDivElement, style: StyleSpecification | string): MapLibreMap {
    const map = new maplibregl.Map({
      container: node,
      style,
      center: HOME_VIEW.center,
      zoom: HOME_VIEW.zoom,
      pitch: HOME_VIEW.pitch,
      bearing: HOME_VIEW.bearing,
      attributionControl: { compact: true },
      maxPitch: 75,
      dragRotate: true,
      touchZoomRotate: true,
    });

    mapRef.current = map;

    // Dev-only handle, so a browser test can click a real coordinate instead of
    // guessing pixels. Under a globe projection the screen position of a
    // latitude is not a linear function of the viewport, and hand-tuned pixel
    // targets silently start landing on land — or on a modal — the moment the
    // camera moves. `import.meta.env.DEV` is statically false in a production
    // build, so Vite removes this entirely.
    if (import.meta.env.DEV) {
      (window as unknown as { __orcaMap?: MapLibreMap }).__orcaMap = map;
    }

    map.on('style.load', () => {
      // Globe, for a 40-degree-wide AOI.
      map.setProjection({ type: 'globe' });

      // A deep-ocean ground and a faint atmosphere. The basemap's own water is
      // too light for an instrument panel at night.
      map.setSky({
        'sky-color': '#04090f',
        'horizon-color': OCEAN.coast,
        'fog-color': '#04090f',
        'sky-horizon-blend': 0.55,
        'horizon-fog-blend': 0.5,
        'fog-ground-blend': 0.1,
        'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.9, 6, 0.35, 10, 0],
      });
    });

    map.on('load', () => {
      // Overlaid, NOT interleaved. Interleaved renders deck inside MapLibre's own
      // pass, where the vector layers drew fine but a BitmapLayer never appeared
      // even with a fully decoded ImageBitmap and no error anywhere. Overlaid
      // composites deck's own canvas above the basemap and draws everything. The
      // cost is terrain occlusion, which an ocean map has no use for.
      const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
      map.addControl(overlay as unknown as maplibregl.IControl);
      overlayRef.current = overlay;
      // Adopt whatever layers exist right now, rather than waiting for the next
      // change that may never come.
      overlay.setProps({ layers: pendingLayers.current });
      onReady?.();
    });

    map.on('click', (event) => {
      // OpenFreeMap paints land with the background and water with vector
      // polygons. Querying those water fills lets the UI stop an inland click
      // before it is incorrectly described as an offshore EEZ violation.
      const waterLayerSpecs = map
        .getStyle()
        .layers.filter((layer) => {
          if (layer.type !== 'fill') return false;
          const id = layer.id.toLowerCase();
          const sourceLayer =
            'source-layer' in layer && typeof layer['source-layer'] === 'string'
              ? layer['source-layer'].toLowerCase()
              : '';
          return (
            id.includes('water') ||
            id.includes('ocean') ||
            id.includes('sea') ||
            sourceLayer.includes('water')
          );
        });
      const waterLayers = waterLayerSpecs.map((layer) => layer.id);
      const waterSources = [
        ...new Set(
          waterLayerSpecs.flatMap((layer) =>
            'source' in layer && typeof layer.source === 'string' ? [layer.source] : [],
          ),
        ),
      ];
      let surface: MapSurface = 'unknown';
      if (
        waterLayers.length > 0 &&
        waterSources.length > 0 &&
        waterSources.every((source) => map.isSourceLoaded(source))
      ) {
        surface = map.queryRenderedFeatures(event.point, { layers: waterLayers }).length
          ? 'water'
          : 'land';
      }
      clickRef.current?.(event.lngLat.lng, event.lngLat.lat, surface);
    });

    const notifyView = () => viewChangeRef.current?.();
    map.on('move', notifyView);
    map.on('zoom', notifyView);
    map.on('resize', notifyView);

    map.on('error', (event) => {
      // Tile 404s are noise; a style failure is not.
      if (event.error?.message?.includes('style')) {
        setFailed(`Basemap failed to load: ${event.error.message}`);
      }
    });

    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');

    return map;
    }

    return () => {
      cancelled = true;
      overlayRef.current = null;
      map?.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Layer updates are a setProps call, not a remount: deck.gl diffs them and
  // only the changed layers re-upload to the GPU.
  useEffect(() => {
    overlayRef.current?.setProps({ layers });
  }, [layers]);

  const retry = useCallback(() => {
    setFailed(null);
    window.location.reload();
  }, []);

  if (failed) {
    return (
      <div className={className}>
        <div className="grid-surface flex h-full items-center justify-center p-8">
          <div className="panel max-w-md rounded-lg p-5">
            <div className="label text-amber mb-2">Map unavailable</div>
            <p className="text-ink-1 text-sm leading-snug">{failed}</p>
            <p className="text-ink-2 mt-3 text-xs leading-snug">
              Every number ORCA computes is still available — the deterministic risk engine, the
              forecast values and their provenance do not depend on the globe rendering.
            </p>
            <button
              type="button"
              onClick={retry}
              className="border-hairline-strong text-cyan hover:bg-abyss-2 mt-4 rounded border px-3 py-1.5 text-xs transition-colors"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  // MapLibre writes `position: relative` as an INLINE style on its container,
  // which beats any positioning class we put there — `absolute inset-0` stops
  // applying and the element collapses to zero height. So the caller's className
  // goes on a wrapper we own, and MapLibre gets an inner div that simply fills
  // it. Diagnosed the hard way: a black map, 23 tiles fetched, canvas 1200x0.
  return (
    <div className={className}>
      <div ref={container} className="h-full w-full" />
    </div>
  );
}
