# ORCA — the other stakeholders, and what to build for them

Written 14 September 2026. Every data source named here was **probed live**
before it was proposed. Anything that failed the probe is recorded with the
failure rather than quietly dropped, and nothing is proposed that depends on a
source I could not reach.

The problem statement names five stakeholder groups. ORCA currently serves two.

| Stakeholder | Named in the PS | ORCA surface today |
|---|---|---|
| Fishermen | yes | **Complete** — verdict, voice, routing, alerts |
| Researchers | yes | **Complete** — workspace, federation, ground truth, dataset builder |
| **Coastal authorities** | yes | **None** |
| **Disaster management agencies** | yes | **None** |
| **Maritime operators** | yes | Partial — routing and AIS exist, no operator view |

---

## Part 1 — What the PS asks for that ORCA does not do

Read line by line against the built system. Only genuine gaps listed.

| PS requirement | Status | Note |
|---|---|---|
| "proactive alerts for … **cyclones**" | **Missing entirely** | Named twice. Nothing in the system knows what a cyclone is. |
| "**marine protected areas, ecologically sensitive zones**" | **Missing** | Geofencing covers EEZ and IMBL treaty lines only. |
| "Are there any lightning or **cyclone** alerts in my area?" | **Half** | Lightning is CAPE-derived and labelled as such. Cyclone: nothing. |
| "Why has fish productivity declined …?" | **Refuses** | `diagnose_productivity` honestly declines to infer causality from a snapshot and names what it would need. Principled, but it is a refusal, not a capability. |
| "multi-turn conversations that enable users to **refine** queries" | **Unverified** | A `thread_id` exists; whether prior turns actually condition the next answer has not been demonstrated. Must be tested before it is claimed. |
| Agents for "**reporting**" | **Missing** | CAP export exists. Nothing produces a situation report for an official. |

---

## Part 2 — Features, by stakeholder

Ordered by value ÷ effort. Each names the data source and its verified status.

### A. Coastal authorities

**A1 · Harbour advisory board** — *the highest-value feature available, and it
needs no new data at all.*

A fisheries officer does not want one boat's verdict. They want: **which
stretches of my coast are unsafe today, and for whom.** ORCA already has the
deterministic risk engine, five cited boat classes, and a landmark registry of
Indian fishing harbours. Running the engine at every harbour × every class
produces exactly that table:

```
Kasimedu (Chennai)   TRAD ■ NO-GO   MOT-S ■ CAUTION  MECH-S ● GO   DEEPSEA ● GO
Digha (West Bengal)  TRAD ■ NO-GO   MOT-S ■ NO-GO    MECH-S ■ CAUTION …
```

The insight it surfaces that nothing else does: **the boundary between classes
moves up and down the coast.** An officer can see "today, traditional craft
should not sail anywhere between Kochi and Mangaluru" — which is the actual form
of a fisheries advisory.

- **Data:** none new. Risk engine + `thresholds.py` + existing harbour landmarks.
- **Effort:** low. One endpoint, one table view.
- **Status:** buildable today.

**A2 · Advisory as a signed, machine-readable bulletin**

CAP 1.2 export already exists per point. Extend it to the harbour board so an
authority can push one bulletin covering their whole jurisdiction into NDMA
SACHET's format.

- **Data:** none new.
- **Effort:** low — `cap_builder.py` already produces XSD-ordered CAP.

### B. Disaster management agencies

**B1 · Cyclone climatology and historical analogue** — *verified accessible.*

**IBTrACS**, NOAA's International Best Track Archive, keyless:

```
https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-
stewardship-ibtracs/v04r01/access/csv/ibtracs.NI.list.v04r01.csv
```

Probed live: **200, 27.8 MB, North Indian basin, 40 named storms in the last
three years**, each with full track — time, latitude, longitude, WMO wind,
pressure, storm speed and direction. The most recent in the 3-year file is
Cyclone DITWAH, December 2025, tracking to 12.1°N 80.2°E — directly off Chennai.

Two products worth building from it:

1. **Exposure climatology.** "How often has a cyclone passed within 100 km of
   this district, and in which months?" That is a real planning number for a
   district disaster officer, and nobody publishes it per-location.
2. **Historical analogue.** Given a developing system's position and track
   bearing, find the closest historical match and show where *that* one went.
   Far more useful to a planner than a probability cone, because it is concrete.

**The honest limit, stated up front:** IBTrACS is a **best-track archive**,
finalised after a season. It is climatology and hindsight, **not a live
warning**. ORCA must never present it as one. Live cyclone warnings for Indian
waters come from the IMD, whose cyclone page is HTML with no machine-readable
feed I could find — that needs the IMD API key already on the credentials list.

- **Data:** IBTrACS — **verified keyless**. Live warnings — **needs IMD**.
- **Effort:** medium.

**B2 · Who is exposed, and where would they go**

Combine the harbour board (A1) with cyclone track: which harbours lie in the
path, how many hours until conditions cross each class's limit, and which
harbour is the nearest one still GO. That is an evacuation-support answer, built
from pieces that already exist.

- **Data:** none new beyond B1.
- **Effort:** medium.

### C. Marine protected areas and sensitive zones

**C1 · Extend geofencing to MPAs** — *partially verified; needs one free token.*

The PS names "marine protected areas, ecologically sensitive zones" explicitly.
ORCA's geofence engine already does the hard part — an STRtree index and a state
machine that fires on transitions (clear → approaching → crossed) — so a new
polygon set is nearly free.

What I found:

- **Marine Regions** (already integrated, powers our EEZ and IMBL layers) does
  carry protection types — `Marine Park`, `Protected Area`, `Natural Reserve`,
  `National Park`. But **Indian coverage is thin**: across the first page of
  each type, exactly one Indian record surfaced (Bhitarkanika Conservation Area,
  Odisha, 20.73°N 86.42°E). Usable, not sufficient.
- **Protected Planet / WDPA** is the authoritative source. `api.protectedplanet.net`
  returns **401 without a token**; the token is a free signup. This is the right
  source and it is the one credential this feature needs.

- **Data:** Protected Planet token (**free signup, not yet held**).
- **Effort:** low once the token exists — the geofence machine is already built.

### D. Researchers — closing the gap I opened

**D1 · Make the dataset builder deliver more than two variables**

It currently advertises and delivers `sst` and `chlorophyll` only. The rest fail
for two knowable reasons: several INCOIS registry entries carry **no variable
mapping**, and three are **archives that ended in 2014, 2020 and 2023**. This is
registry work, not new integration, and it is the highest-value fix in the
researcher track.

**D2 · Turn the productivity refusal into a capability**

`diagnose_productivity` currently declines, correctly, to infer causality from a
snapshot. With the dataset builder now able to produce SST and chlorophyll time
series, it can do materially better: compare the stated period against the same
months in previous years and report **what changed in the water** — while still
refusing to attribute cause without CMFRI landings and effort data.

That distinction is the feature. "Chlorophyll in this box ran 40% below the
five-year mean for the same months" is evidence. "Therefore fish declined" is a
claim ORCA should keep refusing to make.

- **Data:** none new for the ocean side. CMFRI landings remain unavailable as
  machine-readable data.
- **Effort:** medium.

### E. Cross-cutting

**E1 · Multi-turn context — verify before claiming**

The PS asks for conversations that "enable users to refine queries". A
`thread_id` exists. Whether the second turn actually uses the first has not been
demonstrated. **This should be tested before the demo, not during it** — it is
the kind of thing a judge tests in one follow-up question.

**E2 · Situation report generation**

The PS lists "reporting" among the specialist agents. A one-page PDF or
structured brief for an official — conditions, verdict by class, alerts, cyclone
exposure, sources and timestamps — is a different artefact from a chat answer and
is what actually gets forwarded up a chain of command.

---

## Part 3 — MOSDAC, precisely

Tested with the supplied credentials.

| Probe | Result |
|---|---|
| `www.mosdac.gov.in` | 200 — live |
| `/thredds/catalog.xml` | **200 — a real THREDDS server**, but it serves only ISRO GSICS inter-calibration products (INSAT-3D/3R/3S against MetOp IASI). No ocean geophysical fields. Its root is still the stock "You must change this to fit your server!" default. |
| `opendap.mosdac.gov.in` | DNS does not resolve |
| `/opendata`, `/products` | Redirect to Keycloak login, or 404 |
| Auth | **Keycloak**, realm `Mosdac`, OIDC discovery at `/realms/Mosdac/.well-known/openid-configuration` |
| Login with the supplied credentials | **"Account is temporarily disabled; contact your administrator or retry later."** |
| Direct password grant | `unauthorized_client` — the public client does not permit it |

**Two things worth acting on:**

1. That message is **Keycloak's brute-force lockout wording, not an admin ban**.
   The login page itself exposes a `reset-credentials` link. **A password reset
   is very likely to clear it immediately** — faster than waiting on the
   application you filed.
2. **Everything useful on MOSDAC is behind that login**, including the page
   literally called "open data". The public THREDDS is not a shortcut: it
   carries no ocean products.

**What MOSDAC would add once open:** Oceansat-3 OCM ocean colour, SCATSAT winds,
INSAT-3D SST and cyclone products — an Indian-sourced cross-check against the
NASA and Copernicus products ORCA leans on, which is a genuine credibility win
with an ISRO-adjacent jury. The adapter shape is: OIDC authorization-code flow →
session → the portal's download endpoints. Designable now, blocked on the
account.

---

## Part 4 — Build order

| # | Feature | Stakeholder | Data status | Effort |
|---|---|---|---|---|
| 1 | Harbour advisory board | Coastal authority | **none needed** | low |
| 2 | Builder variable coverage | Researcher | none needed | low |
| 3 | Multi-turn context verification | All | none needed | low |
| 4 | Cyclone climatology + analogue | Disaster mgmt | **IBTrACS verified** | medium |
| 5 | Harbour board → CAP bulletin | Coastal authority | none needed | low |
| 6 | Exposure + nearest safe harbour | Disaster mgmt | none needed | medium |
| 7 | Productivity change evidence | Researcher | none needed | medium |
| 8 | MPA / ESZ geofencing | All | **Protected Planet token** | low after token |
| 9 | Situation report export | Authority / DM | none needed | medium |
| 10 | MOSDAC adapter | All | **blocked on account** | medium |

**Seven of ten need no new credentials at all.**

---

## Part 5 — Credentials

| # | Item | Unblocks | Note |
|---|---|---|---|
| 1 | **MOSDAC password reset** | The whole MOSDAC track | Not a new application. The lock reads as a Keycloak brute-force trip and the login page offers a reset link. |
| 2 | **Protected Planet (WDPA) token** | MPA / ESZ geofencing (#8) | Free signup at protectedplanet.net |
| 3 | IMD API key + egress IP whitelist | **Live** cyclone warnings | IBTrACS gives climatology; only IMD gives a live warning for Indian waters |
| 4 | OpenRouter credit (~$10) | LLM chain reliability | Currently on a free slug that could vanish mid-demo |

---

## What I am deliberately not proposing

- **A separate dashboard per role.** The PS names five stakeholders; five
  dashboards is five half-built screens. The harbour board serves authorities and
  disaster management from one view, and the researcher workspace already exists.
- **Live cyclone tracking from IBTrACS.** It is a best-track archive. Presenting
  hindsight as a live warning during a cyclone is the worst thing this system
  could do.
- **Storm-surge modelling.** Genuinely valuable and genuinely out of scope — it
  needs validated bathymetry, a hydrodynamic model and expert calibration. A
  half-built surge estimate near a coastline is dangerous.
- **CMFRI catch integration.** I could not find machine-readable landings data.
  Claiming a fisheries-productivity capability without it would be inventing the
  evidence base.
