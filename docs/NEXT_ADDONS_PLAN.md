# ORCA — research findings and the next build

Written 14 September 2026, after a live-verification pass over every source and
model named here. Anything I could not confirm by actually calling it is marked
**unverified**; anything I confirmed is marked with what I saw.

---

## 0. The headline decisions

| Question | Decision | Why |
|---|---|---|
| Is TerraMind the right core model? | **No — and yes as a specialist.** | It is an *image* encoder for 224×224 Sentinel chips. It structurally cannot forecast waves, wind or sea state, which is ORCA's core. It is excellent for coastal EO segmentation, which is a second-tier product. |
| What deep-learning model ships first? | **FrontCast** — forecast thermal fronts at +1/+2/+3 days. | It is the only marine DL task where we can generate labels ourselves, today, for the Indian EEZ, at zero cost — because we already own the classical detector. |
| Where does the researcher dashboard go? | **A full-screen mode off the masthead**, not another floating rail. | A researcher is a different *audience*, not another tool for the same user. |
| MOSDAC? | **Adapter designed, blocked on the account.** | Verified: the account is locked, and the lock is Keycloak's, not an admin's. Details in §4. |

---

## 1. TerraMind — what I found, and the honest verdict

**It is real and it is good.** Verified directly against the Hugging Face API:

| Variant | Weights | Licence |
|---|---|---|
| TerraMind-1.0-tiny | 212 MB | Apache 2.0 |
| TerraMind-1.0-small | 504 MB | Apache 2.0 |
| TerraMind-1.0-base | 1 519 MB | Apache 2.0 |
| TerraMind-1.0-large | 3 788 MB | Apache 2.0 |

Built by IBM + ESA + Forschungszentrum Jülich, pre-trained on 500 B tokens from
9 M multimodal samples. Inputs: `S2L2A, S2L1C, S1GRD, S1RTC, DEM, RGB`. 224×224
chips → 196 patches × 768-dim embeddings. **It handles missing modalities**, and
**Apache 2.0 means commercial use is safe** — which is rare in this class and is
the single best thing about it.

### Why it is not the core model

1. **It cannot forecast.** It encodes an image. ORCA's safety verdict comes from
   gridded forecast fields — wave height, wind, CAPE. No amount of Sentinel chip
   encoding produces tomorrow's sea state.
2. **Sentinel-2 is blind exactly when we need it.** ~5-day revisit and optical,
   so cloud blocks it. During the south-west monsoon — precisely when marine
   advice matters most — usable scenes over the Indian coast are scarce.
3. **We have no labels.** Fine-tuning sigmoid heads for the Indian EEZ needs
   labelled coastal data (bloom / spill / turbidity) that does not exist openly
   for 60–100°E, 0–25°N. Untrained heads emit noise wearing a confident number.
4. **Weight.** `terratorch` + torch is multiple GB on a project whose stated
   selling point is that it runs with no infrastructure.

### Where it genuinely belongs — Tier 2

**Sentinel-1 SAR, not Sentinel-2 optical.** SAR sees through cloud, so it is the
one EO modality that works during monsoon. Frozen TerraMind-tiny as an encoder
over S1GRD chips — which we *already fetch* through the CDSE/Sentinel Hub
adapter — plus a temporal transformer over a chip time series, plus sigmoid
heads for coastal multi-label states.

**The gate:** ship the pipeline and the UI with the heads explicitly marked
untrained until labels exist. An untrained head that renders a plausible
probability is worse than an empty panel.

### Your named architecture, applied where it works

You asked for *pretrained TerraMind + temporal transformer + sigmoid heads*.
The architectural pattern is right; the substrate was wrong. So **FrontCast uses
the pattern on ocean fields, where labels exist**:

```
SST history (5 days) → shared per-day CNN encoder
                     → temporal transformer over the DAY axis
                     → U-Net decoder
                     → 3 independent sigmoid heads  (+1d, +2d, +3d)
```

Each piece earns its place:

- **Shared per-day encoder** — history length can change without retraining.
- **Temporal transformer over the day axis** — fronts *move*, and the direction
  they are moving is only visible across days. Spatial dims are folded into the
  batch so attention is 5×5 per pixel, not (5·H·W)², which is the difference
  between training on a laptop and not. A learned per-day embedding, because
  recency is not symmetric.
- **Three sigmoid heads, not a softmax** — a pixel can be a front on all three
  days at once. A softmax would force the lead times to compete for one unit of
  probability and make persistence *literally unrepresentable*.

---

## 2. The other models I checked

Verified availability, licence and hardware. Separating "weights downloadable
today" from "paper exists".

| Model | Weights today? | Licence | Runs on a laptop? |
|---|---|---|---|
| **Pangu-Weather** | Yes | CC BY-NC-SA 4.0 | Yes — native ONNX, CPU script ships |
| **ECMWF AIFS Single 2.0** | Yes, 994 MB | **CC BY 4.0** — commercial OK | Likely; PyTorch-native |
| **NVIDIA FourCastNet** | Yes | **Apache 2.0** | Docs assume datacentre GPUs |
| GraphCast (full) | Yes | CC BY-NC-SA | **No** — OOMs on a 24 GB 4090 |
| GenCast (full) | Yes | CC BY-NC-SA | **No** — ~60 GB VRAM + ~300 GB RAM |
| Microsoft Aurora | Yes | CC BY-NC-SA | **No** — ~40 GB GPU |
| **XiHe / WenHai** (ocean) | Yes | **No licence stated** | Fast forward pass, but needs a global 1/12° GLORYS state assembled as input |
| AI-GOMS, FuXi-Ocean, LangYa | Paper only / unverified | — | — |

**Universal blocker:** every one needs a near-real-time *global* initial
condition, not a local slice. None bootstraps from sparse local observation.
That is a standing server pipeline, not something that runs beside ORCA today.

**If we later want a headline foundation model**, ECMWF AIFS is the one to take:
under 1 GB, PyTorch-native, and CC BY 4.0 is the only licence here that survives
commercialisation.

---

## 3. FrontCast — what shipped

**Task.** Five days of MUR SST in → probability of a thermal front zone at +1,
+2, +3 days.

**Labels.** ORCA's own Cayula–Cornillon SIED on the *observed* field at the
target day. Not circular — the model never sees the target day's temperature —
but it does mean FrontCast inherits SIED's definition of a front including its
blind spots. It can be better about **when**, never about **what**. Every
response says so.

**Two measurements that shaped it:**

1. **SIED's window is fixed in pixels, so its behaviour depends on the grid.**
   At 0.2° the 32×32 window spans 6.4° and the detector returns an **empty
   mask** — measured, not assumed. Training runs at 0.1°, where it finds fronts
   over ~2% of the field.
2. **Raw SIED masks are only ~26% persistent day to day.** A front is a *zone*;
   which pixel inside it the detector picks is close to arbitrary. So the target
   is the mask widened by one cell to a ~22 km band — applied identically to the
   persistence baseline, so the task gets better posed without the comparison
   getting flattering.

**Every run scores persistence beside the model.** "Tomorrow's fronts are
today's fronts" is a strong forecast at one day. A model that cannot beat it has
learned nothing, and an F1 with nothing to compare it against is not
information.

**Two bugs the baseline caught immediately**, which is the point of having one:
inverse-frequency class weighting came out near 40 and the model learned to
predict "front" on every pixel (recall 0.95, precision 0.02); and a fixed 0.5
threshold is an arbitrary slice through a probability field. Both fixed —
weight capped at 6, threshold fitted on validation *and reported*.

**It cannot move a safety verdict.** A front is a fishing signal, not a hazard
threshold. `orca.services` does not import `orca.ml`.

---

## 4. MOSDAC — verified, and precisely diagnosed

I tested the credentials directly. Findings:

| Probe | Result |
|---|---|
| `www.mosdac.gov.in` | 200 — live |
| `www.mosdac.gov.in/thredds/catalog.xml` | **200 — a real THREDDS server** |
| THREDDS contents | Only **ISRO GSICS inter-calibration products** (INSAT-3D/3R/3S vs MetOp IASI). No ocean geophysical products. Its root catalog is still the stock "You must change this to fit your server!" default. |
| `opendap.mosdac.gov.in` | DNS does not resolve |
| Auth system | **Keycloak** — realm `Mosdac`, OIDC discovery at `/realms/Mosdac/.well-known/openid-configuration` |
| Login with your credentials | **"Account is temporarily disabled; contact your administrator or retry later."** |
| Direct password grant | `unauthorized_client` — the public `account` client does not permit it |

### The useful diagnosis

That message is **Keycloak's brute-force lockout wording, not an admin ban.**
Keycloak emits it when its failed-login detector trips. That is good news: it is
usually temporary, and a **password reset normally clears it immediately** —
which is likely faster than waiting on the application you filed.

**Practical route when the account returns:** MOSDAC's data access is a Keycloak
authorization-code flow into the order/download portal, not a clean REST API.
The adapter shape is: OIDC login → session cookie → the portal's download
endpoints. The public THREDDS server is *not* a shortcut, because it carries no
ocean products.

**Honest expectation:** MOSDAC is a credibility win with an ISRO-adjacent jury
and a modest data win. Oceansat-3 OCM and SCATSAT winds would be genuinely
useful; INSAT-3D SST would give an Indian-sourced cross-check. But we already
serve INCOIS Oceansat-2 and ASCAT winds through ERDDAP with no auth at all.

---

## 5. The researcher workspace — what shipped

**The rule:** a language model parses the request; **deterministic code does the
matching.** The model turns prose into variables, a bounding box and a date
range, drawing only from closed vocabularies, and it is forbidden to name a
dataset or write a URL.

So the worst a bad parse can do is return the wrong *real* dataset. It cannot
invent a plausible dataset id. That failure would matter most here and surface
latest: a fisherman can tell when an advisory is wrong because he can see the
sea; a researcher handed a fabricated identifier finds out weeks later, after
building on it.

A keyword parser runs on **every** request and is unioned with the model's
output, so discovery still works when no LLM provider answers.

**15 datasets, 10 servable.** Every entry states resolution *as ORCA serves it*
alongside the upstream native figure — a researcher copying our number into a
methods section should be copying the one describing the array they actually
received. Every entry carries caveats, because a catalogue that lists only
capabilities is how a monthly composite ends up answering a daily question.

Verified live:

- `POST /research/discover` — "chlorophyll blooms in the Bay of Bengal during
  the 2025 monsoon" → variables `[chlorophyll]`, bbox `[78, 5, 95, 23]`, dates
  `2025-06-01 → 2025-09-30`, with the assumption *stated*.
- `GET /research/export` — CSV with a provenance header and a citation line.
- Refusals are honest: a non-servable dataset returns the upstream endpoint and
  licence rather than pretending; an oversized request names the real route.

---

## 6. Add-ons worth building next, ranked

Only items where I confirmed the data actually exists and is reachable.

### Tier A — high value, data confirmed

| # | Add-on | Why it matters | Data |
|---|---|---|---|
| A1 | **CIN gate on the lightning veto** | The single biggest correctness win available. CAPE is *potential*; in monsoon ORCA refuses almost the whole coast on lightning alone. High CAPE with strong convective inhibition means the atmosphere is capped and storms will not fire. | **Already fetched.** Open-Meteo `convective_inhibition` is in the evidence set and unused. Zero new integration. |
| A2 | **Argo float profiles** | Real *in-situ* subsurface observations — the only ground truth in the whole system. Turns cross-validation from model-vs-model into model-vs-measurement. | Argovis / Euro-Argo REST. **Unverified** — confirm the keyless endpoint before committing. |
| A3 | **Persistence layer** | Nothing survives a restart: traces, alerts, watched positions all live in memory. PostGIS is already probed and connected. | None needed. |
| A4 | **Authority / fleet dashboard** | The third audience the PS names, and the only one with no surface at all. The data model already supports it. | None needed. |

### Tier B — good, more work

| # | Add-on | Note |
|---|---|---|
| B1 | **SST-anomaly forecaster** | Same FrontCast scaffolding, different head. NOAA OISST v2.1 is daily 0.25°, 1981→present, **no login at all**. Trainable in an afternoon. |
| B2 | **Chlorophyll forecaster → PFZ *forecast*** | Feed predicted SST anomaly + chlorophyll into the existing PFZ rule and you get a 1–3 day-ahead fishing advisory, ahead of INCOIS's own 3×/week cadence. This is the strongest novelty claim available. |
| B3 | **TerraMind on Sentinel-1 SAR** | Tier 2 above. Oil-spill and vessel candidates, monsoon-proof. Ship with heads marked untrained. |
| B4 | **OpenDrift Leeway cross-check** | Validate our IAMSAR drift against the reference implementation. Credibility, not new capability. |

### Tier C — do not build

| Item | Why not |
|---|---|
| GraphCast / GenCast / Aurora inference | Genuinely cluster-class. 40–60 GB VRAM. |
| XiHe / WenHai ocean models | **No licence stated in either repo**, and both need a global 1/12° GLORYS state assembled as input. |
| Fish-catch ML from real catch labels | I could not find a point-level open catch dataset for the Indian EEZ. Sea Around Us is annual and EEZ-aggregated; GFW under-represents exactly the fleet we serve, because most Indian small craft carry no AIS. |

---

## 7. What I need from you

| # | Item | Unblocks | Urgency |
|---|---|---|---|
| 1 | **MOSDAC password reset** (not a new application) | The whole MOSDAC track | Try it now — the lock reads as a Keycloak brute-force trip, which a reset usually clears |
| 2 | OpenRouter credit (~$10) | Removes the last free-tier LLM fragility | Medium |
| 3 | IMD API key + egress IP whitelist | Official bulletins beside ORCA's advisory | Medium |
| 4 | Bhashini credentials | A sovereign translation fallback behind Sarvam | Low — Sarvam works |

Nothing in Tiers A or B is blocked on any of these.
