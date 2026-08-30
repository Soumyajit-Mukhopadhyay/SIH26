# ORCA — master build plan

**Marine EcOsystem Reasoning with Collaborative Agents** · Smart India Hackathon 2026
Derived from `ORCA_SIH2026_Build_Blueprint` (Parts A–N) and `ORCA_Architecture_Chart` (master flow + 10 plates),
reconciled against the *actual* machine, network and credentials as measured on 2026-08-30.

---

## 0. What changed from the blueprint, and why

The blueprint is a good document and I am following its structure, its agent topology, its
deterministic-safety thesis and its explainability contract essentially unchanged. Six things
had to change because the environment does not support them. Each is a deliberate trade, not
a shortcut, and each one is defensible on stage.

| # | Blueprint design | Why it cannot stand | Replacement | Why the replacement is *better* here |
|---|---|---|---|---|
| 1 | **Redis 7** for ARQ queue, `GEOSEARCH` coarse geofence filter, response cache | You asked for no Redis, and there is no container runtime on this machine | In-process `APScheduler` for jobs; a shapely **STRtree** for the coarse spatial filter; a TTL dict + a SQLite `cached_forecasts` table for caching | At ORCA's fence count (~10² polygons: EEZ, IMBL, MPAs, trawl-ban, CRZ) an in-memory STRtree is *faster* than a Redis round-trip plus a PostGIS query. And the blueprint's own risk register lists "Redis wiped on restart, ARQ queue silently dropped" — we designed that failure out |
| 2 | **Postgres 16 + PostGIS + pgvector** as the only store | No Docker (daemon down, C: full) and no Supabase DB password | A **repository interface** with two drivers: `SqliteRepo` (SQLite + shapely, the default, zero infrastructure) and `PostgisRepo` (asyncpg, activated by setting `DATABASE_URL`) | The demo runs on a judge's laptop with `uv run`. The hosted path is a config change, not a rewrite — which is the same argument the blueprint makes about safety thresholds |
| 3 | **COG → Cloudflare R2 → TiTiler** raster pipeline | No R2 credentials, and TiTiler is a second service to keep alive | Derived fields are computed on the AOI grid, colour-mapped **server-side in NumPy**, and written as a single **PNG + JSON sidecar** per variable per timestep, served by our own API and drawn as a deck.gl `BitmapLayer` | The AOI (60–100 °E, 0–25 °N at 0.05°) is only **800 × 500 px**. That is one 300 KB PNG. Tiling a 300 KB image is pure overhead — this is strictly faster, has no tile server to crash, and keeps the server-side `cmocean` colormap argument intact. The COG writer stays in the codebase behind a flag for the scale story |
| 4 | **Twilio WhatsApp / MSG91 SMS / Exotel IVR** fan-out | You asked to skip them | A first-class **Alert Center** in the dashboard: proactive alerts arrive over the same SSE channel, land in a notification tray, raise a map banner, fire a Web Notification, and are spoken aloud via TTS | The *interesting* engineering was never the Twilio call — it was the rules-triggered watcher and the CAP 1.2 record. Both are built. The dispatcher keeps its channel adapters (`push`/`whatsapp`/`sms`/`ivr`/`navic`) as pluggable stubs that log a would-send payload, so the architecture claim stays honest and the roadmap slide is real |
| 5 | **CesiumJS + ion** for the cinematic intro | Your ion token is valid, but Cesium is a ~10 MB dependency, its free tier will not survive judges refreshing, and disk is tight | The cinematic intro is built in **react-three-fiber** with a custom atmosphere/ocean shader over a NASA GIBS texture — no token, no quota, and total shader control. Cesium is kept as a lazy-loaded optional "Photoreal" mode | Removes the blueprint's own listed risk ("Cesium ion free quota burned by repeated demo refreshes") while *increasing* visual control. The token is on file if we add the photoreal mode in polish week |
| 6 | **LangGraph + `langgraph-supervisor`** | `langgraph-supervisor`'s handoff semantics do not give clean control over plan-emission and the critic veto loop, and its Postgres checkpointer needs Postgres | **LangGraph `StateGraph`** (the real framework) with a supervisor node *we* write, and `langgraph.checkpoint.sqlite.SqliteSaver` | Keeps the credible framework answer and `astream_events` for the trace UI, while giving exact control over the two things that are the demo — the emitted plan and the critic bounce |

Everything else — the nine named agents, the deterministic core, the evidence-bound `Advisory`
type, the H3 lookup, the PFZ derivation, the number-integrity guard, CAP 1.2 output, the
freshness contract — is built as specified.

### Environment reality, measured

```
Python      3.14.5 (default) · 3.12.7 available via anaconda  ->  we pin 3.12
Node        22.18.0   npm 10.9.3
git         2.47.1
Docker      installed but daemon NOT running (and C: has no room for WSL)
Disk        C: 182.3 GB used / 0.0 GB free   <-- BLOCKER, see below
            D: 73.7 GB used / 219.3 GB free  <-- project + all caches live here
```

**Disk is the one true blocker.** With C: at zero bytes free, package installs fail mid-unpack
and even command output cannot be written. Run `scripts/00-free-space.ps1` (admin) then
`scripts/01-redirect-caches-to-D.ps1` before Step 1. Target: **≥ 15 GB free on C:**.

---

## 1. The idea we are taking from Akashic — and the line we are not crossing

`CaviraOSS/Akashic` is licensed **AGPL-3.0-or-later**, and its LICENSE additionally carries
adapted *World Monitor* material under the same terms. AGPL is viral **over a network boundary**:
if ORCA contained Akashic code, we would be obliged to publish ORCA's complete corresponding
source to every user of the hosted demo. That is incompatible with a submission we may want to
license permissively.

So: **no file, function, shader, style block or type from that repository enters ORCA.** I read
its `README.md` and its file inventory to identify *concepts* — which are not copyrightable — and
stopped there. Three concepts are worth having, and all three are cheap to write from scratch:

**1. The provenance badge — the single best idea in that repo, and I am extending it.**
Their README states the design principle: *"distinguish live, cached, curated, simulated, and
derived records in the ui."* That is exactly ORCA's unsolved problem. An SST value from a live
CMEMS call, a 6-hour-old cached grid, a hand-loaded EEZ shapefile, a PFZ rank our own code
computed, and a simulated AIS vessel are **five different epistemic categories**, and the
blueprint's design collapses them into one visual treatment.

ORCA gets a **five-state provenance model as a backend type, not a UI decoration**:

| State | Meaning | Example in ORCA |
|---|---|---|
| `LIVE` | fetched from the upstream source during *this* request | Open-Meteo point call on a cache miss |
| `CACHED` | from our store, upstream unchanged, age known and disclosed | the 6-hourly CMEMS SST grid |
| `CURATED` | a static reference dataset we loaded and version-pinned | Marine Regions EEZ v12, WDPA MPAs, port list |
| `DERIVED` | computed by ORCA from other inputs — carries its input lineage | `pfz_rank`, `sst_gradient`, wave steepness, the risk index |
| `SIMULATED` | synthetic, generated for demonstration | the demo fishing fleet where AIS has no Indian-Ocean coverage |

Every `Evidence` object, every layer in the registry, every number in the chat, and every row in
the freshness panel carries one. `SIMULATED` renders with a hatched fill and a hard-edged badge
so it can never be mistaken for measurement. This directly strengthens the PS's explainability
requirement (#6) and it costs an afternoon.

**2. Satellite overpass tracking, re-framed for ISRO.** They ingest TLEs to track satellites.
Reframed, it ties ORCA's data freshness to actual spacecraft: *"the chlorophyll layer you are
looking at is 19 hours old; Oceansat-3 makes its next ocean-colour pass over your fishing area
at 13:42 IST."* Built independently with **satellite.js (MIT)** and public TLE data. Half a day,
and it makes the ISRO connection concrete instead of decorative. (Note: Celestrak is unreachable
from this network — we use `tle.ivanstanojevic.me` as primary. See CREDENTIALS_VERIFIED.md.)

**3. Full-screen visual treatments.** Their README names: standard, CRT, night-vision, thermal,
radar, satcom, noir. These are 20–40 line GLSL fragment shaders — genuinely faster to write than
to extract, and I am writing them from the shader up. One matters more than the rest: a
**thermal** treatment over real SST is *apt* for a marine product rather than decorative. The UI
will be explicit about the distinction between a **data colormap** (thermal ramp applied to real
SST values — meaningful) and a **stylistic treatment** (full-screen post-process — cosmetic),
because blurring those two would undercut the provenance argument we just made.

**What I am deliberately *not* taking:** their connector layer, their risk-scoring and
causal-inference modules, their component architecture, and their Next.js API-route pattern. ORCA
is Python + FastAPI on the backend and Vite + React on the front; there is no shared shape to
borrow even if the licence allowed it.

Permissively-licensed sources we *will* lift code from, per the blueprint's Part I:
`mapbox/webgl-wind` (ISC — particle shaders), `deck.gl` examples (MIT), MapLibre examples (BSD-3),
`satellite.js` (MIT), `searoute-py` (Apache-2.0), `movingpandas` (BSD-3), `pyais` (MIT),
`terra-draw` (MIT), `PMTiles` (BSD-3), `shadcn/ui` (MIT — we own the source).
Explicitly avoided for copyleft: `py-eddy-tracker` (GPL-3.0 — separate CLI step only),
`weather_routing_pi` (GPL-3.0 — algorithm study only), `gladcolor/LLM-Find` (AGPL-3.0).

---

## 2. Repository layout

```
d:\Downloads\SIH26\
├─ README.md                    project front door, quickstart, honest limits
├─ .env                         real secrets (git-ignored)
├─ .env.example                 committed template
├─ .gitignore
├─ scripts/
│   ├─ 00-free-space.ps1
│   ├─ 01-redirect-caches-to-D.ps1
│   └─ dev.ps1                  starts backend + frontend together
├─ docs/
│   ├─ PLAN.md                  this file
│   ├─ CREDENTIALS_VERIFIED.md  live-tested credential + endpoint audit
│   ├─ ARCHITECTURE.md          tier diagram + the deterministic-core argument
│   ├─ DATA_SOURCES.md          every dataset, its provenance state, its cadence
│   ├─ REQUIREMENT_MATRIX.md    PS requirement -> subsystem -> demo evidence (self-scorecard)
│   ├─ DEMO_SCRIPT.md           the 8-minute run of show
│   └─ wireframes/              screen wireframes (published as an Artifact too)
├─ backend/
│   ├─ pyproject.toml
│   ├─ orca/
│   │   ├─ main.py              FastAPI app factory, lifespan, CORS, SSE headers
│   │   ├─ config.py            pydantic-settings; ALL env access goes through here
│   │   ├─ provenance.py        Provenance enum, Freshness, Evidence, Citation  <-- core type
│   │   ├─ schemas/             Advisory, UiSpec, RiskResult, PlanStep, TraceEvent, Boat…
│   │   ├─ db/
│   │   │   ├─ repo.py          the interface every service depends on
│   │   │   ├─ sqlite_repo.py   default driver (aiosqlite + shapely STRtree)
│   │   │   ├─ postgis_repo.py  activated by DATABASE_URL
│   │   │   └─ migrations/      001_core.sql, 002_ops.sql (dialect-tagged)
│   │   ├─ sources/             ONE module per upstream, each returns (value, Provenance, Freshness)
│   │   │   ├─ base.py          retry, timeout, ETag/If-Modified-Since caching, circuit breaker
│   │   │   ├─ open_meteo.py    marine + forecast          [LIVE/CACHED]
│   │   │   ├─ cmems.py         copernicusmarine subset    [CACHED]
│   │   │   ├─ incois_erddap.py griddap: Oceansat-2 OCM, IRS P4 chl, TMI SST, ARGO, ASCAT
│   │   │   ├─ nasa.py          earthaccess: MUR SST, OB.DAAC chl
│   │   │   ├─ gibs.py          WMTS basemap/overlay URL builder (no auth)
│   │   │   ├─ cap_feeds.py     SACHET + IMD CAP RSS -> CAP 1.2 parse -> spatial intersect
│   │   │   ├─ imd.py           REST adapter, dormant until the key + IP whitelist land
│   │   │   ├─ bhuvan.py        WMS adapter, 8 s timeout, graceful UNREACHABLE
│   │   │   ├─ ais.py           AISStream WS + the simulated fleet [LIVE | SIMULATED]
│   │   │   ├─ gfw.py           Global Fishing Watch fishing-effort validation
│   │   │   ├─ usgs.py          quakes -> tsunami context
│   │   │   ├─ tle.py           TLE fetch + cache (ivanstanojevic primary, Celestrak secondary)
│   │   │   └─ marine_regions.py  EEZ/IMBL via geo.vliz.be WFS  [CURATED]
│   │   ├─ science/             pure NumPy/xarray — no I/O, fully unit-testable
│   │   │   ├─ grid.py          the AOI grid, ONE definition, imported everywhere
│   │   │   ├─ fronts.py        Sobel-P90 + Canny + Cayula-Cornillon SIED
│   │   │   ├─ pfz.py           rank 1/2/3 rule + Ekman advection + polygonise
│   │   │   ├─ anomaly.py       climatology anomaly + Hobday marine-heatwave flag
│   │   │   ├─ derive.py        wave steepness, upwelling proxy, agreement score
│   │   │   ├─ colormap.py      cmocean-equivalent ramps -> RGBA PNG + JSON sidecar
│   │   │   └─ h3grid.py        grid <-> H3 res-6, the single H3_RESOLUTION constant
│   │   ├─ services/            the DETERMINISTIC CORE — no LLM below this line
│   │   │   ├─ risk_engine.py   GO/CAUTION/NO-GO + index + vetoes + thresholds_version
│   │   │   ├─ geofence.py      STRtree coarse -> exact shapely/PostGIS -> time-to-cross
│   │   │   ├─ router.py        land-masked A* + per-leg cost rationale
│   │   │   ├─ drift.py         Monte-Carlo SAR particle drift -> probability field
│   │   │   ├─ pfz_lookup.py    H3 ring search -> nearest PFZ as distance + bearing
│   │   │   ├─ overpass.py      satellite.js-equivalent SGP4 -> next pass over an AOI
│   │   │   ├─ cap_builder.py   CAP 1.2 XML
│   │   │   └─ agreement.py     multi-source agreement badge
│   │   ├─ agents/
│   │   │   ├─ state.py         OrcaState (messages, locale, boat, plan, evidence[], verdict)
│   │   │   ├─ graph.py         LangGraph StateGraph + SqliteSaver + our supervisor node
│   │   │   ├─ nodes/           interaction, language, planner, data_discovery, weather,
│   │   │   │                   ocean, geospatial, risk, routing, visualisation,
│   │   │   │                   reporting, critic
│   │   │   ├─ tools/           typed tool functions, each with a machine-readable capability
│   │   │   │                   descriptor so the planner *selects* rather than hardcodes
│   │   │   ├─ prompts.py       one place, versioned
│   │   │   └─ llm.py           provider router: Groq -> Gemini -> OpenRouter -> Ollama
│   │   ├─ language/
│   │   │   ├─ detect.py        script + fastText-style language ID (9 coastal languages)
│   │   │   ├─ translate.py     Sarvam primary, Gemini fallback, AI4Bharat offline path
│   │   │   ├─ speech.py        ASR (Groq whisper-large-v3) + TTS (Sarvam Bulbul)
│   │   │   └─ guard.py         number-integrity guard: mask -> translate -> re-inject
│   │   ├─ api/routes/          agent_stream, forecast, pfz, risk, route, geofence, trip,
│   │   │                       alerts, advisories, catch_report, rasters, overpass,
│   │   │                       freshness, health, dashboard
│   │   └─ jobs/
│   │       ├─ scheduler.py     APScheduler: 1 h waves/wind · 6 h SST/chl · 15 min alerts
│   │       ├─ ingest.py        pull -> clip -> derive -> PNG + sidecar -> catalogue row
│   │       └─ trip_monitor.py  the PROACTIVE ALERT RAIL — rules-triggered, unprompted
│   ├─ data/
│   │   ├─ static/              EEZ, IMBL, MPA, ports, coastline, boat_classes seed
│   │   ├─ rasters/             generated PNG + sidecars
│   │   └─ orca.db              SQLite
│   └─ tests/                   pytest — every deterministic service has tests
└─ frontend/
    ├─ package.json · vite.config.ts · tailwind.config
    └─ src/                     (see §6)
```

---

## 3. Backend design

### 3.1 The contract every value obeys

Nothing reaches the agent layer as a bare number. The single most important type in the codebase:

```python
class Provenance(StrEnum):
    LIVE = "live"; CACHED = "cached"; CURATED = "curated"
    DERIVED = "derived"; SIMULATED = "simulated"

class Freshness(BaseModel):
    valid_time: datetime          # what instant the value describes
    retrieved_at: datetime        # when we got it
    age_hours: float
    is_stale: bool                # age > per-variable STALENESS threshold
    stale_after: datetime
    note: str | None              # human sentence, surfaced *before* the value when stale

class Evidence(BaseModel):
    dataset_id: str               # cmems_mod_glo_wav_anfc_0.083deg_PT3H-i
    provider: str                 # CMEMS | INCOIS | IMD | NDMA | NASA | Open-Meteo | ORCA
    variable: str
    value: float | str | None
    unit: str | None
    provenance: Provenance        # <-- the five-state badge
    freshness: Freshness
    lineage: list[str] = []       # for DERIVED: the dataset_ids it was computed from
    url: str | None
```

`Advisory.evidence: list[Evidence] = Field(min_length=1)` means the structured-output validator
**rejects an uncited answer and retries**. That is a type-level guarantee, not a prompt request,
and it is a thirty-second explanation on stage.

### 3.2 Persistence

Schema follows the blueprint's D.6 with dialect-tagged DDL so the same migrations run on SQLite
and PostGIS: `users`, `boat_classes` (versioned, with `source_citation`), `boats`, `ports`,
`eez_zones`, `imbl_boundaries`, `marine_protected_areas`, `geofences`, `trips`, `positions`,
`geofence_events`, `cached_forecasts`, `advisories`, `alerts`, `agent_runs`, `agent_steps`,
`document_chunks`, `catch_reports`.

SQLite differences, handled in `sqlite_repo.py`:
- geometry stored as WKB blobs + a bbox column; spatial predicates run in **shapely**, with an
  **STRtree** built at startup over the fence set (the Redis-`GEOSEARCH` replacement)
- `positions` uses a plain index rather than range partitioning
- vector search uses NumPy cosine over the (small) chunk set instead of pgvector HNSW
- **the metre-vs-degree footgun is designed out**: all distances go through one
  `geodesic_m()` helper using pyproj `Geod`. The blueprint warns every team hits `ST_Distance`
  returning degrees; there is a unit test asserting a known 1 km pair.

### 3.3 API surface

Blueprint D.7 as specified, plus what the new decisions require:

```
POST /agent/stream            SSE: plan · step · tool_call · tool_result · token
                                   · ui_spec · alert · final     (X-Accel-Buffering: no, no gzip)
GET  /agent/runs/{id}         full replayable trace  -> the "how we got this" panel
GET  /forecast/point          all variables at lat/lon over a window, each with Evidence
GET  /pfz/zones               rank polygons for bbox+date, with rationale
GET  /pfz/nearest             H3 ring search, answered as distance + bearing from a landmark
POST /risk/assess             deterministic. index, verdict, vetoes, components. no LLM
POST /route/plan              GeoJSON + per-leg rationale + why the direct line was rejected
POST /geofence/check          containment, distance-to-boundary, projected time-to-cross
POST /drift/search-area       SAR Monte-Carlo particle drift -> probability field
POST /trip                    file/update/close a trip plan; arms the proactive monitor
GET  /alerts/stream           SSE alert rail (the dashboard's Alert Center)
GET  /advisories/{id}/cap     CAP 1.2 XML
POST /catch-report            community feedback that corrects the PFZ layer
GET  /rasters/{var}/{t}.png   colour-mapped field   + /rasters/{var}/{t}.json sidecar
GET  /overpass                next Oceansat-3 / INSAT-3D / Sentinel-3 pass over an AOI
GET  /freshness               per-source last-success timestamps  -> put this on a demo screen
GET  /healthz
POST /language/{detect,translate,asr,tts}
GET  /dashboard/authority     fleet effort, MPA compliance, AIS-gap flags
```

### 3.4 Agent plane

Twelve nodes in a LangGraph `StateGraph`. The nine the PS names verbatim, plus interaction,
language and the critic:

```
interaction → language → planner ⇄ { data_discovery, weather, ocean_analytics,
                                     geospatial, risk, routing }
                            ↓
                     visualisation → reporting → critic ─approve→ END
                                                       └─revise→ planner
```

- The **planner emits an explicit plan object before executing anything**, and the UI renders it.
  That is the visible difference between an agent and a chatbot, and it is scored.
- Routing is a planner **decision** (`Command(goto=...)`), never an `if/else` in the graph.
- Tools carry machine-readable capability descriptors (resolution, latency, coverage, cost) so
  "autonomously discover and retrieve" is real tool *selection*, and two different questions
  visibly pull different tool sets.
- `SqliteSaver` keyed by `thread_id` gives multi-turn refinement ("and further south?") across a
  page reload.
- `astream_events(version="v2")` is translated into our SSE event schema; every node and tool
  call is also written to `agent_steps` and mirrored to **Langfuse (US host)**.
- **The critic can veto.** It re-reads the draft against the rule-engine verdict, the thresholds
  version and the cited evidence, and returns approve/revise/escalate. We will make it fire once,
  on purpose, in the demo.
- LLM router with real fallback: Groq `openai/gpt-oss-120b` → Gemini `2.5-flash` →
  OpenRouter → local Ollama. Per-session rate limiting so a judge hammering chat cannot burn the
  daily quota.

### 3.5 The deterministic core

Exactly as the blueprint specifies, and the line is drawn in the code, not just the diagram:
`orca/services/` imports nothing from `orca/agents/`, and there is a test asserting that.

- `risk_engine.assess()` is a pure function: weighted index (0.35 wave / 0.30 wind /
  0.15 visibility / 0.20 lightning), **hard vetoes that override the blend**, `boat_classes` row,
  `data_age_hours`, `confidence`, `escalate`, `thresholds_version`.
- Thresholds are seeded from the peer-reviewed small-craft literature the blueprint cites
  (Hs ≥ 1.0 m for < 10 m LOA; Hs ≥ 2.0 m for < 24 m LOA; J. Mar. Sci. Eng. 11(7):1302), each row
  carrying its `source_citation`, because INCOIS's real SVAS thresholds are not published.
- The regression test is the blueprint's own worked example: 8.2 m boat, Hs 2.4 m, wind 26 kn,
  visibility 6 km, lightning 35 % → **index 25/100, two vetoes, NO-GO**.
- The LLM's system prompt forbids issuing or softening a verdict; `verdict_source` is typed
  `Literal["rule_engine"]` so `"llm"` is unrepresentable.

---

## 4. The science, concretely

**AOI**: 60–100 °E, 0–25 °N (the Indian EEZ envelope) at 0.05° → 800 × 500. One definition in
`science/grid.py`. **H3_RESOLUTION = 6**, one constant — the blueprint flags a resolution
mismatch between ingest and query as a silent-empty-map bug.

**PFZ derivation** (the blueprint's central technical finding: INCOIS publishes PFZ as PDF maps,
not an API, so we reimplement the published methodology and validate *against* the bulletins):

1. Thermal front mask — Sobel gradient magnitude, 90th-percentile threshold **for the MVP**; true
   **Cayula–Cornillon SIED (1992)** implemented behind a flag. The UI states which one is running.
   Judges reward the distinction and punish blurring it.
2. Chlorophyll front mask — **Canny**, σ = 2.0, the detector INCOIS actually uses.
3. Productivity cut — Chl-a > 0.3 mg m⁻³.
4. Eddy mask — from SSHA; `py-eddy-tracker` (GPL-3.0) only ever as an **offline CLI step**, never
   an import.
5. Rank: 1 = front alone · 2 = front + eddy **or** Chl > 0.3 · 3 = front + eddy + Chl > 0.3.
6. Advect forward on Ekman-derived surface currents → reported as direction + distance.
7. Polygonise → H3 res-6 → `/pfz/zones`, `/pfz/nearest`.

Result is tagged `DERIVED` with `lineage = [sst_dataset_id, chl_dataset_id, ssh_dataset_id]`.

**Causal attribution** (differentiator #1, the query nobody else answers well): SST anomaly vs
climatology, chlorophyll trend, **Hobday percentile marine-heatwave flag**, upwelling proxy from
wind stress, ENSO/IOD phase — composed as *evidence-weighted attribution*, and labelled as
attribution rather than proven causality. Honesty here is the differentiator.

**Route explanation** is the deliverable, not the polyline: *"the direct line is 4 km shorter but
crosses a band forecast at 2.4 m against your 1.5 m class limit — routing around it costs
22 minutes."* Per-leg km / wave / current-assist written to `agent_steps.output` so the reporting
agent quotes it verbatim.

**Geofence**: STRtree coarse filter → exact shapely → **state machine** (approaching → crossed →
inside → exited) so events are written on *transitions*, not every tick → time-to-cross on the
current heading. The Palk Bay line is the demo: *"on your current heading you cross into Sri
Lankan waters in 38 minutes (11.4 km)."*

---

## 5. Language plane

Detect → translate to canonical English → reason → translate back → **number guard** → TTS.
Nine coastal languages: Gujarati, Marathi, Konkani, Kannada, Malayalam, Tamil, Telugu, Odia,
Bengali.

- Detect: script-range heuristic + romanised-input handling.
- Translate: **Sarvam** primary (verified working, and it preserved "2.4 metres" → "2.4 மீட்டர்"),
  Gemini fallback, self-hosted AI4Bharat IndicTrans2 as the offline path.
- ASR: **Groq `whisper-large-v3`** (verified available) primary, Sarvam Saaras fallback.
- TTS: Sarvam Bulbul.
- **The number guard is the part that matters in a safety system.** Regex-compare numerals
  source vs target; on mismatch, mask the numbers, re-translate, re-inject verbatim. A garbled
  "2.5 metre" is not a cosmetic defect.
- **Konkani TTS is a real gap** (absent from both IndicF5 and Bulbul). We state it and fall back
  to Konkani text + a Marathi-adjacent voice. Knowing your own gap beats claiming coverage you
  cannot demo.

---

## 6. Frontend design

### 6.1 Design language — "ORCA Deep"

An instrument panel, not a website. Rules held without exception:

```
--abyss-0   #04090f     page ground
--abyss-1   #070f18     panel ground
--abyss-2   #0b1725     raised panel
--hairline  rgba(255,255,255,.07)      1px borders only
--cyan      #22d3ee     the single accent — interactive + PFZ + currents
--amber     #f59e0b     WARNING ONLY. never decorative
--red       #ef4444     CRITICAL / NO-GO / veto
--jade      #34d399     GO / healthy / fresh
--violet    #a78bfa     DERIVED provenance
```

- **Monospace for data, sans for prose.** JetBrains Mono for coordinates, SST, Hs, speed,
  bearings, timestamps; Inter for chat and labels. That contrast alone reads as "instrument".
- **Glass sparingly** — 12 px backdrop blur, 1px hairline, only on panels floating over an
  edge-to-edge unframed map. Glass everywhere reads cheap.
- **Colour-code by data type and never deviate**: SST always thermal blue→red, chlorophyll always
  the algae ramp, currents always the cyan particle trail, cyclones always warm red arcs. Judges
  learn the code in ten seconds.
- **Never colour alone as the carrier of meaning** — every colour pairs with an icon and a spoken
  word. Colour-blind-safe ramps throughout (accessibility is a scored PS bullet).
- **Motion with purpose**: when the agent answers, the corresponding layer animates in *from the
  affected bounding box*, so judges see the causal link between what the agent said and what the
  map did.
- **Frame on the real thing**: default camera on the Arabian Sea / Bay of Bengal inside India's
  EEZ, PFZ polygons echoing INCOIS's own styling conventions.

### 6.2 Screens

| Screen | Content |
|---|---|
| **Cinematic boot** (once) | r3f: earth from orbit on a GIBS texture, custom atmosphere + rim-light shader, a 6 s scripted descent into the Bay of Bengal, settling on Kasimedu; cross-fades into the operational canvas |
| **Operational console** (home) | Full-bleed MapLibre v5 **globe** + deck.gl overlay. Left: layer rail with provenance badges + live counts. Right: chat + agent-trace timeline. Bottom: time slider + charts drawer. Top: freshness strip, next-overpass chip, alert bell. Floating: verdict card + evidence panel |
| **Sea-state view** | r3f ocean driven by *real* Hs/Tp/direction — the waves you see are the forecast you were quoted. Wave-height ruler against the boat's class limit |
| **SAR drift mode** | last-known position → animated Monte-Carlo particle cloud → search-probability heatmap + a suggested search box |
| **Trip planner / digital twin** | route, fuel burn, weather window, return-time margin, PFZ target, the go/no-go card, "file this trip" → arms the proactive monitor |
| **Authority dashboard** | fleet effort, MPA/CRZ compliance, AIS-gap flags, advisory reach — the second user the PS names |
| **Trace replay** | replayable `agent_runs`/`agent_steps` timeline; read *why* the critic overrode the draft |
| **Offline mode** | cached advisory + local rule engine + an explicit "offline — last verified advisory 41 min ago" banner. A real code path, not a video |

### 6.3 Layer registry

The visualisation agent emits a `ui_spec`; a registry turns it into concrete GPU layers. Every
layer declares `provenance`, `validTime`, `sourceId`, `colormap`.

| Layer | Implementation |
|---|---|
| SST / chlorophyll / wave-height / PFZ-rank rasters | deck.gl `BitmapLayer` over our server-coloured PNG + sidecar |
| Ocean currents / wind | GPU particle system, shaders written from `mapbox/webgl-wind` (ISC), reading a u/v PNG (R = u, G = v) + JSON sidecar |
| PFZ zones / MPA / EEZ / IMBL / CRZ | `PolygonLayer` / `PathLayer` from our GeoJSON; rank-shaded fill |
| Vessels + tracks | `TripsLayer` animated trails + `IconLayer`; hatched styling for `SIMULATED` |
| Cyclone track + cone | `PathLayer` + `PolygonLayer` |
| Route + rejected alternative | `PathLayer` ×2, the rejected one dashed with its rejection reason on hover |
| Drift particles | `ScatterplotLayer` + `HeatmapLayer` |
| Geofence drawing | `terra-draw` (MIT) |
| Satellite ground tracks + swath | `PathLayer` + `PolygonLayer` from SGP4 propagation |
| Basemap | OpenFreeMap dark (verified 200); **PMTiles archive pre-baked** for demo-day Wi-Fi insurance |

### 6.4 Visual treatments (the "crazy" layer, done honestly)

Full-screen GLSL post-process over the composited map, written from scratch, at 0.75× internal
resolution with a CSS-filter fallback and an FPS guard:

`standard` · `thermal` (luminance → thermal ramp) · `night-vision` (green gain + grain +
vignette) · `radar` (rotating sweep, range rings, phosphor persistence) · `bathymetric`
(depth-banded contours from GEBCO) · `CRT` (scanlines, barrel distortion, chromatic aberration) ·
`noir`.

**The UI labels these `treatment: stylistic` and holds them visually separate from
`colormap: data`.** A thermal ramp applied to real SST values is a measurement; a thermal
post-process over the whole screen is decoration. Conflating them after building a five-state
provenance system would be self-defeating.

### 6.5 Frontend stack

React 19 · TypeScript · Vite 7 · Tailwind v4 · shadcn/ui (we own the source) ·
MapLibre GL v5 + `@deck.gl/{core,layers,geo-layers,mapbox}` · react-three-fiber + drei +
postprocessing · Recharts (+ ECharts for heavy time series) · `motion` (import from `motion/react`)
· `@turf/turf` · terra-draw · PMTiles · satellite.js · Lucide icons.
Cesium + Resium remain a **lazy optional** photoreal mode. Leaflet fallback behind a WebGL2
capability probe.

---

## 7. Build steps

Each step ends with a **test that must pass** and a **git commit**. No step starts before the
previous one's test passes.

| # | Step | Ships | Test that must pass | Commit |
|---|---|---|---|---|
| **0** | **Free disk, pin toolchain** | `scripts/00`, `scripts/01` run; ≥15 GB on C:; Python 3.12 venv on D:; core wheels installed | `python -c "import numpy,scipy,xarray,shapely,pyproj,h3,skimage,fastapi"` exits 0 | `chore: toolchain + disk remediation` |
| **1** | **Repo + docs + skeleton** | git init, `.gitignore`, `.env`/`.env.example`, `config.py`, `provenance.py`, FastAPI app, `/healthz`, `/freshness` | server boots; `/healthz` 200; every credential in `.env` loads and is masked in logs | `feat: project skeleton + provenance model` |
| **2** | **Source adapters** | Open-Meteo, INCOIS ERDDAP, CAP feeds, USGS, GIBS, TLE, Marine Regions WFS + base client (retry/timeout/circuit-breaker/HTTP cache) | `pytest tests/test_sources.py` — real waves off Chennai with a correct `Freshness`; every return carries a `Provenance` | `feat: upstream source adapters with provenance` |
| **3** | **Persistence + static vectors** | migrations, `SqliteRepo`, `PostgisRepo`, STRtree, `geodesic_m()`, loaded EEZ/IMBL/MPA/ports/coastline/boat_classes | `pytest tests/test_geo.py` — the known-1 km pair asserts metres; point-in-EEZ correct | `feat: persistence layer + curated reference geography` |
| **4** | **Ocean science + rasters** | AOI grid, fronts (Sobel + Canny + SIED), PFZ rank, anomaly + MHW, colormaps → PNG + sidecar, H3 | `pytest tests/test_science.py`; `/rasters/sst/latest.png` renders; PFZ rank field has all three ranks | `feat: PFZ engine, front detection, raster pipeline` |
| **5** | **Deterministic core I — risk** | `risk_engine`, `boat_classes` with citations, `/risk/assess` | the blueprint's worked case returns **index 25, NO-GO, 2 vetoes**; a test asserts `services/` never imports `agents/` | `feat: deterministic risk engine` |
| **6** | **Deterministic core II** | geofence + state machine + time-to-cross, A* router + rationale, SAR drift, CAP 1.2, nearest-PFZ, overpass | Palk Bay heading → "cross in N minutes"; router rejects the high-wave band **with a reason**; CAP XML validates | `feat: geofence, routing, drift, CAP 1.2` |
| **7** | **Agent graph** | `OrcaState`, 12 nodes, typed tools + capability descriptors, `Advisory` with mandatory evidence, `SqliteSaver`, LLM router | all **8 PS sample queries** answer end-to-end in English, each with ≥1 citation; two different questions pull different tool sets | `feat: LangGraph supervisor with 9 specialists + critic` |
| **8** | **Streaming + trace** | `/agent/stream` SSE, `agent_runs`/`agent_steps`, Langfuse (US host), `/agent/runs/{id}` | `curl -N` shows `plan` → `step` → `tool_call` → `token` → `ui_spec` → `final` incrementally, **not in one lump** | `feat: SSE agent streaming + replayable traces` |
| **9** | **Language plane** | detect, translate, ASR, TTS, number guard | Tamil in → Tamil out with **every numeral intact**; guard unit test catches an injected mangle | `feat: Indic language plane + number-integrity guard` |
| **10** | **Frontend foundation + wireframes** | design tokens, shell, routing, API client, SSE hook, shadcn base; wireframes doc + Artifact | `npm run build` clean; wireframes published | `feat: frontend design system + shell` |
| **11** | **Map console** | MapLibre v5 globe, deck.gl overlay, layer registry, **provenance badges**, layer rail, time slider, legend | toggling layers renders real rasters; every layer shows a correct provenance badge | `feat: operational globe + layer registry` |
| **12** | **Chat + trace + advisory** | chat panel, live agent-trace timeline, verdict card, evidence panel, charts, chat↔map bridge | typing a question visibly changes the map; the "why/sources" panel lists dataset + timestamp | `feat: conversational console with live agent trace` |
| **13** | **3D + treatments** | r3f cinematic intro, sea-state ocean driven by real Hs/Tp, current/wind particles, the 7 GLSL treatments | 60 fps at 1080p on integrated graphics with treatments off; FPS guard degrades gracefully | `feat: cinematic 3D, ocean scene, visual treatments` |
| **14** | **Proactive rail + extra modes** | trip monitor job, Alert Center, SAR mode, trip planner, authority dashboard, overpass panel, offline degradation | an **unprompted** alert reaches the dashboard mid-session; killing the network shows the staleness banner and the local verdict still computes | `feat: proactive alerts, SAR, authority dashboard, offline mode` |
| **15** | **Harden + demo** | requirement-matrix self-score, eval set, seed data, demo script, README, canned fallbacks, rate limits | every **P0** in `REQUIREMENT_MATRIX.md` marked demonstrable; full 8-min run without a code change | `docs: demo script, eval set, requirement scorecard` |

Steps 1–9 are backend and independently testable with `curl`/`pytest`. Steps 10–14 are frontend
against a working API. Step 15 is the rehearsal pass.

---

## 8. Honest limits — stated up front, in the product

Copied into the README and the advisory card, because the blueprint is right that volunteering
these reads as engineering maturity and getting caught without them reads as reckless:

1. **ORCA supplements, never replaces, official IMD and INCOIS bulletins.**
2. **The LLM never issues a safety verdict.** A deterministic, versioned rule engine does, and
   the critic checks the explanation against it.
3. **PFZ has no public API.** We derive it from the published INCOIS methodology and validate
   against the official bulletins. The demo states which front detector is running.
4. **INCOIS's SVAS thresholds are not published.** Ours are seeded from peer-reviewed small-craft
   literature, versioned, cited per row, and swap in as a config change.
5. **Lightning has no free authoritative Indian source.** GOES/GLM does not cover India. We use
   forecast lightning probability and say so.
6. **AIS coverage over the Indian Ocean is sparse** on the free tier. Real vessels render `LIVE`;
   the demo fleet renders `SIMULATED`, hatched, and never mixed silently.
7. **Konkani has no TTS** in either IndicF5 or Bulbul.
8. ORCA is safety-of-life-**adjacent** decision support, not a certified marine safety system.
   Real deployment needs validation against historical incident data and sign-off from a maritime
   safety authority.

---

## 9. Open questions — none of them blocking

1. **Supabase database password** → lights up the hosted PostGIS/pgvector path. Zero code change.
2. **MOSDAC + IMD API** approvals → adapters are written and dormant; they activate on key arrival.
3. **Bhashini `userID`/`ulcaApiKey`** → additive third language stack.
4. **Buy \$10 of OpenRouter credits** → 50 → 1 000 req/day. The only spend, and it protects Q&A.

I am proceeding on the assumption that all four remain unavailable, so nothing in the build waits
on them.
