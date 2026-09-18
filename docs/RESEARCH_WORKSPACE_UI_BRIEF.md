# ORCA Researcher Workspace — UI redesign brief (for ChatGPT)

Copy everything below into ChatGPT. Ask it for **layout, typography, and component ideas only**. Do **not** ask it to invent new APIs, ranking formulas, or datasets.

---

## 1. Your job

Redesign **only** the Researcher Workspace UI so it looks like a real research product (NASA Earthdata / Copernicus / ERDDAP-style), **not** like an AI demo with nested cards and manifesto paragraphs.

Do **not** change:

- Ranking / matching logic
- Catalogue contents
- Backend routes
- The rest of the ORCA map/chat app
- Safety / GO-NO-GO

Do change:

- Layout, spacing, hierarchy, copy length
- How results, parse, and context are **presented**

---

## 2. Product (one sentence)

ORCA is an Indian coastal marine console. Fishermen get a GO/CAUTION/NO-GO card on a map. **Researchers** get a full-screen workspace to **find real datasets** (SST, chlorophyll, winds, PFZ, …), **see how the question was parsed**, **download a CSV/Excel**, **compare satellite SST to RAMA buoys**, and **see FrontCast model status**.

---

## 3. Files you may restyle (frontend only)

| File | Role |
|------|------|
| `frontend/src/components/ResearcherWorkspace.tsx` | Overlay, tabs, Discover, Catalogue list, Ground truth, Models |
| `frontend/src/components/DatasetBuilder.tsx` | Build-a-spreadsheet tab |
| `frontend/src/App.tsx` | Only `researchOpen && <ResearcherWorkspace />` — do not restyle the map/chat |

Stack: React 19, Tailwind v4, lucide-react, existing tokens in `frontend/src/styles/tokens.css` (abyss-0/1/2, cyan accent, ink-0..3, hairline, Inter + JetBrains Mono). Keep this palette so the overlay still feels like ORCA, not a random SaaS.

---

## 4. What the UI looks like TODAY (the problem)

Full-screen overlay (`fixed inset-0 z-50`), **max-width ~5xl centered column**, tabs in the **header** (discover / catalogue / build a dataset / ground truth / models).

Pain:

1. **Box in a box in a box** — every section is `rounded border border-hairline`. Parse box, then a stack of `article` cards, each with another inner bordered block for caveats.
2. **Essay copy** — long “how we thought about this” paragraphs inside the UI (amber caveats, architecture lectures on Models, RAMA lecture on Ground truth).
3. **Tiny type everywhere** (`text-2xs`) — reads as a debug panel, not a product.
4. **No spatial context** — bbox is shown as `78.0, 5.0, 95.0, 23.0` chips, no map.
5. **No visual identity per dataset** — title + chips only; no thumbnail, no density of information like a data catalogue.
6. Discover **score** shown as `5.8` / `2.8` (raw matcher points). Mockups use 0–1 “Relevance”.
7. Sparkles icon on “find data”; uppercase LABEL chrome.

The user attached a **target direction** (not a pixel-perfect spec): left sidebar, top search, “Interpreted query” chips in one row, **flat result list** with a small ocean thumbnail, metadata line (resolution · cadence · coverage), **Relevance 0.95**, right **Query context** panel with a map + region + time + “Why these results” (3 short bullets) + a timeline.

Treat that screenshot as **inspiration**. You may propose something **simpler and better** if nested panels would still look fake. Prefer **one** sidebar + **one** list + **one** context column over more cards.

---

## 5. Tabs / information architecture (keep these five jobs)

You may **rename labels** and **move chrome** (e.g. sidebar vs top tabs). Do not drop a job.

| Current tab | What it does |
|-------------|--------------|
| Discover | Natural-language search → parsed intent + ranked matches + optional federated ERDDAP hits |
| Catalogue | All ~15 curated datasets ORCA has actually integrated |
| Build a dataset | Pick variables + region + dates → preview table → CSV/Excel |
| Ground truth | RAMA buoy vs MUR SST (bias, RMSE, matched days). Sparse, ~1 month lag |
| Models | FrontCast status. Often **not available** (no PyTorch). Must not look like a live model if `ready === false` |

Suggested IA if you improve it:

- **Discover** (default)
- **Catalogue**
- **Collections / Build** (today’s “build a dataset”)
- **Validate** (today’s “ground truth”)
- **Models** (collapse to a status line if not loaded)

“Jobs / Saved searches / API Access / My Datasets” in the mockup **do not exist**. Do not invent fake nav items that go nowhere. If you want a sidebar, only real destinations.

---

## 6. APIs — keep these contracts

### Discover

`POST /api/research/discover` body `{ "question": string, "limit"?: 8 }`

Returns (simplified):

```ts
{
  question: string
  parsed_by: "llm+heuristic" | "heuristic"
  note: string
  intent: {
    variables: string[]
    bbox: [west, south, east, north] | null
    place: string | null
    start: string | null   // ISO date
    end: string | null
    kinds: string[]
    assumptions: string[]  // e.g. monsoon = Jun–Sep
  }
  matches: ResearchDataset[]  // curated, scored
  federated: {
    servers_queried: number
    servers_responding: number
    found: number
    datasets: FederatedDataset[]
    note: string
  } | null
  snippet: string | null  // python snippet for top match
}
```

### Catalogue

`GET /api/research/catalogue` → `{ datasets: ResearchDataset[], count, note, regions }`

### Export one curated dataset

`GET /api/research/export?dataset=mur_sst&west=&south=&east=&north=&start=&format=csv`

### Build spreadsheet

`GET /api/research/build/variables` → `{ variables: string[] }`

`POST /api/research/build` body:

```ts
{
  variables: string[]
  west, south, east, north: number
  start: string   // "YYYY-MM-DD" or similar
  end: string     // date or "today"
  points: 1 | 9   // 1 = centre; else 3×3
  step_days: number
  format: "json" | "csv" | "xlsx"
}
```

JSON preview: `{ rows, summary: { rows, empty_columns, datasets, missing_by_variable, ... } }`

### Ground truth

`GET /api/research/insitu/buoys?west=60&south=0&east=100&north=25`

`GET /api/research/insitu/validate-sst?lat=&lon=&days=30`

### Models

`GET /api/ml/models` → `{ models: [ { version, torch_installed, weights_present, ready, load_error, history_days, lead_days, label_source, not_a_verdict, training_report } ] }`

If `ready === false`, UI must say the model **cannot run here**. Do not fake skill numbers.

---

## 7. Dataset object (what each list row can show)

```ts
{
  id: string
  title: string
  provider: string
  kind: "observation" | "reanalysis" | "forecast" | "derived" | "model"
  variables: { name, unit, description }[]
  resolution_deg: number          // as ORCA SERVES it
  native_resolution: string       // e.g. "0.01 deg (~1 km)"
  cadence: string                 // daily / monthly / hourly
  coverage: string
  access: "open" | "free-signup" | "credentialed" | "unavailable"
  licence: string
  endpoint: string
  caveats: string                 // important — show on expand, not as a wall
  servable: boolean               // ORCA can subset (Integrated)
  score?: number                  // matcher points, typically ~0.1–8, NOT 0–1
  why?: string[]                  // short reasons; often repetitive — you may rewrite DISPLAY of why, not invent new science
}
```

**Relevance display:** you MAY map `score` to a 0–1 bar for UI (`score / maxScoreOnThisPage`) as **presentation**. Do **not** claim it is a neural ranker. Label it “Match” or “Relevance (keyword match)” if you show a number.

**Thumbnails:** the API does **not** return images. Options you may propose:

- CSS/SVG abstract swatches by `kind` / `id` (sst = warm ramp, chl = green-blue) — honest as decoration
- If `id` is `mur_sst` / `esacci_chl_monthly` / `orca_pfz`, optionally `/api/rasters/<variable>/latest.png` **when that ingest exists** — may be missing
- Do **not** use random Unsplash/ocean photos (looks fake)

**Federated rows** are **not reviewed**. They must look visually quieter than curated “ORCA serves this” rows. Do not mix them into one list without a divider.

---

## 8. Honest science the UI must not hide (short, not essays)

- Monthly chlorophyll ≠ daily bloom.
- Several INCOIS products are **archives** (coverage ended).
- ORCA grid is often **coarser** than native 1 km.
- RAMA validates **track record**, not today’s SST (~month lag, few stations).
- FrontCast does not affect safety verdicts.
- Discover score is **not** scientific importance.

Show these as **one-line metadata or an expand row**, not amber paragraphs on every card.

---

## 9. What the attached mockup got right (use)

- Left **nav** instead of header tab soup
- Single **search** in the content header + Find data
- **Interpreted query** as a compact chip row (variable, place, dates, kind)
- Results as a **list**, not nested articles
- Right **context**: region name, bbox as human text, selected variables, short “why these results”
- “Integrated” instead of “ORCA serves this”

## What the mockup got wrong / we cannot ship blindly

- Fake **Collections / Jobs / Saved searches / API Access** unless you only show current tabs
- **Thumbnails** that look like unique satellite scenes for every row — we don’t have 15 unique images
- Relevance **0.95, 0.88…** as if ML ranking — our scores are keyword overlap
- “Why these results” numbered as monsoon-science claims — keep why tied to `intent` + `why[]` (serves chlorophyll, covers box, etc.)
- User profile “Mohd Hammad Ansari / Researcher” — optional dummy; not in the app
- Extra datasets in the screenshot (e.g. “ORCA upwelling indices”) — **only render catalogue/matches from the API**
- A second nested “Interpreted query” **and** a heavy right card **and** filters **and** sort — still a lot of boxes. Prefer fewer surfaces.

---

## 10. Design constraints (anti-AI-slop)

Please propose UI that avoids:

- Nested rounded bordered cards more than **one** level
- Manifesto / README prose in the UI
- Decorative gradients, glow, sparkles
- Rainbow chips
- Tiny 10px body text for titles
- Every row repeating the same caveat paragraph
- Fake empty states (“Your AI is thinking…”)

Prefer:

- Flat list, 12–16px titles, 13px meta
- Hairline **dividers** instead of boxes
- One accent (cyan) for primary button and active nav
- Map **once** in the context column (bbox rectangle on a simple dark geo or static India outline) — optional; bbox text is enough for v1
- Expand **one** row at a time for caveats, licence, endpoint, Download CSV, copy citation

---

## 11. Deliverable I want from you (ChatGPT)

Please return:

1. **Recommended layout** (sidebar / main / context) as a simple wireframe in ASCII or nested lists.
2. **Per-tab layout** (Discover, Catalogue, Build, Ground truth, Models) — what stays on screen vs expand.
3. **Copy rewrite** — max 1 line for header, 1 line for parse, 1 line caveat on expand. Example strings.
4. **What to delete** from current ResearcherWorkspace (essay blocks, nested wrappers).
5. **Component list** for the implementer: `WorkspaceShell`, `SideNav`, `SearchBar`, `InterpretedQuery`, `DatasetRow`, `ContextPanel`, `BuildForm`, `BuoyStats` — no new backend.
6. **Thumbnail strategy** that is honest.
7. **How to show score** without lying.
8. Optional: a **simpler** layout than the screenshot if you think the screenshot still looks “dashboard-AI”.

Do **not** output a full rewritten 900-line TSX unless asked. Ideas + structure first.

---

## 12. Current Discover empty / filled states

Empty: search + 5 example queries as pills.

Filled: “How ORCA read your question” bordered box (chips + assumptions list + note paragraph) then “N matches” then stacked DatasetCards then federated section.

Build: variable chips, area presets (Kerala / Tamil Nadu / Arabian Sea / Bay of Bengal), date range, centre vs lattice, preview table.

Ground truth: intro paragraphs, buoy chips, then a bordered stats grid.

Models: architecture paragraph, amber “PyTorch not installed”, optional metrics table, nested “Where labels come from / What it cannot do”.

---

## 13. Example question (for your mock wireframe)

User types: `chlorophyll blooms in the Bay of Bengal during the monsoon`

Typical intent:

- variables: chlorophyll (and matcher may still surface SST, winds, derived fronts/PFZ)
- place: Bay of Bengal
- bbox: ~ 78, 5, 95, 23
- start/end: Jun–Sep of current year (assumption shown)
- parsed_by: llm+heuristic

Top match is usually ESA CCI chlorophyll monthly (servable). Lower matches include INCOIS archives and ORCA derived fronts/PFZ.

---

End of brief.
