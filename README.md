# ORCA

**Marine EcOsystem Reasoning with Collaborative Agents** — Smart India Hackathon 2026

Agentic marine decision support for the Indian EEZ: a fisherman asks a question in their own
language, and ORCA discovers the right datasets, reasons over them with a team of specialist
agents, computes a safety verdict with a **deterministic rule engine**, and answers with every
number carrying its source, its provenance state and its age.

- `docs/PLAN.md` — the master build plan (architecture, science, 16 build steps)
- `docs/CREDENTIALS_VERIFIED.md` — every credential and endpoint, live-tested
- `ORCA_SIH2026_Build_Blueprint.pdf` — the originating blueprint

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
6. **AIS coverage over the Indian Ocean is sparse** on the free tier. Real vessels render `LIVE`;
   the demo fleet renders `SIMULATED`, hatched, and is never silently mixed with live data.
7. **Konkani has no TTS** in either IndicF5 or Bulbul.
8. ORCA is safety-of-life-**adjacent** decision support, not a certified marine safety system.
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

## Security

Secrets are `SecretStr`, and a `SecretScrubber` filter is installed on the root log handler that
rewrites both known credential values *and* credential-shaped patterns out of every log record —
because a screen-shared demo terminal is the most likely way an API key leaks. `/config` serves
the effective configuration with every secret fingerprinted, and a test asserts no raw secret can
be served over HTTP.
