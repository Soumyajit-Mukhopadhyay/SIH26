# ORCA

**Marine EcOsystem Reasoning with Collaborative Agents** — Smart India Hackathon 2026

Agentic marine decision support for the Indian EEZ: a fisherman asks a question in their own
language, and ORCA discovers the right datasets, reasons over them with a team of specialist
agents, computes a safety verdict with a **deterministic rule engine**, and answers with every
number carrying its source, its provenance state and its age.

- `docs/PLAN.md` — the master build plan (architecture, science, 16 build steps)
- `docs/REQUIREMENT_MATRIX.md` — every claim, its state, and **how to check it**
- `docs/DEMO_SCRIPT.md` — the eight-minute run, live, without a code change
- `docs/CREDENTIALS_VERIFIED.md` — every credential and endpoint, live-tested
- `ORCA_SIH2026_Build_Blueprint.pdf` — the originating blueprint

---

## What it does

Ten things, each of which can be checked on a running system — see
`docs/REQUIREMENT_MATRIX.md` for the check per row.

**Decide.** A `GO / CAUTION / NO-GO / UNVERIFIABLE` for a position and a vessel
class, from a deterministic rule engine with cited, versioned thresholds. Hard
vetoes are a separate mechanism from the blended index, so no amount of good
weather elsewhere can outvote an exceeded limit.

**Refuse.** `UNVERIFIABLE` is a first-class verdict with its own card, because a
system that renders missing data as calm water is worse than no system.

**Attribute.** Every value carries its dataset, provider, age and one of six
provenance states. Lead time is distinguished from staleness: a forecast valid at
06:00 tomorrow is not stale data.

**Explain.** A LangGraph supervisor publishes its plan before it runs anything,
streams each tool call with its measured latency, and passes the draft to a critic
that can reject it for disagreeing with the rule engine — visibly, with the round
count on screen.

**Speak.** Eleven Indian languages in and out, by voice. Every numeral, unit and
verdict term is masked, re-injected verbatim and verified as a multiset, per
clause, and a clause that cannot be verified stays in the source language rather
than being translated unsafely.

**Route.** A* over a lattice costed by the same rule engine, where a vetoed cell
is impassable rather than expensive. When no passage exists, the blocking cells
are named with their reasons.

**Search.** SAR drift as a 2000-particle Monte Carlo (IAMSAR `current +
leeway(wind)`) returning 50% and 95% containment areas — never a single predicted
position.

**Warn.** A trip monitor that speaks without being asked, on transitions only,
carrying the previous value beside the current one.

**Show.** A MapLibre globe with deck.gl: SST, chlorophyll, thermal fronts, derived
PFZ, wind and current particle advection, EEZ and IMBL treaty lines, geofence
transitions. Plus a 3D sea state driven by the real Hs, period and direction, and
seven visual treatments behind a frame-rate guard that actually gives something
up.

**Hand off.** CAP 1.2 XML, bilingual, in the OASIS standard NDMA SACHET and IMD
already publish in — built to feed the systems that exist rather than replace
them.

---

## Quickstart

```powershell
# 1. toolchain (once)
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e ".\backend[dev]"

# 2. secrets
Copy-Item .env.example .env    # then fill it in

# 3. run
.\scripts\dev.ps1              # backend at http://127.0.0.1:8000/docs
.\scripts\dev.ps1 -Test        # ruff + pytest
.\scripts\dev.ps1 -NoInfra     # force the zero-infrastructure path
```

Then look at **`/healthz`** and **`/freshness`** — they report exactly which drivers, credentials
and upstream sources are live, rather than a green tick that only means "the process is running".

## ORCA runs with no infrastructure

The default persistence driver is **SQLite + shapely STRtree**, with an in-process TTL cache and
APScheduler. No Docker, no Postgres, no Redis, no cloud account. That property is deliberate: the
demo has to survive a judge's laptop.

Bring the optional accelerators up and ORCA upgrades itself transparently:

```powershell
docker compose up -d    # Postgres 16 + PostGIS 3.4 on :5433, Redis 7 on :6380
```

| Driver | Without infrastructure | With it |
|---|---|---|
| Persistence | SQLite + shapely | PostGIS (`DATABASE_URL`) + pgvector |
| Spatial index | shapely `STRtree` | PostGIS GiST |
| Cache | in-process TTL dict | Redis |
| Job queue | APScheduler (in-process) | ARQ |
| Vector search | NumPy cosine | pgvector HNSW |

Startup probes each one and records what it actually got in `app.state.infra`, so `/healthz`
tells you the truth. A dead accelerator is a logged fallback, never an outage — and there are
tests asserting that.

## Honest limits

Stated here, in the product, and on the advisory card — because volunteering them reads as
engineering maturity and getting caught without them reads as reckless.

1. **ORCA supplements, never replaces, official IMD and INCOIS bulletins.**
2. **The LLM never issues a safety verdict.** A deterministic, versioned rule engine does, and a
   critic agent checks the explanation against it. `verdict_source` is typed
   `Literal["rule_engine"]`, so `"llm"` is unrepresentable.
3. **PFZ has no public API.** We derive it from the published INCOIS methodology and validate
   against the official bulletins. The UI states which front detector is running.
4. **INCOIS's SVAS thresholds are not published.** Ours are seeded from peer-reviewed small-craft
   literature, versioned, cited per row, and swap in as a config change.
5. **Lightning has no free authoritative Indian source.** GOES/GLM does not cover India. We use
   forecast lightning probability and say so.
6. **AIS coverage over the Indian Ocean is sparse** on the free tier. A zero-vessel live snapshot
   is labelled as coverage-limited, never interpreted as empty water. ORCA does not fabricate a
   fallback fleet.
7. **A predicted satellite overpass is only a nominal swath opportunity.** It does not prove that
   an acquisition was tasked, completed, cloud-free, or delivered as a usable product.
8. **Konkani has no TTS** in either IndicF5 or Bulbul.
9. **The lightning veto is CAPE-derived, and CAPE is potential rather than occurrence.** During
   the south-west monsoon large stretches of the Indian coast carry 2000–4500 J/kg, which maps
   above the 60% veto and makes ORCA refuse almost everything there. The honest reading of a
   lightning NO-GO is "thunderstorm potential is high across this whole area", not "a storm is
   over your boat". A convective-inhibition gate would sharpen it; it is not implemented.
10. **Routing coarsens its lattice under the upstream call budget, and a coarser lattice is a MORE
   conservative router** — one vetoed 60 km cell blocks a corridor a boat might thread. The
   response says so when it happens.
11. **Traces do not survive a restart.** `/agent/runs/{id}` replays from an in-process ring
    buffer; the `agent_runs`/`agent_steps` tables are not wired.
12. ORCA is safety-of-life-**adjacent** decision support, not a certified marine safety system.
    Real deployment needs validation against historical incident data and sign-off from a maritime
    safety authority.

## The provenance contract

`backend/orca/provenance.py` is the most important file in the codebase. Nothing reaches the agent
layer as a bare number:

```python
Evidence(
    dataset_id="incois_tmi_3day_datasets",
    provider=Provider.INCOIS,
    variable="sst",
    value=29.1, unit="degC",
    provenance=Provenance.CACHED,     # live | cached | curated | derived | simulated | unavailable
    freshness=Freshness.of("sst", valid_time),   # per-variable staleness policy
)
```

Three invariants are enforced by the type, not by a prompt:

- `Advisory.evidence` has `min_length=1` — **an uncited answer fails validation and retries.**
- `DERIVED` evidence must declare its `lineage`; a computed value with no named inputs is not
  auditable, so the PFZ layer cannot be magical.
- `SIMULATED` is excluded from `DECISION_GRADE` — a GO/NO-GO can never rest on invented data.

## Layout

```
backend/orca/
  provenance.py   the contract every value obeys
  config.py       every env var, as SecretStr; no module reads os.environ
  obs/            masked logging + the source-health registry behind /freshness
  sources/        one module per upstream, each returning (value, Provenance, Freshness)
  science/        pure NumPy/xarray — fronts, PFZ, anomalies, colormaps, H3
  services/       the DETERMINISTIC CORE. Imports nothing from agents/ (asserted by a test)
  agents/         LangGraph StateGraph: 12 nodes, typed tools, a critic that can veto
  language/       9 coastal languages + the number-integrity guard
  api/routes/     the HTTP surface
  jobs/           scheduler, ingest, and the proactive trip monitor
```

## Tests

```powershell
.\scripts\dev.ps1 -Test    # ruff format + ruff check + pytest
```

312 tests. What they are for is worth stating, because the count on its own means
nothing: the suite exists to pin the behaviours that are invisible from outside
and expensive to get wrong. A sample of what is actually asserted —

- the blueprint's worked risk case returns **index 25, NO-GO, 2 vetoes**;
- `services/` never imports `agents/`, so the deterministic core cannot acquire a
  model dependency by accident;
- the number guard catches an injected mangle, and a **unit** mangle (`kn` → `km`)
  is caught even when every numeral survives;
- protected verdict words survive every hyphen variant a model might emit,
  including U+2011, which once produced `NO‽GO`;
- A\* returns the same cost as exhaustive Dijkstra over the same lattice, so
  "optimal under the cost model" is verified rather than asserted;
- the trip monitor stays **silent** on a first observation and on a persisting
  condition — the silence is the feature;
- `geodesic_m` matches PostGIS to 0.001 m on a known pair.

The frontend is checked by rendering it: `frontend/shot.mjs` screenshots the
console and reports every console error and failed request, because a clean
typecheck says nothing about whether the globe painted.

## Security

Secrets are `SecretStr`, and a `SecretScrubber` filter is installed on the root log handler that
rewrites both known credential values *and* credential-shaped patterns out of every log record —
because a screen-shared demo terminal is the most likely way an API key leaks. `/config` serves
the effective configuration with every secret fingerprinted, and a test asserts no raw secret can
be served over HTTP.
