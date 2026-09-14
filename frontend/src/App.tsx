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
import {
  BellRing,
  Bot,
  ChevronLeft,
  ChevronRight,
  Crosshair,
  LifeBuoy,
  Siren,
  Loader2,
  MapPin,
  Navigation,
  Palette,
  Ruler,
  Satellite,
  Waves,
} from 'lucide-react';
import { clsx } from 'clsx';
import { api, ApiError } from '@/lib/api';
import type {
  DistressResponse,
  FenceCollection,
  FreshnessReport,
  GeofenceCheck,
  PfzZonesResponse,
  RasterCatalogue,
  Landmark,
  PointForecast,
  RiskResult,
  DriftClass,
  DriftPlan,
  RouteCell,
  RoutePlan,
  ThresholdTable,
} from '@/lib/types';
import {
  AOI,
  HOME_VIEW,
  OceanMap,
  type MapSurface,
  type OceanMapHandle,
} from '@/components/OceanMap';
import { FreshnessStrip } from '@/components/FreshnessStrip';
import { ResearcherWorkspace } from '@/components/ResearcherWorkspace';
import { VerdictCard } from '@/components/VerdictCard';
import { EvidencePanel } from '@/components/EvidencePanel';
import { ChatPanel } from '@/components/ChatPanel';
import { useVoiceRoster } from '@/components/VoiceBar';
import { SeaStatePanel } from '@/components/SeaStatePanel';
import { RoutePanel } from '@/components/RoutePanel';
import { DistressPanel } from '@/components/DistressPanel';
import { SarPanel } from '@/components/SarPanel';
import { AlertRail } from '@/components/AlertRail';
import { useAlerts } from '@/hooks/useAlerts';
import { GlobeIntro, markIntroSeen, shouldPlayIntro } from '@/scenes/GlobeIntro';
import { TreatmentFilters } from '@/components/TreatmentFilters';
import { TreatmentRail } from '@/components/TreatmentRail';
import { radarOverlayStyle, TREATMENT_BY_ID, type TreatmentId } from '@/lib/treatments';
import { useFrameRate } from '@/hooks/useFrameRate';
import { LayerRail } from '@/components/LayerRail';
import { BoundaryPanel } from '@/components/BoundaryPanel';
import { LocationSearch } from '@/components/LocationSearch';
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
  surface: MapSurface;
}

const MARINE_EVIDENCE = new Set([
  'wave_height',
  'wave_period',
  'swell_height',
  'wave_direction',
  'sst',
  'sst_satellite',
  'sst_uncertainty',
  'sea_surface_current',
  'sea_surface_current_direction',
]);

/** Compact chip labels — do not show raw IND-* codes as the primary text. */
const VESSEL_CHIPS: { code: string; label: string }[] = [
  { code: 'IND-TRAD', label: 'Traditional / non-motorised' },
  { code: 'IND-MOT-S', label: 'Small motorised' },
  { code: 'IND-MECH-S', label: 'Small mechanised' },
  { code: 'IND-MECH-L', label: 'Large mechanised' },
  { code: 'IND-DEEPSEA', label: 'Deep-sea' },
];

/** Default prototype category — category only; never invent an LOA for APIs. */
const DEFAULT_BOAT_CLASS = 'IND-MOT-S';

/** Visual-only hull length for the 3D sea view. Never sent to safety APIs. */
function visualLoaM(activeClass: { loa_range_m: [number, number] } | undefined): number {
  if (activeClass) {
    const [lo, hiRaw] = activeClass.loa_range_m;
    const hi = hiRaw >= 1000 ? lo + 10 : hiRaw;
    return Math.round(((lo + hi) / 2) * 10) / 10;
  }
  return 8;
}

function resolvedSurface(requested: MapSurface, pointForecast: PointForecast): MapSurface {
  if (requested !== 'unknown') return requested;
  return Object.keys(pointForecast.evidence).some((variable) => MARINE_EVIDENCE.has(variable))
    ? 'water'
    : 'land';
}

export default function App() {
  const mapRef = useRef<OceanMapHandle>(null);
  const pointQueryEpoch = useRef(0);

  const [freshness, setFreshness] = useState<FreshnessReport | null>(null);
  const [landmarks, setLandmarks] = useState<Landmark[]>([]);
  const [thresholds, setThresholds] = useState<ThresholdTable | null>(null);

  const [selection, setSelection] = useState<Selection | null>(null);
  const [forecast, setForecast] = useState<PointForecast | null>(null);
  const [risk, setRisk] = useState<RiskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verdictPanelOpen, setVerdictPanelOpen] = useState(true);
  const [evidencePanelOpen, setEvidencePanelOpen] = useState(true);
  const [chatOpen, setChatOpen] = useState(true);

  const [boatClassCode, setBoatClassCode] = useState<string>(DEFAULT_BOAT_CLASS);

  const { run: agentRun, ask: askAgent, stop: stopAgent } = useAgentStream();

  // Voice settings live here rather than in the panel so they survive a panel
  // remount, and so the map side can read the chosen language later.
  const voices = useVoiceRoster();
  const [replyLanguage, setReplyLanguage] = useState('en');
  const [speakReply, setSpeakReply] = useState(true);
  const [seaViewOpen, setSeaViewOpen] = useState(false);
  const [routeOpen, setRouteOpen] = useState(false);
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
  const [distressOpen, setDistressOpen] = useState(false);
  const [distress, setDistress] = useState<DistressResponse | null>(null);
  const [sarHours, setSarHours] = useState(6);
  const [sarClass, setSarClass] = useState('PIW-VERTICAL');
  const [sarClasses, setSarClasses] = useState<DriftClass[]>([]);

  // Proactive alerts. The stream is opened once for the session, not per panel:
  // it is the only channel where ORCA speaks first, and an alert that arrives
  // while the rail is closed still has to be counted.
  const alerts = useAlerts();
  const [alertRailOpen, setAlertRailOpen] = useState(false);
  const [intelOpen, setIntelOpen] = useState(false);
  const [leftRailOpen, setLeftRailOpen] = useState(true);
  const [watchId, setWatchId] = useState<string | null>(null);

  // Keep the watch pointed at the vessel and position the console is showing.
  // A watch that keeps judging the boat it was registered with produces alerts
  // about a boat the user is no longer in.
  useEffect(() => {
    if (!watchId) return;
    void alerts.retarget(watchId, {
      lat: selection?.lat,
      lon: selection?.lon,
      boatClassCode,
    });
    // `alerts` is a stable hook object; the dependency that matters is the trip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchId, boatClassCode, selection?.lat, selection?.lon]);

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
  const [researchOpen, setResearchOpen] = useState(false);
  const [lookOrigin, setLookOrigin] = useState<{ x: number; y: number } | null>(null);
  const look = TREATMENT_BY_ID[treatment];
  const lookOverlay =
    treatment === 'radar' && lookOrigin
      ? radarOverlayStyle(lookOrigin.x, lookOrigin.y)
      : look.overlay;

  const syncLookOrigin = useCallback(() => {
    if (!selection) {
      setLookOrigin(null);
      return;
    }
    setLookOrigin(mapRef.current?.projectPct(selection.lon, selection.lat) ?? null);
  }, [selection]);

  // The guard measures continuously but only ever takes away a treatment — there
  // is nothing to give up in Standard, and dropping data layers to protect a
  // frame rate would be the wrong trade in a safety tool.
  const frame = useFrameRate({
    active: treatment !== 'standard',
    label: `the ${look.label} treatment`,
    onDegrade: () => setTreatment('standard'),
  });

  useEffect(() => {
    syncLookOrigin();
  }, [syncLookOrigin, treatment]);

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
  const [heading] = useState<number | null>(null);
  const [speed] = useState(8.0);

  // ---- boot ----
  useEffect(() => {
    void (async () => {
      const [h, f, l, t] = await Promise.allSettled([
        api.health(),
        api.freshness(),
        api.landmarks(),
        api.thresholds(),
      ]);
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
    async (
      lon: number,
      lat: number,
      label?: string,
      requestedSurface: MapSurface = 'unknown',
    ) => {
      const queryEpoch = ++pointQueryEpoch.current;
      setSelection({ lon, lat, label, surface: requestedSurface });
      setLoading(true);
      setError(null);
      if (requestedSurface === 'land') {
        setGeofence(null);
        setRouteDestination(null);
        setRoutePlan(null);
        setPickingDestination(false);
        setSeaViewOpen(false);
        setRouteOpen(false);
      }
      try {
        // Fetched together on purpose: the verdict and the evidence panel must
        // be reading the same numbers, or the card and the panel could disagree
        // on screen, which would be worse than either being slightly stale.
        // Vessel comes from state only — never invent a default LOA for safety.
        const [pointForecast, verdict] = await Promise.all([
          api.forecastPoint(lat, lon, requestedSurface !== 'land'),
          api.assessRisk(lat, lon, null, boatClassCode),
        ]);
        if (queryEpoch !== pointQueryEpoch.current) return;
        const surface = resolvedSurface(requestedSurface, pointForecast);
        setSelection((current) =>
          current && current.lon === lon && current.lat === lat
            ? { ...current, surface }
            : current,
        );
        setForecast(pointForecast);
        setRisk(verdict);
        if (surface === 'land') {
          setGeofence(null);
          setRouteDestination(null);
          setRoutePlan(null);
          setPickingDestination(false);
          setSeaViewOpen(false);
          setRouteOpen(false);
        } else {
          void api
            .geofenceCheck(
              lat,
              lon,
              heading ?? undefined,
              heading === null ? undefined : speed,
              geofence?.states ?? {},
            )
            .then((next) => {
              if (queryEpoch === pointQueryEpoch.current) setGeofence(next);
            })
            .catch(() => {
              if (queryEpoch === pointQueryEpoch.current) setGeofence(null);
            });
        }
        void api.freshness().then(setFreshness).catch(() => undefined);
      } catch (cause) {
        if (queryEpoch !== pointQueryEpoch.current) return;
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
        if (queryEpoch === pointQueryEpoch.current) setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [boatClassCode, heading, speed],
  );

  const rejectLandDestination = useCallback(
    (lon: number, lat: number, surface: MapSurface) => {
      if (surface !== 'land') return false;
      setPickingDestination(false);
      setRouteDestination(null);
      setRoutePlan(null);
      setLoading(false);
      setError(
        `${lat.toFixed(3)}°N ${lon.toFixed(3)}°E is on land. ` +
        'A marine route destination must be placed on water.',
      );
      return true;
    },
    [],
  );

  // Re-run when the vessel changes: the same sea is a different verdict for a
  // canoe and a trawler, and that is the point worth demonstrating.
  useEffect(() => {
    if (selection) {
      void query(selection.lon, selection.lat, selection.label, selection.surface);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boatClassCode]);

  // A new heading changes time-to-cross but nothing else, so re-check the
  // boundaries without re-fetching the forecast.
  useEffect(() => {
    if (!selection || selection.surface !== 'water') return;
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
            if (object) void query(object.lon, object.lat, object.label, 'water');
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

    // ---- the distress response ------------------------------------------
    //
    // A different palette from the SAR rings on purpose. SAR mode answers a
    // modelling question; distress mode is an incident, and letting the two look
    // identical on the map would let a coordinator confuse a what-if with a live
    // case. The transit track is the piece with no SAR equivalent: it is the
    // only line here that a crew actually steers.
    if (distress) {
      const rings = [...distress.search_area.containment].sort((a, b) => b.fraction - a.fraction);
      for (const area of rings) {
        const tight = area.fraction <= 0.6;
        out.push(
          new GeoJsonLayer({
            id: `distress-area-${area.fraction}`,
            data: {
              type: 'Feature',
              geometry: { type: 'Polygon', coordinates: [area.ring] },
              properties: { fraction: area.fraction },
            } as never,
            filled: true,
            stroked: true,
            getFillColor: tight ? [244, 63, 94, 58] : [244, 63, 94, 26],
            getLineColor: tight ? [253, 164, 175, 235] : [244, 63, 94, 190],
            getLineWidth: 2.5,
            lineWidthUnits: 'pixels',
            pickable: false,
          }),
        );
      }

      // The transit, only when it was actually routed. A great-circle fallback
      // is an ETA, not a track to steer, and drawing it as a line on a chart
      // would invite somebody to follow it across a headland.
      if (distress.transit.routed && distress.transit.path?.length) {
        out.push(
          new PathLayer<{ path: [number, number][] }>({
            id: 'distress-transit',
            data: [{ path: distress.transit.path }],
            getPath: (d) => d.path,
            getColor: [56, 189, 248, 225],
            getWidth: 3,
            widthUnits: 'pixels',
            pickable: false,
          }),
        );
      }

      out.push(
        new ScatterplotLayer<{ position: [number, number]; kind: string }>({
          id: 'distress-centres',
          data: distress.notify.map((contact) => ({
            position: [contact.coordinates.lon, contact.coordinates.lat] as [number, number],
            kind: contact.kind,
          })),
          getPosition: (d) => d.position,
          getRadius: (d) => (d.kind === 'MRCC' ? 8 : 6),
          radiusUnits: 'pixels',
          filled: true,
          stroked: true,
          getFillColor: [14, 165, 233, 200],
          getLineColor: [240, 249, 255, 240],
          getLineWidth: 1.5,
          lineWidthUnits: 'pixels',
          pickable: false,
        }),
        new ScatterplotLayer<{ position: [number, number] }>({
          id: 'distress-lkp',
          data: [
            {
              position: [
                distress.incident.last_known_position.lon,
                distress.incident.last_known_position.lat,
              ] as [number, number],
            },
          ],
          getPosition: (d) => d.position,
          getRadius: 7,
          radiusUnits: 'pixels',
          filled: false,
          stroked: true,
          getLineColor: [255, 255, 255, 245],
          getLineWidth: 2.5,
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
              if (object) void query(object.lon, object.lat, undefined, 'water');
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
    distress,
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

  const activeClass = useMemo(() => {
    if (!thresholds || !boatClassCode) return undefined;
    return thresholds.classes.find((c) => c.code === boatClassCode);
  }, [thresholds, boatClassCode]);
  const seaVisualLoaM = visualLoaM(activeClass);
  const isLandSelection = selection?.surface === 'land';

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
      {researchOpen && <ResearcherWorkspace onClose={() => setResearchOpen(false)} />}

      <FreshnessStrip
        onOpenResearch={() => setResearchOpen(true)}
        freshness={freshness}
        tools={
          <div
            className="border-hairline flex h-7 w-max max-w-full overflow-hidden rounded-md border"
            role="tablist"
            aria-label="Map tools"
          >
            {(
              [
                {
                  id: 'alerts',
                  label: 'Alerts',
                  title: 'Alert centre',
                  Icon: BellRing,
                  tone: alerts.unseen > 0 ? 'text-red' : 'text-amber',
                  active: alertRailOpen,
                  disabled: false,
                  badge: alerts.unseen,
                  toggle: () => {
                    const next = !alertRailOpen;
                    setAlertRailOpen(next);
                    if (next) {
                      alerts.markAllSeen();
                      setTreatmentRailOpen(false);
                      setIntelOpen(false);
                      setSarOpen(false);
                      setRouteOpen(false);
                      setSeaViewOpen(false);
                    }
                  },
                },
                {
                  id: 'look',
                  label: 'Look',
                  title: 'Visual treatments',
                  Icon: Palette,
                  tone: 'text-cyan',
                  active: treatmentRailOpen,
                  disabled: false,
                  badge: 0,
                  toggle: () => {
                    const next = !treatmentRailOpen;
                    setTreatmentRailOpen(next);
                    setIntelOpen(false);
                    setSarOpen(false);
                    setRouteOpen(false);
                    setSeaViewOpen(false);
                    if (next) setAlertRailOpen(false);
                  },
                },
                {
                  id: 'intel',
                  label: 'Intel',
                  title: 'Orbital & vessel intelligence',
                  Icon: Satellite,
                  tone: 'text-cyan',
                  active: intelOpen,
                  disabled: false,
                  badge: 0,
                  toggle: () => {
                    const next = !intelOpen;
                    setIntelOpen(next);
                    setTreatmentRailOpen(false);
                    setSarOpen(false);
                    setRouteOpen(false);
                    setSeaViewOpen(false);
                    if (next) setAlertRailOpen(false);
                  },
                },
                {
                  id: 'sar',
                  label: 'SAR',
                  title: 'Search and rescue drift',
                  Icon: LifeBuoy,
                  tone: 'text-red',
                  active: sarOpen,
                  disabled: false,
                  badge: 0,
                  toggle: () => {
                    const next = !sarOpen;
                    setSarOpen(next);
                    setTreatmentRailOpen(false);
                    setIntelOpen(false);
                    setRouteOpen(false);
                    setSeaViewOpen(false);
                    if (next) setAlertRailOpen(false);
                  },
                },
                {
                  id: 'distress',
                  label: 'Distress',
                  title: 'Distress — person or vessel in the water: call, search plan, transit',
                  Icon: Siren,
                  tone: 'text-red',
                  active: distressOpen,
                  disabled: false,
                  badge: 0,
                  toggle: () => {
                    const next = !distressOpen;
                    setDistressOpen(next);
                    setTreatmentRailOpen(false);
                    setIntelOpen(false);
                    setSarOpen(false);
                    setRouteOpen(false);
                    setSeaViewOpen(false);
                    if (next) setAlertRailOpen(false);
                  },
                },
                {
                  id: 'passage',
                  label: 'Safe passage',
                  title: selection && !isLandSelection
                    ? 'Plan a cleared passage'
                    : 'Pick a sea point first',
                  Icon: Navigation,
                  tone: 'text-cyan',
                  active: routeOpen,
                  disabled: !selection || isLandSelection,
                  badge: 0,
                  toggle: () => {
                    if (!selection || isLandSelection) return;
                    const next = !routeOpen;
                    setRouteOpen(next);
                    setTreatmentRailOpen(false);
                    setIntelOpen(false);
                    setSarOpen(false);
                    setSeaViewOpen(false);
                    if (next) setAlertRailOpen(false);
                  },
                },
                {
                  id: 'sea',
                  label: 'Sea view',
                  title: selection && !isLandSelection
                    ? 'Forecast as the sea it describes'
                    : 'Pick a sea point first',
                  Icon: Waves,
                  tone: 'text-cyan',
                  active: seaViewOpen,
                  disabled: !selection || isLandSelection,
                  badge: 0,
                  toggle: () => {
                    if (!selection || isLandSelection) return;
                    const next = !seaViewOpen;
                    setSeaViewOpen(next);
                    setTreatmentRailOpen(false);
                    setIntelOpen(false);
                    setSarOpen(false);
                    setRouteOpen(false);
                    if (next) setAlertRailOpen(false);
                  },
                },
              ] as const
            ).map((tab, index) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={tab.active}
                title={tab.title}
                disabled={tab.disabled}
                onClick={tab.toggle}
                className={clsx(
                  'relative flex h-full items-center gap-1.5 px-2.5 text-2xs transition-colors',
                  index > 0 && 'border-hairline border-l',
                  tab.disabled && 'cursor-not-allowed opacity-40',
                  tab.active ? 'bg-white/8' : 'hover:bg-white/5',
                )}
              >
                <tab.Icon className={clsx('h-3 w-3', tab.tone)} aria-hidden />
                <span className={clsx('label', tab.active ? 'text-ink-0' : 'text-ink-2')}>
                  {tab.label}
                </span>
                {tab.badge > 0 && (
                  <span className="bg-red/25 text-red data rounded-full px-1 text-2xs leading-none">
                    {tab.badge}
                  </span>
                )}
              </button>
            ))}
          </div>
        }
      />

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
            onViewChange={syncLookOrigin}
            onClick={(lon, lat, surface) => {
              // While picking a destination the click sets the endpoint and does
              // NOT move the selection: re-running the point forecast would throw
              // away the origin the user is planning from.
              if (pickingDestination) {
                if (rejectLandDestination(lon, lat, surface)) return;
                setError(null);
                setRouteDestination({ lat, lon });
                setPickingDestination(false);
                setRoutePlan(null);
                return;
              }
              void query(lon, lat, undefined, surface);
            }}
            className="absolute inset-0"
          />
          {lookOverlay && (
            <div className="pointer-events-none absolute inset-0" style={lookOverlay} />
          )}
        </div>

        {/* ---------------- left rail: place and vessel ---------------- */}
        <div
          className={clsx(
            'pointer-events-none absolute top-3 bottom-3 left-3 z-20 flex w-64 flex-col gap-2 overflow-y-auto transition-transform duration-200',
            !leftRailOpen && '-translate-x-[16.75rem]',
          )}
        >
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
            <LocationSearch
              landmarks={landmarks}
              selectedLabel={selection?.label ?? null}
              onPick={(place) => {
                mapRef.current?.flyTo(place.lon, place.lat, 8);
                void query(place.lon, place.lat, place.name, 'water');
              }}
            />
          </div>

          <div className="glass pointer-events-auto rounded-lg">
            <div className="border-hairline flex items-center gap-1.5 border-b px-3 py-2">
              <Ruler className="text-cyan h-3.5 w-3.5" aria-hidden />
              <span className="label">Your vessel</span>
            </div>
            <div className="p-3">
              <div className="label text-ink-3 mb-1.5 text-2xs">Boat type</div>
              <div className="flex flex-wrap gap-1.5">
                {VESSEL_CHIPS.map((chip) => {
                  const selected = boatClassCode === chip.code;
                  return (
                    <button
                      key={chip.code}
                      type="button"
                      onClick={() => setBoatClassCode(chip.code)}
                      className={clsx(
                        'rounded border px-2 py-1 text-2xs leading-snug transition-colors',
                        selected
                          ? 'border-cyan/50 bg-cyan/12 text-cyan'
                          : 'border-hairline text-ink-2 hover:border-cyan/30 hover:text-ink-0',
                      )}
                      aria-pressed={selected}
                    >
                      {chip.label}
                    </button>
                  );
                })}
              </div>
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
              check={isLandSelection ? null : geofence}
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

        {/* Left rail collapse / expand tab — sits on the right edge of the panel */}
        <button
          type="button"
          onClick={() => setLeftRailOpen((v) => !v)}
          className={clsx(
            'glass text-ink-2 hover:text-cyan pointer-events-auto absolute top-1/2 z-30 flex h-12 w-5 -translate-y-1/2 items-center justify-center rounded-r-lg transition-all duration-200',
            leftRailOpen ? 'left-[16.75rem]' : 'left-0',
          )}
          aria-label={leftRailOpen ? 'Collapse left panel' : 'Expand left panel'}
          title={leftRailOpen ? 'Hide location & vessel panel' : 'Show location & vessel panel'}
        >
          {leftRailOpen ? (
            <ChevronLeft className="h-3 w-3" aria-hidden />
          ) : (
            <ChevronRight className="h-3 w-3" aria-hidden />
          )}
        </button>

        {/* ---------------- centre-left: chat + live trace ---------------- */}
        <div
          className={clsx(
            'glass pointer-events-auto absolute top-3 bottom-3 z-20 w-[23rem] flex-col rounded-lg transition-[left] duration-200',
            chatOpen ? 'flex' : 'hidden',
            leftRailOpen ? 'left-[17.5rem]' : 'left-3',
          )}
        >
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
                boatClassCode,
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
            onClose={() => setChatOpen(false)}
          />
        </div>

        {!chatOpen && (
          <button
            type="button"
            onClick={() => setChatOpen(true)}
            className={clsx(
              'glass border-cyan/40 text-cyan hover:border-cyan/70 hover:bg-cyan/10 pointer-events-auto absolute bottom-4 z-30 flex h-12 w-12 items-center justify-center rounded-full border shadow-[0_0_28px_-8px_rgba(34,211,238,0.7)] transition-[left,color] duration-200',
              leftRailOpen ? 'left-[17.5rem]' : 'left-3',
            )}
            aria-label="Open MitraAI chat"
            title="Open MitraAI chat"
          >
            <Bot className="h-5 w-5" aria-hidden />
            {agentRun.running && (
              <span
                className="bg-jade absolute top-1 right-1 h-2 w-2 animate-pulse rounded-full"
                aria-hidden
              />
            )}
          </button>
        )}

        {/* ------- Safe passage / Sea view panels (opened from the top strip) ------- */}
        {routeOpen && selection && !isLandSelection && (
          <div
            className={clsx(
              'pointer-events-none absolute bottom-3 z-20 w-[22rem] transition-[left] duration-200',
              leftRailOpen ? 'left-[41rem]' : 'left-[24.5rem]',
            )}
          >
            <RoutePanel
              origin={selection}
              destination={routeDestination}
              onPickDestination={() => setPickingDestination((value) => !value)}
              pickingDestination={pickingDestination}
              boatClassCode={boatClassCode}
              loaM={null}
              speedKn={speed}
              plan={routePlan}
              onPlan={setRoutePlan}
              onClose={() => setRouteOpen(false)}
            />
          </div>
        )}

        {seaViewOpen && selection && !isLandSelection && (
          <div className="pointer-events-none absolute right-[25.5rem] bottom-3 z-20 flex flex-col items-end">
            <SeaStatePanel
              forecast={forecast}
              boatClass={activeClass}
              loaM={seaVisualLoaM}
              open
              onToggle={setSeaViewOpen}
            />
          </div>
        )}

        {/* Panels opened from the top strip drop onto the map. */}
        <div
          className={clsx(
            'pointer-events-none absolute top-3 z-40 flex items-start gap-2 transition-[left] duration-200',
            chatOpen
              ? leftRailOpen
                ? 'left-[41rem]'
                : 'left-[24.5rem]'
              : leftRailOpen
                ? 'left-[17.5rem]'
                : 'left-3',
          )}
        >
          {alertRailOpen && (
            <div className="pointer-events-auto">
              <AlertRail
                alerts={alerts.alerts}
                status={alerts.status}
                connected={alerts.connected}
                unseen={alerts.unseen}
                onOpen={alerts.markAllSeen}
                onAcknowledge={(id) => void alerts.acknowledge(id)}
                onCheckNow={alerts.checkNow}
                canWatch={Boolean(selection && !isLandSelection)}
                watching={Boolean(watchId)}
                onWatchToggle={() => {
                  if (watchId) {
                    void alerts.unwatch(watchId);
                    setWatchId(null);
                    return;
                  }
                  if (!selection || isLandSelection) return;
                  void alerts
                    .watch({
                      lat: selection.lat,
                      lon: selection.lon,
                      boatClassCode,
                      label: selection.label,
                    })
                    .then(setWatchId)
                    .catch(() => setWatchId(null));
                }}
                open
                onToggle={(open) => {
                  setAlertRailOpen(open);
                  if (open) {
                    setTreatmentRailOpen(false);
                    setIntelOpen(false);
                    setSarOpen(false);
                    setRouteOpen(false);
                    setSeaViewOpen(false);
                  }
                }}
              />
            </div>
          )}

          {(treatmentRailOpen || intelOpen || sarOpen || distressOpen) && (
            <div className="relative shrink-0">
              {treatmentRailOpen && (
                <div className="absolute top-0 left-0">
                  <TreatmentRail
                    active={treatment}
                    onChange={setTreatment}
                    fps={frame.fps}
                    degradedReason={frame.reason}
                    open
                    onToggle={(open) => {
                      setTreatmentRailOpen(open);
                      if (open) {
                        setAlertRailOpen(false);
                        setIntelOpen(false);
                        setSarOpen(false);
                        setRouteOpen(false);
                        setSeaViewOpen(false);
                      }
                    }}
                  />
                </div>
              )}

              {intelOpen && (
                <div className="absolute top-0 left-0">
                  <OperationalIntelPanel
                    point={isLandSelection ? null : selection}
                    open
                    onToggle={(open) => {
                      setIntelOpen(open);
                      if (open) {
                        setAlertRailOpen(false);
                        setTreatmentRailOpen(false);
                        setSarOpen(false);
                        setRouteOpen(false);
                        setSeaViewOpen(false);
                      }
                    }}
                  />
                </div>
              )}

              {sarOpen && (
                <div className="absolute top-0 left-0">
                  <SarPanel
                    origin={isLandSelection ? null : selection}
                    hours={sarHours}
                    onHours={setSarHours}
                    objectClass={sarClass}
                    onObjectClass={setSarClass}
                    classes={sarClasses}
                    plan={sarPlan}
                    onPlan={(next) => {
                      setSarPlan(next);
                      const ring = next?.areas?.[next.areas.length - 1]?.ring;
                      if (ring?.length) {
                        const lons = ring.map((point) => point[0]);
                        const lats = ring.map((point) => point[1]);
                        const pad = 0.25;
                        mapRef.current?.flyToBox(
                          Math.min(...lons) - pad,
                          Math.min(...lats) - pad,
                          Math.max(...lons) + pad,
                          Math.max(...lats) + pad,
                        );
                      }
                    }}
                    open
                    onToggle={(open) => {
                      setSarOpen(open);
                      if (open) {
                        setAlertRailOpen(false);
                        setTreatmentRailOpen(false);
                        setIntelOpen(false);
                        setRouteOpen(false);
                        setSeaViewOpen(false);
                        setDistressOpen(false);
                      }
                    }}
                  />
                </div>
              )}

              {distressOpen && (
                <div className="absolute top-0 left-0">
                  <DistressPanel
                    origin={isLandSelection ? null : selection}
                    objectClass={sarClass}
                    classes={sarClasses}
                    open
                    onToggle={(open) => {
                      setDistressOpen(open);
                      if (open) {
                        setAlertRailOpen(false);
                        setTreatmentRailOpen(false);
                        setIntelOpen(false);
                        setSarOpen(false);
                        setRouteOpen(false);
                        setSeaViewOpen(false);
                      }
                    }}
                    onResult={(next) => {
                      setDistress(next);
                      // Frame the whole incident: the search box AND the centre
                      // responding to it. Zooming to the box alone hides the one
                      // fact that governs everything — how far away help is.
                      const ring = next?.search_area.containment.at(-1)?.ring;
                      if (ring?.length) {
                        const lons = ring.map((point) => point[0]);
                        const lats = ring.map((point) => point[1]);
                        const centre = next?.notify[0]?.coordinates;
                        if (centre) {
                          lons.push(centre.lon);
                          lats.push(centre.lat);
                        }
                        const pad = 0.2;
                        mapRef.current?.flyToBox(
                          Math.min(...lons) - pad,
                          Math.min(...lats) - pad,
                          Math.max(...lons) + pad,
                          Math.max(...lats) + pad,
                        );
                      }
                    }}
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {/* ---------------- right: verdict + evidence ---------------- */}
        <div className="pointer-events-none absolute top-3 right-3 bottom-3 z-20 flex flex-col items-end gap-2">
          {loading && !risk && (
            <div className="glass pointer-events-auto flex w-[24rem] items-center gap-2 rounded-lg px-4 py-3">
              <Loader2 className="text-cyan h-4 w-4 animate-spin" aria-hidden />
              <span className="text-ink-1 text-xs">
                Fetching live conditions and computing the verdict…
              </span>
            </div>
          )}

          {error && (
            <div className="glass border-red/40 pointer-events-auto w-[24rem] rounded-lg border px-4 py-3">
              <div className="label text-red mb-1">Request failed</div>
              <p className="text-ink-1 text-xs leading-snug">{error}</p>
            </div>
          )}

          {risk &&
            (verdictPanelOpen ? (
              <div className="pointer-events-auto w-[24rem]">
                <VerdictCard
                  result={risk}
                  geofence={geofence}
                  landPoint={isLandSelection}
                  onCollapse={() => setVerdictPanelOpen(false)}
                />
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setVerdictPanelOpen(true)}
                className={clsx(
                  'glass pointer-events-auto flex h-10 w-10 items-center justify-center rounded-lg border transition-colors hover:bg-white/5',
                  risk.verdict === 'NO-GO'
                    ? 'border-red/55 text-red'
                    : risk.verdict === 'CAUTION'
                      ? 'border-amber/50 text-amber'
                      : risk.verdict === 'GO'
                        ? 'border-jade/45 text-jade'
                        : 'border-hairline-strong text-ink-1',
                )}
                aria-label="Expand safety verdict horizontally"
                title={`Open ${risk.verdict} safety verdict`}
              >
                <ChevronLeft className="h-4 w-4" aria-hidden />
              </button>
            ))}

          {forecast &&
            (evidencePanelOpen ? (
              <div className="glass pointer-events-auto min-h-0 w-[24rem] flex-1 overflow-y-auto rounded-lg">
                <div className="glass border-hairline sticky top-0 z-10 flex items-center gap-1.5 border-b px-3 py-2">
                  <Waves className="text-cyan h-3.5 w-3.5" aria-hidden />
                  <span className="label flex-1">Evidence — how ORCA knows</span>
                  {loading && <Loader2 className="text-cyan h-3 w-3 animate-spin" aria-hidden />}
                  <button
                    type="button"
                    onClick={() => setEvidencePanelOpen(false)}
                    className="text-ink-2 hover:bg-abyss-2/70 hover:text-cyan -my-1 -mr-1 rounded p-1.5 transition-colors"
                    aria-label="Collapse evidence panel horizontally"
                    title="Hide this card and expose more of the sea"
                  >
                    <ChevronRight className="h-4 w-4" aria-hidden />
                  </button>
                </div>
                <EvidencePanel forecast={forecast} landPoint={isLandSelection} />
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setEvidencePanelOpen(true)}
                className="glass border-cyan/35 text-cyan pointer-events-auto flex h-10 w-10 items-center justify-center rounded-lg border transition-colors hover:bg-white/5"
                aria-label="Expand evidence panel horizontally"
                title="Open evidence — how ORCA knows"
              >
                <ChevronLeft className="h-4 w-4" aria-hidden />
              </button>
            ))}
        </div>

        {/* ---------------- empty state ---------------- */}
        {!selection && !loading && !error && (
          <div className={clsx('pointer-events-none absolute inset-y-0 right-[25rem] z-10 flex items-center justify-center transition-[left] duration-200', leftRailOpen ? 'left-[41.5rem]' : 'left-[25rem]')}>
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
                ORCA pulls live wave, wind, visibility and convective data for that point, calculates
                a GO / NO-GO assessment, and shows you every number it used — with its source, its
                age and its provenance state.
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
