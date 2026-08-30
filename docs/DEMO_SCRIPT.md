# ORCA — eight-minute demo script

Written to be run **without a code change**, against live data, in this order.
Timings are what the run actually takes, not what would be convenient.

Two rules that shape the whole script:

1. **Every claim gets checked on screen.** ORCA's pitch is that its numbers are
   attributable and its verdicts are deterministic. So the demo does not assert
   that — it opens the panel that proves it.
2. **The refusals are the demo, not the failures.** ORCA saying "no safe passage,
   and here are the six cells that block it" is more convincing than any route it
   could draw, and "UNVERIFIABLE — this is not the same as safe" is the single
   most important thing on the screen. Do not skip past them.

---

## Before you start

```bash
# Backend
cd backend && ../.venv/Scripts/python.exe -m uvicorn orca.main:app --host 127.0.0.1 --port 8000

# Frontend
cd frontend && npm run dev
```

Then, in one browser tab: <http://127.0.0.1:5173>

Warm the caches once, a few minutes before presenting. Everything below works
cold, but the first raster fetch and the first earth texture are the only slow
moments in the run and there is no reason to spend them on stage:

```bash
curl -s -o /dev/null http://127.0.0.1:8000/imagery/earth/bluemarble.jpg
curl -s -o /dev/null http://127.0.0.1:8000/imagery/earth/nightlights.jpg
curl -s http://127.0.0.1:8000/rasters/catalogue | head -c 200
```

**Pick the demo coast on the day.** During the monsoon the Chennai corridor is
genuinely vetoed for lightning almost everywhere, which makes a fine honesty
story and a poor routing story. Check before you present:

```bash
curl -s "http://127.0.0.1:8000/forecast/point?lat=17.7&lon=83.35" \
  | python -c "import json,sys;d=json.load(sys.stdin)['evidence'];print({k:d[k]['value'] for k in ('wave_height','wind_speed','convective_energy')})"
```

CAPE under ~1500 J/kg gives you a GO somewhere. Visakhapatnam and the Gujarat
coast were clear when this was written; Chennai was not.

---

## 0:00 — 0:20 · The opening shot

Load the page. NASA's Blue Marble, city lights on the night side, an atmosphere
limb, descending into the Bay of Bengal.

> "That imagery is NASA GIBS, fetched live and cached by ORCA's own endpoint. The
> first pixel on the screen has a provenance, and so does everything after it."

Press **Escape** if you want to move on early. It plays once per session.

---

## 0:20 — 1:30 · A verdict, and what is under it

Click any sea point off the chosen coast.

Three things to point at, in this order:

1. **The verdict card.** "Decided by a deterministic rule engine — no language
   model", with the thresholds version (`orca-thresholds-2026.08`).
2. **The vetoes.** If there are any: "2 hard vetoes — these override the score".
   Say plainly that a veto is not a large number in a weighted sum; it is a
   separate mechanism, so no amount of good weather elsewhere can outvote an
   exceeded hard limit.
3. **The evidence panel.** Every value with its provider, its age, and its
   provenance state. Point at one `forecast +26 min` badge:

> "That is a forecast valid 26 minutes from now, not stale data. ORCA
> distinguishes lead time from staleness, because a value valid tomorrow at 06:00
> is fourteen hours 'old' by subtraction and perfectly current in fact."

Now drag the **vessel slider** from 8.2 m to 22 m. The verdict changes.

> "Same sea, different boat, different answer. The limits come from a cited table
> of five Indian fleet classes, not from a single generic threshold."

---

## 1:30 — 2:30 · Click land on purpose

Click somewhere inland, or a coverage gap.

> "**UNVERIFIABLE.** Not 'safe', not 'no data' — ORCA says it could not obtain
> enough to judge, names which inputs were missing, and tells you to confirm with
> your fisheries office or the coastal VHF channel. This is the most important
> screen in the product: a system that renders missing data as calm water is
> worse than no system."

Go back to a sea point before continuing.

---

## 2:30 — 4:00 · Ask it something compound, in Tamil, out loud

Set the reply language to **Tamil** and leave **speak** on.

Type (or press the mic and say):

> "நாளை காலை கடலுக்குப் போவது பாதுகாப்பானதா? மற்றும் மீன் எங்கே கிடைக்கும்?"
> *(Is it safe to go out tomorrow morning? And where will I find fish?)*

Narrate the trace as it lands — it arrives in this order and the order is the
point:

| Frame | Say |
|---|---|
| `decomposition` | "It split a compound question into two, labelled the intents, and the tools those intents require become a floor the planner may add to but not drop from." |
| `plan` | "The plan is published *before* anything runs." |
| `tool_result` ×5 | "Each tool with its measured latency, client-side." |
| `critic` | "A critic checks the draft against the rule engine's verdict. If it had softened a NO-GO, it would be rejected and you would see the round count." |
| `final` | "Both halves answered, in the order asked." |
| `translation` | "Tamil, with `figures intact · 18`. Every numeral, unit and verdict word was masked, re-injected verbatim, and verified as a multiset." |
| `audio` | Press play. |

If a clause stayed in English, do not gloss over it:

> "Two of eight clauses could not be verified, so they stayed in English rather
> than being translated unsafely. Every figure on the screen is still correct."

**The line worth landing:** Sarvam once rendered "NO-GO" into Tamil as
"punishment for those who go astray", and once rendered "14.7 kn" as "14.7
kilometres". Both are fluent, confident, and destroy the meaning. That is why
verdict words and confusable units are protected alongside the digits.

---

## 4:00 — 5:00 · The sea, at the size it actually is

Open **Sea view** and expand it.

> "Amplitude from the significant wave height, wavelength from the deep-water
> dispersion relation `L = gT²/2π` — so a 8.8 second swell is 120 metres long,
> which is why the domain is this big and why the boat looks that small. The boat
> is drawn to its real length overall. The red contour is your vessel class's
> wave-height limit. When a crest breaks through it, that is the veto, and it is
> the same comparison the rule engine made."

If the steepness note is showing, read it: the render is gentler than the sea and
the panel says so.

---

## 5:00 — 6:00 · Passage planning, including the refusal

**Safe passage** panel → **set** → click a destination → **plan the passage**.

If a route comes back: distance, duration, and the detour against the great
circle.

> "Five per cent longer than the direct line. Every cell on it was cleared by the
> same rule engine that produced the verdict — the router carries no thresholds of
> its own."

If it refuses — and for a small boat in the monsoon it often will — **that is the
better demo**:

> "No safe passage, and here is why: six cells the engine refuses, with the
> CAPE-derived lightning probability for each. A vetoed cell is impassable, not
> expensive. Encoding 'never' as a large cost is how routers end up sailing
> through a cyclone to save a day."

Note the land mask while you are here:

> "ORCA's land mask is the wave model's own silence. A cell the wave model
> declines to answer for is exactly the set a boat must not cross — and it means
> 'unroutable' and 'unmeasurable' are the same state, which is the honest
> position."

---

## 6:00 — 7:00 · Search and rescue

**SAR** → object class **Life raft with canopy, no drogue** → **12 h** →
**compute the search area**. The map frames the result.

> "Two thousand particles. Drift is surface current plus leeway, the IAMSAR
> formulation. Each particle carries its own leeway coefficients drawn from the
> class's measured spread and its own crosswind sign — because that sign flips
> unpredictably between individual objects of the same class, and that scatter is
> what makes a search area a cloud rather than an ellipse."

Point at the small line under the numbers:

> "That is the centre of the distribution, and it is deliberately small type.
> ORCA will not give you a predicted position. A coordinated search that
> concentrates on one point because software offered one is a worse outcome than
> no software at all."

And the error budget:

> "For a person in the water the area's size is set by the current field's own
> error, not by the object's leeway — a person has almost no sail area. ORCA says
> which term dominates, so a coordinator can judge whether a better current
> analysis would change their plan."

---

## 7:00 — 7:40 · An alert nobody asked for

**Alerts** → **watch this point**. Then change the vessel slider, and press
**check now**.

> "The watch was re-assessed and something crossed a line, so ORCA spoke without
> being asked. Note that the alert carries what it *was* as well as what it *is* —
> 'GO → NO-GO' is actionable, 'NO-GO' on its own is a status line the verdict card
> already shows."

> "Alerts fire on transitions only. A job that emits the current verdict every
> cycle produces a feed, and a feed is something people learn to ignore — which
> means the one alert that mattered scrolls past with the ninety that did not."

And read the footer:

> "Nothing is sent anywhere. ORCA has no authority to contact a fisherman."

---

## 7:40 — 8:00 · Treatments, and the machine-readable output

**Treatment** rail → **Thermal**, then **Radar**, then back to **Standard**.

> "Seven treatments, and one constraint that shaped all of them: ORCA's raster
> legends are generated from the same lookup tables that colour the pixels, so a
> colour *means* a temperature. A treatment breaks that, so while one is active
> the layer rail hides its legends rather than showing stops that no longer match
> the screen. The frame-rate guard turns a treatment off if it cannot hold the
> budget, rather than showing a number and doing nothing."

Finish on the left rail: **Download this advisory as CAP 1.2 XML**.

> "CAP 1.2, the OASIS standard NDMA SACHET and IMD already publish in, with a
> second `<info>` block in the local language. ORCA is built to feed the systems
> that exist, not to replace them."

---

## If something breaks on stage

| Symptom | Say this, then do this |
|---|---|
| A tool row goes red | "That is a live upstream, and it just failed — which is the point of showing them." The verdict and evidence panels are unaffected. |
| The agent answer is stiff and template-like | The LLM chain fell through to the deterministic renderer. `curl /agent/providers` shows which providers are alive. The verdict is unaffected — it never went through a model. |
| The globe is black | WebGL2 is missing or blocked. The console shows an explicit message, not a white screen. Use a different browser. |
| Rasters are old | Say the age out loud — it is on the badge. `POST /rasters/refresh` re-ingests, but do not do it mid-demo. |
| No route, anywhere, for any vessel | That is the monsoon. Show the blocking cells and their CAPE figures; it is a stronger story than a line on a map. |

## The one-sentence version

> ORCA answers a fisherman's question in his own language and out loud, shows
> every number's source and age, computes the go/no-go with a deterministic engine
> the model is not allowed to overrule, and says "I cannot tell" when it cannot —
> which is the only version of this product that is safe to ship.
