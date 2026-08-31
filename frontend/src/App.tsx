/**
 * The operational console.
 *
 * Layout: a full-bleed globe with the freshness strip across the top, the
 * location/vessel rail on the left, and the verdict plus evidence on the right.
 * Panels float over an unframed map — the map is the document, the panels are
 * instruments laid on it.
 *
 * The wiring that matters: clicking the sea fetches the forecast and the
 * deterministic verdict together, so the number on the card and the numbers in
 * the evidence panel are the same fetch and cannot disagree.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BitmapLayer,
  GeoJsonLayer,
  PathLayer,
  PolygonLayer,
  ScatterplotLayer,
} from '@deck.gl/layers';
import type { Layer } from '@deck.gl/core';
import { Anchor, Crosshair, Loader2, MapPin, Ruler, Waves } from 'lucide-react';
import { clsx } from 'clsx';
import { api, ApiError } from '@/lib/api';
import type {
  FenceCollection,
  FreshnessReport,
  GeofenceCheck,
  PfzZonesResponse,
  RasterCatalogue,
  Health,
  Landmark,
  PointForecast,
  RiskResult,
  DriftClass,
  DriftPlan,
  RouteCell,
  RoutePlan,
  ThresholdTable,
} from '@/lib/types';
import { AOI, HOME_VIEW, OceanMap, type OceanMapHandle } from '@/components/OceanMap';
import { FreshnessStrip } from '@/components/FreshnessStrip';
import { VerdictCard } from '@/components/VerdictCard';
import { EvidencePanel } from '@/components/EvidencePanel';
import { ChatPanel } from '@/components/ChatPanel';
import { useVoiceRoster } from '@/components/VoiceBar';
import { SeaStatePanel } from '@/components/SeaStatePanel';
import { RoutePanel } from '@/components/RoutePanel';
import { SarPanel } from '@/components/SarPanel';
import { AlertRail } from '@/components/AlertRail';
import { useAlerts } from '@/hooks/useAlerts';
import { GlobeIntro, markIntroSeen, shouldPlayIntro } from '@/scenes/GlobeIntro';
import { TreatmentFilters } from '@/components/TreatmentFilters';
import { TreatmentRail } from '@/components/TreatmentRail';
import { TREATMENT_BY_ID, type TreatmentId } from '@/lib/treatments';
import { useFrameRate } from '@/hooks/useFrameRate';
import { LayerRail } from '@/components/LayerRail';
import { BoundaryPanel } from '@/components/BoundaryPanel';
import { OperationalIntelPanel } from '@/components/OperationalIntelPanel';
import { useAgentStream } from '@/hooks/useAgentStream';
import { useRasterImages } from '@/hooks/useRasterImages';
import { useParticleFlow } from '@/hooks/useParticleFlow';

/**
 * The AOI outline as a densified ring.
 *
 * A four-point rectangle is wrong on a globe: each edge renders as a straight
 * chord rather than following the parallel or meridian, so the box visibly does
 * not match the coverage it claims. Sampling every 2 degrees fixes it.
 */
function aoiRing(step = 2): [number, number][] {
  const ring: [number, number][] = [];
  for (let lon = AOI.west; lon <= AOI.east; lon += step) ring.push([lon, AOI.south]);
  for (let lat = AOI.south; lat <= AOI.north; lat += step) ring.push([AOI.east, lat]);
  for (let lon = AOI.east; lon >= AOI.west; lon -= step) ring.push([lon, AOI.north]);
  for (let lat = AOI.north; lat >= AOI.south; lat -= step) ring.push([AOI.west, lat]);
  ring.push([AOI.west, AOI.south]);
  return ring;
}

interface Selection {
  lon: number;
  lat: number;
  label?: string;
}

export default function App() {
  const mapRef = useRef<OceanMapHandle>(null);

  const [health, setHealth] = useState<Health | null>(null);
  const [freshness, setFreshness] = useState<FreshnessReport | null>(null);
  const [landmarks, setLandmarks] = useState<Landmark[]>([]);
  const [thresholds, setThresholds] = useState<ThresholdTable | null>(null);

  const [selection, setSelection] = useState<Selection | null>(null);
  const [forecast, setForecast] = useState<PointForecast | null>(null);
  const [risk, setRisk] = useState<RiskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [loaM, setLoaM] = useState(8.2);

  const { run: agentRun, ask: askAgent, stop: stopAgent } = useAgentStream();

  // Voice settings live here rather than in the panel so they survive a panel
  // remount, and so the map side can read the chosen language later.
  const voices = useVoiceRoster();
  const [replyLanguage, setReplyLanguage] = useState('en');
  const [speakReply, setSpeakReply] = useState(true);
  const [seaViewOpen, setSeaViewOpen] = useState(false);
  const [routePlan, setRoutePlan] = useState<RoutePlan | null>(null);
  const [routeDestination, setRouteDestination] = useState<{ lat: number; lon: number } | null>(
    null,
  );
  const [pickingDestination, setPickingDestination] = useState(false);

  // SAR mode. Kept beside the route state rather than inside the panel so the
  // map layers can read it and the panel can be closed without losing a result
  // that took two thousand particles to compute.
  const [sarOpen, setSarOpen] = useState(false);
  const [sarPlan, setSarPlan] = useState<DriftPlan | null>(null);
  const [sarHours, setSarHours] = useState(6);
  const [sarClass, setSarClass] = useState('PIW-VERTICAL');
  const [sarClasses, setSarClasses] = useState<DriftClass[]>([]);

  // Proactive alerts. The stream is opened once for the session, not per panel:
  // it is the only channel where ORCA speaks first, and an alert that arrives
  // while the rail is closed still has to be counted.
  const alerts = useAlerts();
  const [alertRailOpen, setAlertRailOpen] = useState(false);
  const [watchId, setWatchId] = useState<string | null>(null);

  // Keep the watch pointed at the vessel and position the console is showing.
  // A watch that keeps judging the boat it was registered with produces alerts
  // about a boat the user is no longer in.
  useEffect(() => {
    if (!watchId) return;
    void alerts.retarget(watchId, {
      lat: selection?.lat,
      lon: selection?.lon,
      loaM,
    });
    // `alerts` is a stable hook object; the dependency that matters is the trip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchId, loaM, selection?.lat, selection?.lon]);

  useEffect(() => {
    let live = true;
    api
      .sarClasses()
      .then((payload) => {
        if (live) setSarClasses(payload.classes);
      })
      .catch(() => {
        /* the panel shows an empty selector rather than blocking the console */
      });
    return () => {
      live = false;
    };
  }, []);
  // Read once, at mount: reading it in render would restart the intro on every
  // re-render until the flag was written.
  const [intro, setIntro] = useState(shouldPlayIntro);
  const [treatment, setTreatment] = useState<TreatmentId>('standard');
  const [treatmentRailOpen, setTreatmentRailOpen] = useState(false);
  const look = TREATMENT_BY_ID[treatment];

  // The guard measures continuously but only ever takes away a treatment — there
  // is nothing to give up in Standard, and dropping data layers to protect a
  // frame rate would be the wrong trade in a safety tool.
  const frame = useFrameRate({
    active: treatment !== 'standard',
    label: `the ${look.label} treatment`,
    onDegrade: () => setTreatment('standard'),
  });

  const [rasters, setRasters] = useState<RasterCatalogue | null>(null);
  const [activeLayers, setActiveLayers] = useState<Set<string>>(new Set(['sst']));
  const [layerOpacity, setLayerOpacity] = useState(0.75);
  const [refreshing, setRefreshing] = useState(false);
  const [pfz, setPfz] = useState<PfzZonesResponse | null>(null);
  // Bumped after every ingest so the browser refetches `latest.png` instead of
  // showing a cached image of the previous run.
  const [rasterEpoch, setRasterEpoch] = useState(0);

  const [fences, setFences] = useState<FenceCollection | null>(null);
  const [geofence, setGeofence] = useState<GeofenceCheck | null>(null);
  const [showFences, setShowFences] = useState(true);
  const [heading, setHeading] = useState<number | null>(null);
  const [speed, setSpeed] = useState(8.0);

  // ---- boot ----
  useEffect(() => {
    void (async () => {
      const [h, f, l, t] = await Promise.allSettled([
        api.health(),
        api.freshness(),
        api.landmarks(),
        api.thresholds(),
      ]);
      if (h.status === 'fulfilled') setHealth(h.value);
      if (f.status === 'fulfilled') setFreshness(f.value);
      if (l.status === 'fulfilled') setLandmarks(l.value.landmarks);
      if (t.status === 'fulfilled') setThresholds(t.value);
      if (h.status === 'rejected') {
        setError('Backend unreachable. Start it with scripts\\dev.ps1.');
      }
      await loadRasters();
      void api.fences(0.01).then(setFences).catch(() => undefined);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadRasters = useCallback(async () => {
    try {
      const catalogue = await api.rasterCatalogue();
      setRasters(catalogue);
      // Bumped so the browser refetches `latest.png` rather than showing a
      // cached image of the previous ingest under the same URL.
      setRasterEpoch((n) => n + 1);
      if (catalogue.variables.some((v) => v.variable === 'pfz_rank')) {
        void api
          .pfzZones(1)
          .then(setPfz)
          .catch(() => undefined);
      }
      return catalogue;
    } catch {
      return null;
    }
  }, []);

  const refreshRasters = useCallback(async () => {
    setRefreshing(true);
    try {
      await api.refreshRasters('sobel');
      // The ingest runs in the background, so poll until the catalogue's
      // generated_at actually moves rather than guessing at a fixed delay.
      const before = rasters?.variables.find((v) => v.variable === 'sst')?.generated_at ?? null;
      for (let attempt = 0; attempt < 40; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const next = await loadRasters();
        const running = next?.refresh?.running ?? false;
        const after = next?.variables.find((v) => v.variable === 'sst')?.generated_at ?? null;
        if (!running && after !== before) break;
      }
    } finally {
      setRefreshing(false);
    }
  }, [loadRasters, rasters]);

  const toggleLayer = useCallback((variable: string) => {
    setActiveLayers((prev) => {
      const next = new Set(prev);
      if (next.has(variable)) next.delete(variable);
      else next.add(variable);
      return next;
    });
  }, []);

  // The freshness strip is only honest if it keeps up with reality.
  useEffect(() => {
    const id = setInterval(() => {
      void api.freshness().then(setFreshness).catch(() => undefined);
    }, 15_000);
    return () => clearInterval(id);
  }, []);

  // ---- the core interaction ----
  const query = useCallback(
    async (lon: number, lat: number, label?: string, loa = loaM) => {
      setSelection({ lon, lat, label });
      setLoading(true);
      setError(null);
      try {
        // Fetched together on purpose: the verdict and the evidence panel must
        // be reading the same numbers, or the card and the panel could disagree
        // on screen, which would be worse than either being slightly stale.
        const [pointForecast, verdict] = await Promise.all([
          api.forecastPoint(lat, lon),
          api.assessRisk(lat, lon, loa),
        ]);
        setForecast(pointForecast);
        setRisk(verdict);
        void api
          .geofenceCheck(lat, lon, heading ?? undefined, heading === null ? undefined : speed, geofence?.states ?? {})
          .then(setGeofence)
          .catch(() => setGeofence(null));
        void api.freshness().then(setFreshness).catch(() => undefined);
      } catch (cause) {
        const message =
          cause instanceof ApiError
            ? cause.status === 0
              ? 'Backend unreachable — ORCA is offline.'
              : cause.detail
            : 'Unexpected failure.';
        setError(message);
        setForecast(null);
        setRisk(null);
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [loaM, heading, speed],
  );

  // Re-run when the vessel changes: the same sea is a different verdict for a
  // canoe and a trawler, and that is the point worth demonstrating.
  useEffect(() => {
    if (selection) void query(selection.lon, selection.lat, selection.label, loaM);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaM]);

  // A new heading changes time-to-cross but nothing else, so re-check the
  // boundaries without re-fetching the forecast.
  useEffect(() => {
    if (!selection) return;
    void api
      .geofenceCheck(
        selection.lat,
        selection.lon,
        heading ?? undefined,
        heading === null ? undefined : speed,
        geofence?.states ?? {},
      )
      .then(setGeofence)
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [heading, speed, selection]);

  // ---- the causal link ----
  // When the agent answers, the map moves to the bounding box the answer is
  // about. This is the whole reason ui_spec exists: a judge should see that the
  // chat and the globe are one system, not two demos sharing a screen.
  const appliedSpec = useRef<string | null>(null);
  useEffect(() => {
    const spec = agentRun.final?.ui_spec;
    if (!spec || !mapRef.current) return;
    const key = `${agentRun.runId}`;
    if (appliedSpec.current === key) return;
    appliedSpec.current = key;
    const [west, south, east, north] = spec.bbox;
    mapRef.current.flyToBox(west, south, east, north);
  }, [agentRun.final, agentRun.runId]);

  // The agent's verdict is the same rule-engine output as the card's, so adopt
  // it rather than letting two copies of the same truth drift apart on screen.
  useEffect(() => {
    if (agentRun.final?.risk) setRisk(agentRun.final.risk);
  }, [agentRun.final]);

  // Decode the PNGs ourselves; see hooks/useRasterImages for why deck.gl's own
  // async image prop is not used here.
  const rasterUrls = useMemo(() => {
    const out: Record<string, string> = {};
    for (const variable of rasters?.variables ?? []) out[variable.variable] = `/api${variable.png}`;
    return out;
  }, [rasters]);
  const rasterImages = useRasterImages([...activeLayers], rasterUrls, rasterEpoch);

  // Flow particles. Only one vector field animates at a time: two overlapping
  // particle systems are visually unreadable, and the wind and the current move
  // at genuinely different speeds so a shared scale would misrepresent one.
  const flowVariable = activeLayers.has('current_uv')
    ? 'current_uv'
    : activeLayers.has('wind_uv')
      ? 'wind_uv'
      : null;
  const flowMeta = flowVariable
    ? (rasters?.variables.find((v) => v.variable === flowVariable) ?? null)
    : null;
  const flowSpec = useMemo(
    () =>
      flowMeta?.bounds && flowMeta.encoding
        ? {
            bounds: flowMeta.bounds,
            maxAbs: flowMeta.encoding.max_abs,
            particleSpeed: flowMeta.particle_speed ?? 0.4,
          }
        : null,
    [flowMeta],
  );
  const flowParticles = useParticleFlow(
    flowVariable ? (rasterImages.images[flowVariable] ?? null) : null,
    flowSpec,
    { count: 9000, enabled: Boolean(flowVariable) },
  );

  // ---- layers ----
  const layers = useMemo<Layer[]>(() => {
    const out: Layer[] = [];

    // The AOI envelope. ORCA states the area it actually covers rather than
    // implying global competence, so this boundary needs to be legible — the
    // first version was alpha 46 and invisible, which made the honesty
    // invisible too. Drawn as a densified path so it curves correctly under the
    // globe projection instead of cutting a chord across it.
    out.push(
      new PathLayer<{ path: [number, number][] }>({
        id: 'aoi-boundary',
        data: [{ path: aoiRing() }],
        getPath: (d) => d.path,
        getColor: [34, 211, 238, 120],
        getWidth: 1.5,
        widthUnits: 'pixels',
        widthMinPixels: 1,
        jointRounded: true,
        capRounded: true,
        pickable: false,
      }),
    );

    // Data rasters, drawn beneath everything else so markers and boundaries stay
    // readable over them. Ordered so a derived layer sits above its source.
    // Scalar (colour-mapped) rasters only. The u/v fields are drawn as particles
    // below, not as a bitmap: an image of packed vector components is not a
    // picture of anything.
    const ORDER = ['sst', 'chlorophyll', 'sst_gradient', 'pfz_rank'];
    for (const variable of ORDER) {
      if (!activeLayers.has(variable)) continue;
      const meta = rasters?.variables.find((v) => v.variable === variable);
      const bitmap = rasterImages.images[variable];
      if (!meta?.bounds || !bitmap) continue;
      out.push(
        new BitmapLayer({
          id: `raster-${variable}-${rasterEpoch}`,
          // The server colour-mapped this, and the sidecar's bounds are handed
          // straight through, so there is no chance to transpose them here.
          image: bitmap,
          bounds: meta.bounds,
          opacity: layerOpacity,
          pickable: false,
        }),
      );
    }

    // Flow particles. Drawn as short trailing segments: the streak length reads
    // as speed, and the taper gives direction without arrowheads.
    if (flowVariable && flowParticles.length > 0 && flowSpec) {
      const isCurrent = flowVariable === 'current_uv';
      out.push(
        // PathLayer with two-point paths rather than LineLayer. LineLayer was set
        // up correctly here — visible, 585 instances, a live model, valid
        // coordinates 0.3 degrees apart — and drew nothing at all, while
        // PathLayer renders reliably in this same overlay (the AOI boundary uses
        // it). Rounded caps also suit a flow streak better than butt ends.
        new PathLayer<(typeof flowParticles)[number]>({
          id: `flow-${flowVariable}`,
          data: flowParticles,
          getPath: (d) => [d.from, d.to],
          getColor: (d) => {
            // Opacity by speed: uniform brightness would make a calm basin look
            // as energetic as a monsoon jet, which misrepresents the field.
            const ratio = Math.min(d.speed / (flowSpec.maxAbs * 0.45), 1);
            const alpha = 90 + ratio * 160;
            return isCurrent ? [34, 211, 238, alpha] : [186, 214, 235, alpha];
          },
          getWidth: (d) => 1.0 + Math.min(d.speed / (flowSpec.maxAbs * 0.4), 1) * 1.6,
          widthUnits: 'pixels',
          widthMinPixels: 0.8,
          capRounded: true,
          jointRounded: true,
          pickable: false,
          updateTriggers: { getColor: flowVariable, getWidth: flowVariable },
        }),
      );
    }

    // PFZ outlines on top of the rank raster: the fill shows extent, the outline
    // makes an individual zone something you can point at.
    if (activeLayers.has('pfz_rank') && pfz?.zones?.length) {
      out.push(
        new PolygonLayer<(typeof pfz.zones)[number]>({
          id: 'pfz-zones',
          data: pfz.zones.filter((z) => z.rank >= 2).slice(0, 60),
          getPolygon: (d) => d.polygon,
          stroked: true,
          filled: false,
          getLineColor: (d) => (d.rank === 3 ? [34, 211, 238, 255] : [34, 211, 238, 170]),
          getLineWidth: (d) => (d.rank === 3 ? 2 : 1.2),
          lineWidthUnits: 'pixels',
          lineWidthMinPixels: 1,
          pickable: true,
        }),
      );
    }

    // Maritime boundaries. The IMBL treaty lines get the emphasis, not the EEZ:
    // crossing one is a legal and safety event, while the EEZ is context.
    if (showFences && fences?.features?.length) {
      out.push(
        new GeoJsonLayer({
          id: 'fences-eez',
          data: {
            ...fences,
            features: fences.features.filter((f) => f.properties.kind !== 'imbl'),
          } as never,
          stroked: true,
          filled: false,
          getLineColor: [148, 168, 187, 90],
          getLineWidth: 1,
          lineWidthUnits: 'pixels',
          lineWidthMinPixels: 1,
          pickable: true,
        }),
        new GeoJsonLayer({
          id: 'fences-imbl',
          data: {
            ...fences,
            features: fences.features.filter((f) => f.properties.kind === 'imbl'),
          } as never,
          stroked: true,
          filled: false,
          getLineColor: [245, 158, 11, 220],
          getLineWidth: 2,
          lineWidthUnits: 'pixels',
          lineWidthMinPixels: 1.5,
          pickable: true,
        }),
      );
    }

    // The nearest point on each boundary in range, and the line to it. Showing
    // WHERE the crossing would happen is more useful than a distance alone.
    if (geofence?.proximities?.length && selection) {
      const relevant = geofence.proximities.filter(
        (p) => p.kind === 'imbl' && p.distance_km < 60,
      );
      if (relevant.length > 0) {
        out.push(
          new PathLayer<(typeof relevant)[number]>({
            id: 'boundary-bearings',
            data: relevant,
            getPath: (d) => [
              [selection.lon, selection.lat],
              [d.nearest_point.lon, d.nearest_point.lat],
            ],
            getColor: (d) =>
              d.time_to_cross_min !== null && d.time_to_cross_min < 60
                ? [245, 158, 11, 200]
                : [148, 168, 187, 110],
            getWidth: 1.2,
            widthUnits: 'pixels',
            widthMinPixels: 1,
            pickable: false,
          }),
        );
      }
    }

    if (landmarks.length > 0) {
      out.push(
        new ScatterplotLayer<Landmark>({
          id: 'landmarks',
          data: landmarks,
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 4,
          radiusUnits: 'pixels',
          radiusMinPixels: 3,
          getFillColor: [148, 168, 187, 190],
          getLineColor: [11, 23, 37, 255],
          lineWidthMinPixels: 1,
          stroked: true,
          pickable: true,
          onClick: ({ object }) => {
            if (object) void query(object.lon, object.lat, object.label);
          },
        }),
      );
    }

    // ---- the SAR search area --------------------------------------------
    //
    // Widest first, so the 50% ring composites visibly ON TOP of the 95% one
    // instead of being buried under it. Filled at low alpha and stroked, because
    // a fill alone over an SST raster is unreadable and a stroke alone does not
    // read as an area to sweep.
    if (sarPlan?.areas?.length) {
      const ordered = [...sarPlan.areas].sort((a, b) => b.fraction - a.fraction);
      for (const area of ordered) {
        const tight = area.fraction <= 0.6;
        out.push(
          new GeoJsonLayer({
            id: `sar-area-${area.fraction}`,
            data: {
              type: 'Feature',
              geometry: { type: 'Polygon', coordinates: [area.ring] },
              properties: { fraction: area.fraction },
            } as never,
            filled: true,
            stroked: true,
            getFillColor: tight ? [232, 163, 61, 46] : [239, 68, 68, 30],
            getLineColor: tight ? [232, 163, 61, 220] : [239, 68, 68, 200],
            getLineWidth: 2,
            lineWidthUnits: 'pixels',
            pickable: false,
          }),
        );
      }

      out.push(
        // The mean drift track. Dashed would be nicer but PathLayer has no dash
        // support without an extension, and adding one for a hairline is not
        // worth the bundle.
        new PathLayer<{ path: [number, number][] }>({
          id: 'sar-track',
          data: [{ path: sarPlan.track }],
          getPath: (d) => d.path,
          getColor: [255, 214, 170, 190],
          getWidth: 1.5,
          widthUnits: 'pixels',
          pickable: false,
        }),
        new ScatterplotLayer<{ position: [number, number] }>({
          id: 'sar-lkp',
          data: [{ position: sarPlan.last_known_position }],
          getPosition: (d) => d.position,
          getRadius: 6,
          radiusUnits: 'pixels',
          filled: false,
          stroked: true,
          getLineColor: [255, 255, 255, 235],
          getLineWidth: 2,
          lineWidthUnits: 'pixels',
          pickable: false,
        }),
      );
    }

    // ---- the planned passage -------------------------------------------
    //
    // Three layers, in this order, because each one answers a different
    // question: where does it go, where does it turn, and what did it avoid.
    if (routePlan?.ok && routePlan.path && routePlan.path.length > 1) {
      out.push(
        // A wide dark casing under the line. Without it a cyan route over the
        // orange SST raster is genuinely hard to follow, and a route you cannot
        // trace is not a route.
        new PathLayer<{ path: [number, number][] }>({
          id: 'route-casing',
          data: [{ path: routePlan.path }],
          getPath: (d) => d.path,
          getColor: [4, 12, 22, 220],
          getWidth: 7,
          widthUnits: 'pixels',
          jointRounded: true,
          capRounded: true,
          pickable: false,
        }),
        new PathLayer<{ path: [number, number][] }>({
          id: 'route-line',
          data: [{ path: routePlan.path }],
          getPath: (d) => d.path,
          // Coloured by the WORST verdict on the route, not the mean: a passage
          // that is GO for 90% of its length and CAUTION for one leg is a
          // CAUTION passage, and the line should say so at a glance.
          getColor:
            routePlan.worst_verdict === 'GO'
              ? [52, 211, 153, 255]
              : routePlan.worst_verdict === 'CAUTION'
                ? [232, 163, 61, 255]
                : [148, 163, 184, 255],
          getWidth: 3,
          widthUnits: 'pixels',
          jointRounded: true,
          capRounded: true,
          pickable: false,
        }),
      );

      if (routePlan.waypoints?.length) {
        out.push(
          new ScatterplotLayer<RouteCell>({
            id: 'route-waypoints',
            data: routePlan.waypoints,
            getPosition: (d) => [d.lon, d.lat],
            getRadius: 4,
            radiusUnits: 'pixels',
            getFillColor: [4, 12, 22, 235],
            getLineColor: [226, 240, 247, 235],
            getLineWidth: 1.5,
            lineWidthUnits: 'pixels',
            stroked: true,
            pickable: true,
            onClick: ({ object }) => {
              if (object) void query(object.lon, object.lat);
            },
          }),
        );
      }
    }

    // Cells the direct line would have crossed and the rule engine refused.
    // Drawn on BOTH outcomes: on a route they justify the detour, and on a
    // refusal they are the entire answer.
    const refusedCells = (routePlan?.refused_on_direct_line ?? []).filter((c) => !c.passable);
    if (refusedCells.length) {
      out.push(
        new ScatterplotLayer<RouteCell>({
          id: 'route-refused',
          data: refusedCells,
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 7,
          radiusUnits: 'pixels',
          filled: false,
          stroked: true,
          getLineColor: [239, 68, 68, 220],
          getLineWidth: 2,
          lineWidthUnits: 'pixels',
          pickable: true,
        }),
      );
    }

    if (routeDestination) {
      out.push(
        new ScatterplotLayer<{ lat: number; lon: number }>({
          id: 'route-destination',
          data: [routeDestination],
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 7,
          radiusUnits: 'pixels',
          filled: false,
          stroked: true,
          getLineColor: [34, 211, 238, 235],
          getLineWidth: 2,
          lineWidthUnits: 'pixels',
          pickable: false,
        }),
      );
    }

    if (selection) {
      const colour: [number, number, number] =
        risk?.verdict === 'GO'
          ? [52, 211, 153]
          : risk?.verdict === 'CAUTION'
            ? [245, 158, 11]
            : risk?.verdict === 'NO-GO'
              ? [239, 68, 68]
              : [34, 211, 238];

      out.push(
        new ScatterplotLayer<Selection>({
          id: 'selection-halo',
          data: [selection],
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 18,
          radiusUnits: 'pixels',
          getFillColor: [...colour, 40],
          pickable: false,
        }),
        new ScatterplotLayer<Selection>({
          id: 'selection',
          data: [selection],
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 6,
          radiusUnits: 'pixels',
          getFillColor: [...colour, 255],
          getLineColor: [4, 9, 15, 255],
          lineWidthMinPixels: 2,
          stroked: true,
          pickable: false,
          updateTriggers: { getFillColor: risk?.verdict },
        }),
      );
    }

    return out;
  }, [
    landmarks,
    selection,
    routePlan,
    routeDestination,
    sarPlan,
    risk?.verdict,
    query,
    rasters,
    activeLayers,
    layerOpacity,
    rasterEpoch,
    pfz,
    rasterImages.images,
    fences,
    showFences,
    geofence,
    flowVariable,
    flowParticles,
    flowSpec,
  ]);

  const activeClass = useMemo(
    () => thresholds?.classes.find((c) => loaM >= c.loa_range_m[0] && loaM < c.loa_range_m[1]),
    [thresholds, loaM],
  );

  return (
    <div className="bg-abyss-0 flex h-full flex-col">
      {/* The console mounts and loads UNDERNEATH the intro, so the six seconds
          are spent rather than wasted: by the time the globe fades the basemap,
          the rasters and the boundaries are already there. */}
      {intro && (
        <GlobeIntro
          onFinished={() => {
            markIntroSeen();
            setIntro(false);
          }}
        />
      )}
      <TreatmentFilters />
      <FreshnessStrip health={health} freshness={freshness} />

      <div className="relative flex-1 overflow-hidden">
        {/* The treated layer. Only the MAP is inside it: running a filter over
            the glass panels too would recolour the verdict and make the numbers
            unreadable, and a NO-GO rendered in phosphor green is exactly the
            kind of clever that gets someone hurt. */}
        <div
          className="absolute inset-0"
          style={{
            filter: look.filter ?? undefined,
            // Promote to its own compositor layer so the filter is applied once
            // per frame on the GPU rather than re-rasterising on every pan.
            willChange: look.filter ? 'filter' : undefined,
            ...look.style,
          }}
        >
          <OceanMap
            ref={mapRef}
            layers={layers}
            onClick={(lon, lat) => {
            // While picking a destination the click sets the endpoint and does
            // NOT move the selection: re-running the point forecast would throw
            // away the origin the user is planning from.
            if (pickingDestination) {
              setRouteDestination({ lat, lon });
              setPickingDestination(false);
              setRoutePlan(null);
              return;
            }
            void query(lon, lat);
          }}
            className="absolute inset-0"
          />
          {look.overlay && (
            <div className="pointer-events-none absolute inset-0" style={look.overlay} />
          )}
        </div>

        {/* ---------------- left rail: place and vessel ---------------- */}
        <div className="pointer-events-none absolute top-3 bottom-3 left-3 z-20 flex w-64 flex-col gap-2 overflow-y-auto">
          <div className="glass pointer-events-auto rounded-lg">
            <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
              <MapPin className="text-cyan h-3.5 w-3.5" aria-hidden />
              <span className="label">Location</span>
              <button
                type="button"
                onClick={() => mapRef.current?.resetView()}
                className="text-ink-2 hover:text-cyan ml-auto text-2xs transition-colors"
                title={`Return to ${HOME_VIEW.center[1]}°N ${HOME_VIEW.center[0]}°E`}
              >
                reset
              </button>
            </div>
            <div className="max-h-52 overflow-y-auto p-1.5">
              {landmarks.map((place) => {
                const active =
                  selection &&
                  Math.abs(selection.lat - place.lat) < 1e-6 &&
                  Math.abs(selection.lon - place.lon) < 1e-6;
                return (
                  <button
                    key={place.key}
                    type="button"
                    onClick={() => {
                      mapRef.current?.flyTo(place.lon, place.lat, 8);
                      void query(place.lon, place.lat, place.label);
                    }}
                    className={clsx(
                      'flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors',
                      active ? 'bg-cyan/12 text-cyan' : 'text-ink-1 hover:bg-abyss-2',
                    )}
                  >
                    <Anchor className="h-3 w-3 shrink-0 opacity-60" aria-hidden />
                    <span className="truncate">{place.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="glass pointer-events-auto rounded-lg">
            <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
              <Ruler className="text-cyan h-3.5 w-3.5" aria-hidden />
              <span className="label">Your vessel</span>
            </div>
            <div className="p-3">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="data text-ink-0 text-lg">{loaM.toFixed(1)} m</span>
                <span className="text-ink-2 text-2xs">length overall</span>
              </div>
              <input
                type="range"
                min={3}
                max={30}
                step={0.1}
                value={loaM}
                onChange={(event) => setLoaM(Number(event.target.value))}
                className="accent-cyan w-full"
                aria-label="Boat length overall in metres"
              />
              {activeClass && (
                <div className="border-hairline mt-2 border-t pt-2">
                  <div className="text-ink-1 text-2xs leading-snug">{activeClass.label}</div>
                  <div className="data text-ink-2 mt-1 flex gap-3 text-2xs">
                    <span>
                      Hs&nbsp;<span className="text-ink-0">{activeClass.max_wave_m} m</span>
                    </span>
                    <span>
                      wind&nbsp;<span className="text-ink-0">{activeClass.max_wind_kn} kn</span>
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="pointer-events-auto">
            <BoundaryPanel
              check={geofence}
              heading={heading}
              speed={speed}
              onHeading={setHeading}
              onSpeed={setSpeed}
              capUrl={selection ? api.capUrl(selection.lat, selection.lon, loaM, 'ta') : null}
              visible={showFences}
              onToggleVisible={() => setShowFences((v) => !v)}
            />
          </div>

          <div className="pointer-events-auto">
            <LayerRail
              catalogue={rasters}
              active={activeLayers}
              onToggle={toggleLayer}
              onRefresh={() => void refreshRasters()}
              refreshing={refreshing || (rasters?.refresh?.running ?? false)}
              opacity={layerOpacity}
              onOpacity={setLayerOpacity}
            />
          </div>
        </div>

        {/* ---------------- centre-left: chat + live trace ---------------- */}
        <div className="glass pointer-events-auto absolute top-3 bottom-3 left-[17.5rem] z-20 flex w-[23rem] flex-col rounded-lg">
          <ChatPanel
            run={agentRun}
            disabled={!selection}
            placeLabel={
              selection
                ? (selection.label ??
                  `${selection.lat.toFixed(2)}°N ${selection.lon.toFixed(2)}°E`)
                : null
            }
            onAsk={(question) => {
              if (!selection) return;
              void askAgent({
                question,
                lat: selection.lat,
                lon: selection.lon,
                loaM,
                place: selection.label,
                replyLanguage,
                speak: speakReply,
              });
            }}
            onStop={stopAgent}
            language={replyLanguage}
            onLanguage={setReplyLanguage}
            speak={speakReply}
            onSpeak={setSpeakReply}
            voices={voices}
          />
        </div>

        {/* ------- bottom centre: passage planning, then the sea view ------- */}
        {selection && (
          <div className="pointer-events-none absolute bottom-3 left-[41rem] z-20 w-[22rem]">
            <RoutePanel
              origin={selection}
              destination={routeDestination}
              onPickDestination={() => setPickingDestination((value) => !value)}
              pickingDestination={pickingDestination}
              loaM={loaM}
              speedKn={speed}
              plan={routePlan}
              onPlan={setRoutePlan}
            />
          </div>
        )}

        {/* ------- the forecast as the sea it describes -------
            Anchored bottom-left of the map area so it grows upward and never
            covers the verdict card, which stays the authority on the page. */}
        {selection && (
          <div className="pointer-events-none absolute right-[25.5rem] bottom-3 z-20 flex flex-col items-end">
            <SeaStatePanel
              forecast={forecast}
              boatClass={activeClass}
              loaM={loaM}
              open={seaViewOpen}
              onToggle={setSeaViewOpen}
            />
          </div>
        )}

        {/* ---------------- top left of the map: alerts ---------------- */}
        <div className="pointer-events-none absolute top-3 left-[41rem] z-30 flex flex-col items-start">
          <AlertRail
            alerts={alerts.alerts}
            status={alerts.status}
            connected={alerts.connected}
            unseen={alerts.unseen}
            onOpen={alerts.markAllSeen}
            onAcknowledge={(id) => void alerts.acknowledge(id)}
            onCheckNow={alerts.checkNow}
            canWatch={Boolean(selection)}
            watching={Boolean(watchId)}
            onWatchToggle={() => {
              if (watchId) {
                void alerts.unwatch(watchId);
                setWatchId(null);
                return;
              }
              if (!selection) return;
              void alerts
                .watch({
                  lat: selection.lat,
                  lon: selection.lon,
                  loaM,
                  label: selection.label,
                })
                .then(setWatchId)
                .catch(() => setWatchId(null));
            }}
            open={alertRailOpen}
            onToggle={setAlertRailOpen}
          />
        </div>

        {/* ---------------- top right of the map: SAR mode ---------------- */}
        <div className="pointer-events-none absolute top-3 right-[25.5rem] z-20 flex flex-col items-end gap-2">
          <OperationalIntelPanel point={selection} />
          <SarPanel
            origin={selection}
            hours={sarHours}
            onHours={setSarHours}
            objectClass={sarClass}
            onObjectClass={setSarClass}
            classes={sarClasses}
            plan={sarPlan}
            onPlan={(next) => {
              setSarPlan(next);
              // Frame the search area. A 2000 km² area is about a 25 km radius,
              // which at EEZ zoom is ten pixels — computing it and then not
              // showing it is the same as not computing it. Padded generously so
              // the ring is not flush against the panel edges.
              const ring = next?.areas?.[next.areas.length - 1]?.ring;
              if (ring?.length) {
                const lons = ring.map((p) => p[0]);
                const lats = ring.map((p) => p[1]);
                const pad = 0.25;
                mapRef.current?.flyToBox(
                  Math.min(...lons) - pad,
                  Math.min(...lats) - pad,
                  Math.max(...lons) + pad,
                  Math.max(...lats) + pad,
                );
              }
            }}
            open={sarOpen}
            onToggle={setSarOpen}
          />
        </div>

        {/* ---------------- top centre: visual treatments ---------------- */}
        <div className="pointer-events-none absolute top-3 left-1/2 z-20 -translate-x-1/2">
          <TreatmentRail
            active={treatment}
            onChange={setTreatment}
            fps={frame.fps}
            degradedReason={frame.reason}
            open={treatmentRailOpen}
            onToggle={setTreatmentRailOpen}
          />
        </div>

        {/* ---------------- right: verdict + evidence ---------------- */}
        <div className="pointer-events-none absolute top-3 right-3 bottom-3 z-20 flex w-[24rem] flex-col gap-2">
          {loading && !risk && (
            <div className="glass pointer-events-auto flex items-center gap-2 rounded-lg px-4 py-3">
              <Loader2 className="text-cyan h-4 w-4 animate-spin" aria-hidden />
              <span className="text-ink-1 text-xs">
                Fetching live conditions and computing the verdict…
              </span>
            </div>
          )}

          {error && (
            <div className="glass border-red/40 pointer-events-auto rounded-lg border px-4 py-3">
              <div className="label text-red mb-1">Request failed</div>
              <p className="text-ink-1 text-xs leading-snug">{error}</p>
            </div>
          )}

          {risk && (
            <div className="pointer-events-auto">
              <VerdictCard result={risk} />
            </div>
          )}

          {forecast && (
            <div className="glass pointer-events-auto min-h-0 flex-1 overflow-y-auto rounded-lg">
              <div className="glass border-hairline sticky top-0 z-10 flex items-center gap-1.5 border-b px-3 py-2">
                <Waves className="text-cyan h-3.5 w-3.5" aria-hidden />
                <span className="label">Evidence — how ORCA knows</span>
                {loading && <Loader2 className="text-cyan ml-auto h-3 w-3 animate-spin" aria-hidden />}
              </div>
              <EvidencePanel forecast={forecast} />
            </div>
          )}
        </div>

        {/* ---------------- empty state ---------------- */}
        {!selection && !loading && (
          <div className="pointer-events-none absolute inset-y-0 right-[25rem] left-[41.5rem] z-10 flex items-center justify-center">
            {/* `pointer-events-none`, deliberately. The card says "click anywhere
                on the sea" and then, being `pointer-events-auto`, swallowed every
                click in the middle of the map — a user following the instruction
                literally got nothing back. There is nothing interactive on it, so
                it has no business intercepting anything. */}
            <div className="glass pointer-events-none max-w-md rounded-lg p-5 text-center">
              <Crosshair className="text-cyan mx-auto mb-3 h-6 w-6" aria-hidden />
              <h1 className="text-ink-0 mb-1.5 text-base font-semibold">
                Click anywhere on the sea
              </h1>
              <p className="text-ink-1 text-xs leading-relaxed">
                ORCA pulls live wave, wind, visibility and convective data for that point, computes
                a GO / NO-GO with a deterministic rule engine, and shows you every number it used —
                with its source, its age and its provenance state.
              </p>
              <p className="text-ink-3 mt-3 text-2xs leading-snug">
                Coverage is the Indian EEZ envelope, {AOI.west}–{AOI.east}°E and {AOI.south}–
                {AOI.north}°N, outlined in cyan.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
