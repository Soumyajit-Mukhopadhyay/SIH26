# ORCA — Decision Logic (slide deck text)

Copy each **Slide** block into PowerPoint. Speaker notes are in *italics* under each slide.

Policy version in code: `orca-thresholds-2026.09` · Live table: `GET /risk/thresholds`

---

## Slide 1 — Title

**ORCA decision logic**

Deterministic safety verdicts for Indian coastal fishing

- Rule engine owns GO / CAUTION / NO-GO / UNVERIFIABLE
- LLM explains only — never decides
- Every number cited: source, age, threshold version

---

## Slide 2 — Two layers (not one score)

**Layer A — Trip decision (priority cascade)**  
Highest authority wins. Not weighted.

1. Official IMD / INCOIS warning  
2. Legal prohibition (bans, notices — when connected)  
3. Geographic prohibition (EEZ / IMBL / restricted zones)  
4. Environmental rule engine (waves, wind, visibility, convective context)

**Layer B — Environmental comfort index (inside layer 4 only)**  
Four inputs → hard vetoes first → then transparent 0–100 index

| Input | Role |
|---|---|
| Significant wave height (Hs) | Primary operability / capsize driver |
| Wind speed | Primary operability; aligned to IMD warning bands |
| Visibility | Navigation safety (class-dependent floor) |
| CAPE (convective energy) | Thunderstorm *potential* — soft score only, not an IMD alert |

*A high fishing-opportunity (PFZ) rank cannot cancel an official warning or a hard veto.*

---

## Slide 3 — Final action cascade (diagram text)

```
Official WARNING / EMERGENCY?  →  DO NOT PROCEED
        ↓ no
Legal PROHIBITED?                →  DO NOT PROCEED
        ↓ no
Geographic PROHIBITED?           →  DO NOT PROCEED  (e.g. IMBL crossed)
        ↓ no
Environmental NO-GO?             →  DO NOT PROCEED
        ↓ no
Environmental UNVERIFIABLE?      →  UNVERIFIABLE
        ↓ no
Environmental CAUTION?           →  CAUTION
        ↓ no
                                 →  PROCEED
```

Unknown official or legal status is **flagged**, not treated as “all clear.”

---

## Slide 4 — Hard vetoes (most defensible part)

**Applied before any blended score.** If any limit is exceeded → **NO-GO**, regardless of index.

Per **Indian fleet boat class** (traditional → deep-sea):

| Check | Rule |
|---|---|
| Wave | Hs ≥ class `max_wave_m` → veto |
| Wind | speed ≥ class `max_wind_kn` → veto |
| Visibility | below class `min_visibility_km` → veto |

**CAPE / lightning:** graded into comfort index only — **never alone a hard veto** (India has no free authoritative lightning feed; CAPE ≠ official alert).

Example veto text shown to user:  
*“Hs 1.6 m is at or over the 1.5 m limit for your 8 m motorised FRP boat.”*

---

## Slide 5 — Where veto limits come from (literature + IMD)

### Peer-reviewed small-craft operability

**Journal of Marine Science and Engineering (2023)**  
DOI: [10.3390/jmse11071302](https://doi.org/10.3390/jmse11071302)

> Operability of small craft degrades sharply above **Hs ~1 m** for hulls **under 10 m LOA**; vessels to **24 m LOA** retain operability to approximately **Hs 2 m**.

**ORCA use:** seeds `max_wave_m` for IND-TRAD (1.0 m), IND-MOT-S (1.5 m), IND-MECH-S (2.0 m), IND-MECH-L (2.5 m).

---

### IMD marine warning conventions (operational authority)

**India Meteorological Department — Mausam marine services**  
Portal: [https://mausam.imd.gov.in/](https://mausam.imd.gov.in/)  
API catalogue: [https://api.imd.gov.in/public/api_reference.html](https://api.imd.gov.in/public/api_reference.html)

**IMD conventions ORCA aligns to:**

| IMD product / convention | ORCA mapping |
|---|---|
| **Small-craft warning** — winds **22–33 kn** | Wind limits for inshore / mechanised classes (e.g. 22 kn bottom of band for 7–10 m FRP) |
| **Gale warning** — from **34 kn** | Upper bound of small-craft band; deep-sea class limit 40 kn |
| **Fishermen warning** — do not venture to sea | Official cascade layer (IMD CAP RSS + keyed JSON when approved) |
| **Port warning / sea-area bulletin / coastal bulletin** | Official cascade layer |
| **Cyclone track / wind warning / cone** | Official cascade layer |

> *Fishermen are advised not to venture into the sea under a small craft warning.*

**ORCA does not issue warnings.** It reads IMD products and escalates when verified.

---

### INCOIS SVAS (methodology cited; numbers not published)

**INCOIS Small Vessel Advisory Service**  
[https://incois.gov.in/portal/osf/svas.jsp](https://incois.gov.in/portal/osf/svas.jsp)

INCOIS publishes the **service and methodology**; **numeric GO/NO-GO thresholds are not public.** ORCA therefore:

- Seeds limits from **JMSE + IMD** (above), not by claiming INCOIS’s unpublished table
- Computes **BSI in parallel** from the published SVAS paper (next slide)

---

## Slide 6 — Comfort index (transparent; weights are policy)

**Only if no hard veto and data is decision-grade:**

**Index** = 0.35×wave_score + 0.30×wind_score + 0.15×vis_score + 0.20×convective_score

| Component | Scoring shape | Rationale |
|---|---|---|
| Wave, wind | `100 × (1 − (value/limit)²)` | Quadratic: risk rises **faster than linear** near limit (small-craft physics intuition) |
| Visibility | linear to 10 km, cap 100 | Beyond ~10 km, marginal navigational gain for coastal craft |
| CAPE | band → score (LOW…VERY_HIGH) | NOAA-style instability bands — **potential**, not strikes |

**Verdict bands on index:**

| Index | Verdict |
|---|---|
| ≥ 70 | GO |
| 40–69 | CAUTION |
| < 40 | NO-GO |

**Honest statement for examiners:**  
Weights **35 / 30 / 15 / 20** and cutoffs **70 / 40** are **versioned ORCA policy** (`orca-thresholds-2026.09`), exposed via API — not copied from a single national “marine GO score” standard. They prioritise wave and wind because literature + IMD treat them as primary go/no-go drivers for Indian small craft.

---

## Slide 7 — CAPE / convective component (literature)

**No free authoritative lightning observation grid for India** (e.g. GOES GLM does not cover the basin).

ORCA uses **CAPE** (J/kg) from forecast models as a **thunderstorm-potential indicator**.

**Reference — NOAA convective indices guidance**  
[https://www.weather.gov/lmk/indices](https://www.weather.gov/lmk/indices)

| CAPE (J/kg) | Label (ORCA) | Interpretation |
|---|---|---|
| < 500 | LOW | Weak instability |
| 500–1000 | MODERATE | |
| 1000–1500 | ELEVATED | |
| 1500–2500 | HIGH | Moderate instability (NOAA-style) |
| > 2500 | VERY HIGH | Strong instability — severe thunderstorm *potential* |

**Not used as:** IMD lightning alert, strike probability, or standalone NO-GO gate.

---

## Slide 8 — Parallel scientific model: INCOIS BSI (0–7)

**Separate from the 0–100 comfort index.** Shown alongside; does not replace GO/CAUTION/NO-GO.

**Aditya, Sandhya, Harikumar & Nair (2020/2022)**  
*Development of small vessel advisory and forecast services system for safe navigation and operations at sea.*  
**Journal of Operational Oceanography**  
DOI: [10.1080/1755876X.2020.1846267](https://doi.org/10.1080/1755876X.2020.1846267)

**Verified equations in ORCA:**

| Criterion | Index | Threshold | BSI contribution |
|---|---|---|---|
| Wave steepness | Isteepness = (Ss/0.05)×(Hs/2.5) | > 0.8 | +1 |
| Crossing seas | Icrossing = 0.5×Hs×exp(−10(σs−1)²) | > 0.65 | +2 |
| Rapid wave development (6 h) | Z6h = \|Hsea_i − Hsea_f\| / Hsea_i | ≥ 0.2 | +4 |

**BSI = 0** → safe for all three criteria (paper §3.2.4)  
**Any BSI > 0** → dangerous in SVAS framework

Beam capsize check (eq. 10): **Beam < 4×Hs** when BSI already non-zero.

---

## Slide 9 — Data confidence (when we refuse to guess)

**UNVERIFIABLE** is a first-class outcome — not “safe,” not “dangerous.”

Low confidence → escalate message; user told to confirm on VHF / fisheries office:

- Forecast older than **6 h** (`CONFIDENCE_AGE_LIMIT_H`)
- Missing wave, wind, visibility, or CAPE
- No decision-grade evidence (source down)
- Vessel class unknown (no silent default boat)

---

## Slide 10 — What the user sees (audit trail)

Every verdict carries:

- `verdict_source: "rule_engine"` (LLM cannot set verdict — type-enforced)
- `thresholds_version: "orca-thresholds-2026.09"`
- Per-component **formula string** (e.g. `max(0, 100 × (1 − (1.2/1.5)²)) = 36.0`)
- **Veto list** with units and class limits
- **Citations** per boat row (JMSE, IMD, INCOIS SVAS note)
- **Evidence panel:** provider, age, LIVE/CACHED/UNAVAILABLE

Demo check: click sea point → Evidence panel + `GET /risk/thresholds`

---

## Slide 11 — References (copy to final slide)

1. **Small-craft operability / Hs limits** — J. Mar. Sci. Eng. (2023). DOI: [10.3390/jmse11071302](https://doi.org/10.3390/jmse11071302)

2. **INCOIS SVAS / BSI equations** — Aditya, N. D. et al. (2020/2022). *J. Operational Oceanography*. DOI: [10.1080/1755876X.2020.1846267](https://doi.org/10.1080/1755876X.2020.1846267) · INCOIS overview: [incois.gov.in SVAS](https://incois.gov.in/portal/osf/svas.jsp)

3. **IMD marine warnings & API products** — India Meteorological Department. [mausam.imd.gov.in](https://mausam.imd.gov.in/) · API reference: [api.imd.gov.in/public/api_reference.html](https://api.imd.gov.in/public/api_reference.html) · CAP feed: [IMD CAP RSS](https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml)

4. **CAPE as instability indicator (not lightning)** — NOAA Weather Forecast Office indices. [weather.gov/lmk/indices](https://www.weather.gov/lmk/indices)

5. **ORCA policy (weights, bands, boat table)** — `backend/orca/services/thresholds.py` · `backend/orca/services/risk_engine.py` · version `orca-thresholds-2026.09`

---

## Slide 12 — Examiner Q&A (short answers)

**Q: Why 35% wave and 30% wind?**  
A: Wave and wind are the primary operability limits in small-craft literature and IMD warning bands. The exact split is declared ORCA policy, versioned and API-visible — ready to recalibrate when INCOIS publishes SVAS numbers or fleet data is available.

**Q: Is this INCOIS-certified?**  
A: No. We cite INCOIS SVAS methodology and implement Aditya et al.’s BSI equations. Threshold tables are seeded from JMSE + IMD because INCOIS numeric limits are unpublished.

**Q: Does CAPE mean lightning?**  
A: No. CAPE is atmospheric instability potential from the forecast model. Official lightning/cyclone/fishermen status comes from IMD feeds.

**Q: Can the AI override NO-GO?**  
A: No. `verdict_source` is locked to `rule_engine`. A critic node rejects drafts that soften the verdict.

**Q: What if IMD API key is missing?**  
A: ORCA still checks the public IMD CAP RSS. JSON products (fishermen warning, cyclone track) activate after registration. Unknown ≠ no warning.

---

## One-paragraph abstract (for report cover)

ORCA separates **authoritative warnings** from **environmental physics**. Trip decisions follow a fixed priority cascade: official IMD warnings override legal and geographic constraints, which override a deterministic environmental rule engine. Environmental assessment applies **cited vessel-class hard limits** on significant wave height (JMSE, 2023), wind speed (IMD small-craft and gale conventions), and visibility, then a **transparent weighted comfort index** (wave 35%, wind 30%, visibility 15%, convective indicator 20%) whose weights are versioned policy rather than a hidden heuristic. In parallel, ORCA computes INCOIS’s **Boat Safety Index** from Aditya et al. (2020/2022) without collapsing scales. The language model explains every verdict; it never issues one.
