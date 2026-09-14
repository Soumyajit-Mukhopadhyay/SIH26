# ORCA — requirement matrix and self-score

Every row is a claim, and every claim names **how to check it** in a running
system. That is the whole point of this document: a matrix of green ticks that
cannot be verified is a slide, not a status report.

Three states, and they are used strictly:

| State | Meaning |
|---|---|
| **Demonstrable** | Works against live data, in the running system, by the stated check. |
| **Partial** | Works with a stated limitation, written in the row. |
| **Not built** | Not implemented. Named here rather than omitted. |

Nothing in this file is marked Demonstrable on the strength of a unit test alone.
Unit tests are noted where they pin behaviour that is hard to see from outside —
312 of them pass — but "demonstrable" means someone can watch it happen.

---

## P0 — the problem statement's core

| # | Requirement | State | How to check it |
|---|---|---|---|
| P0-1 | A safety verdict for a position and a vessel | **Demonstrable** | Click any sea point. `GO / CAUTION / NO-GO / UNVERIFIABLE` with an index out of 100, the boat class, and the cited thresholds version. |
| P0-2 | The verdict is deterministic, not model-generated | **Demonstrable** | `POST /risk/assess` returns `verdict_source: "rule_engine"` — a `Literal`, so "llm" is unrepresentable. The card says "no language model" and a test asserts `services/` never imports `agents/`. |
| P0-3 | Every number carries its source, age and provenance | **Demonstrable** | The evidence panel: dataset, provider, age, and one of six provenance states per value. `GET /forecast/point` returns the same objects the UI renders. |
| P0-4 | Missing data does not read as safe data | **Demonstrable** | Click a land cell or a coverage gap: `UNVERIFIABLE` with "ORCA could not obtain enough data to judge — this is not the same as safe", and the specific missing inputs listed. |
| P0-5 | Natural-language questions, answered with the trace visible | **Demonstrable** | Ask anything in the console. Plan appears first, then each tool with its measured latency, then the critic's ruling, then the answer. `curl -N /agent/stream` shows the frames arriving separately, not in one lump. |
| P0-6 | Answers in Indian coastal languages | **Demonstrable** | Set the reply language. 11 languages; the guard verifies every numeral, unit and verdict term survived and reports per-clause failures. |
| P0-7 | Voice in and voice out | **Demonstrable** | Press-to-talk with a live level meter → transcript shown for correction → answer spoken. Sarvam Saarika for Indic, Groq Whisper for English; Bulbul v3 for speech. |
| P0-8 | Maritime boundary awareness | **Demonstrable** | Set a heading and speed near Palk Bay: "you cross Sri Lanka – India in N minutes", from a state machine that fires on transitions. |
| P0-9 | Where to fish | **Demonstrable** | `find_fishing_zones`, or ask. Derived PFZ ranks with distance and bearing, and an explicit list of which criteria could not be applied. |
| P0-10 | An operational map of live conditions | **Demonstrable** | MapLibre globe projection with deck.gl: SST, chlorophyll, thermal fronts, PFZ, and wind/current particle advection. Every layer badges its provenance. |

## P1 — differentiators

| # | Requirement | State | How to check it |
|---|---|---|---|
| P1-1 | Compound questions answered in full | **Demonstrable** | "Is it safe tomorrow, and where will I find fish?" — the trace shows the decomposition, the intents, and the tool union. Verified in English, Tamil, Hindi and romanised Tamil. |
| P1-2 | A critic that can reject the model's own draft | **Demonstrable** | The trace shows the ruling and the round count; a rejected draft is reported in the UI rather than hidden. |
| P1-3 | Safe-passage routing with a rationale | **Demonstrable** | Pick a destination, plan. A*, with a vetoed cell impassable rather than expensive; when no route exists the blocking cells are named with their reasons. |
| P1-4 | SAR drift as a search area | **Demonstrable** | SAR panel. IAMSAR `current + leeway(wind)`, 2000-particle Monte Carlo, 50%/95% containment hulls. Never a single predicted position. |
| P1-5 | Proactive alerts, unprompted | **Demonstrable** | "Watch this point", then let it run or press "check now". Alerts fire on transitions only and carry their previous value. |
| P1-6 | Machine-readable advisories | **Demonstrable** | `GET /advisories/cap` returns CAP 1.2 with XSD-ordered `<info>` children and a second block for the local language. `/advisories/cap/validate` checks the ordering. |
| P1-7 | 3D sea state driven by the real forecast | **Demonstrable** | Sea view. Amplitude from Hs, wavelength from `L = gT²/2π`, direction from the reported bearing, boat scaled to its LOA, and a red contour at the class Hs limit. |
| P1-8 | Cinematic entry | **Demonstrable** | First load: NASA GIBS Blue Marble globe with atmosphere and city lights, descending to the Indian EEZ. Skippable, once per session. |
| P1-9 | Visual treatments with a performance guard | **Demonstrable** | Seven treatments. The guard measures median frame time and turns a treatment **off** when it cannot hold the budget, rather than displaying a number. |
| P1-10 | Honest degradation when a source fails | **Demonstrable** | The freshness strip shows sources up/down; a failed tool gets a red row in the trace with its error; the deterministic endpoints keep working when the agent plane fails. |
| P1-11 | Replayable agent traces | **Partial** | `GET /agent/runs/{id}` replays a full trace, but from an in-process ring buffer — traces do not survive a restart. The `agent_runs`/`agent_steps` tables are not wired. |
| P1-13 | A researcher-facing data catalogue and NL discovery | **Demonstrable** | Open the Researcher workspace from the masthead. Ask in prose: the panel shows the variables, box and dates it parsed AND every assumption, then ranks 15 real datasets. A language model parses; deterministic code matches, so it cannot name a dataset that does not exist — `tests/test_research.py` pins that boundary. CSV export carries a provenance header and a citation line. |
| P1-14 | A trained deep-learning model, with its skill published | **Partial** | `GET /ml/models` and the workspace's Models tab. FrontCast (211k params: per-day CNN encoder → temporal transformer over the day axis → three independent sigmoid heads) forecasts thermal fronts at +1/+2/+3 days from 5 days of MUR SST, trained on 151 days over the Indian EEZ. **Its measured skill is shown beside a persistence baseline, and where it loses, the UI says so in amber.** See the honest note below. |
| P1-12 | Multi-source cross-validation | **Demonstrable** | `GET /validation/point` aligns valid times and units for model-vs-MUR SST, Open-Meteo-vs-NASA POWER wind, and Open-Meteo-vs-CMEMS significant wave height. Provider uncertainty is used where supplied; otherwise the response labels ORCA's cross-model tolerance explicitly. |

## P2 — planned, and where they actually stand

| # | Requirement | State | Note |
|---|---|---|---|
| P2-1 | Persistence layer (SQLite + PostGIS repositories) | **Partial** | PostGIS is probed at startup and the geofence index uses shapely/STRtree when it is absent. The repository layer itself is not written; nothing is persisted between restarts. |
| P2-2 | Langfuse trace export | **Not built** | Credentials are configured and dormant. |
| P2-3 | Satellite overpass prediction (SGP4) | **Demonstrable** | Current CelesTrak GP/TLE records are propagated locally with SGP4 for Sentinel-3A/B and EOS-06; `/satellites/overpasses` and the operational-intelligence panel label results as nominal-swath opportunities, not confirmed acquisitions. |
| P2-4 | CMEMS / NASA Earthdata / Sentinel Hub / AIS / GFW adapters | **Demonstrable** | Official Copernicus Marine Toolbox point access, NASA CMR discovery and POWER wind, CDSE OAuth + Sentinel Hub STAC and processed OLCI previews, bounded AISStream snapshots with CPA/TCPA screening, and GFW 4Wings reports are callable. `/integrations/status` states credentials, runtime readiness and caveats without returning secrets. |
| P2-5 | Authority dashboard (fleet view) | **Not built** | The single-vessel console is complete; a multi-vessel authority view is not. |
| P2-6 | Offline degradation mode | **Partial** | Rasters and reference geography are on disk and serve without network, and the earth texture is cached, so the map and layers survive a dead connection. There is no explicit offline banner and the point forecast needs the network. |
| P2-7 | Trip planner (multi-leg) | **Not built** | Single-leg routing works; multi-leg with time windows does not. |

---

## Deliberate non-goals

Stated so they are not mistaken for gaps:

- **ORCA does not issue warnings.** IMD and INCOIS do. Every surface says ORCA
  supplements and never replaces them, and the CAP output is shaped for their
  systems rather than positioned as an alternative.
- **ORCA sends no SMS, push or email.** It has no authority to contact a
  fisherman, and a prototype that quietly acquires a notification channel is one
  that can spam a real person. Alerts live in the console.
- **The model never issues a verdict.** It explains one. This is enforced by the
  type system, checked by the critic, and stated three ways in the prompt.
- **No single predicted position for a drifting person.** A search area, with the
  containment fraction stated.

## Known limitations worth saying out loud

- **The lightning veto is CAPE-derived, and CAPE is potential rather than
  occurrence.** During the south-west monsoon large stretches of the Indian coast
  carry 2000–4500 J/kg, which maps above the 60% veto and makes ORCA refuse
  almost everything there. That is defensible and it is also coarse: the honest
  reading of a NO-GO on lightning alone is "thunderstorm potential is high across
  this whole area", not "a storm is over your boat". A CIN gate would sharpen it.
- **Routing coarsens its lattice under the API budget**, and a coarser lattice
  makes the router *more* conservative — one vetoed 60 km cell blocks a corridor a
  boat might thread. The response says so when it happens.
- **The Gerstner sea surface is clamped at a steepness of 0.85.** Above that the
  parameterisation self-intersects, so a very steep sea is drawn gentler than it
  is; the panel reports the gap rather than hiding it.
- **FrontCast does not yet reliably beat persistence.** "Tomorrow's fronts are
  today's fronts" is a strong forecast at one day, and the model is trained on
  151 days — a single season. The result is reported per lead time with the
  baseline beside it, and the interface marks a loss in amber rather than
  showing the model's score alone. A score published without the baseline it
  must beat is a number chosen to look good; this is the opposite arrangement,
  and it is deliberate. Two things would most likely change it: several years of
  archive instead of one season, and adding surface current as an input channel
  so the model can see what actually advects a front.
- **TerraMind is not wired in.** IBM/ESA's geospatial foundation model is
  Apache 2.0 with downloadable weights (tiny 212 MB → large 3.8 GB), and it is
  the right model for coastal Sentinel-1 SAR segmentation — but it is an image
  encoder and cannot forecast sea state, and there is no labelled Indian-EEZ
  coastal dataset to train its heads on. Plan in `docs/NEXT_ADDONS_PLAN.md`.
- **MOSDAC is blocked on the account, not on engineering.** Its public THREDDS
  server serves only ISRO GSICS inter-calibration products, not ocean fields,
  and the portal is behind Keycloak. The supplied account returns "temporarily
  disabled", which is Keycloak's brute-force lockout wording rather than an
  administrative ban.
- **Free-tier LLM slugs move.** Three of four providers were dead at one point in
  development for three unrelated reasons. `/agent/providers` reports which one
  answered, and the deterministic verdict does not depend on any of them.
