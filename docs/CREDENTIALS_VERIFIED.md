# ORCA — credential & endpoint verification

Every credential you supplied was tested with a live request on **2026-08-30**
(second batch, including the Supabase database password, verified the same day).
This file records what works, what needs a correction, and what is still missing.
No secret values are recorded here — they live in `.env` (git-ignored).

## Verified working

| Service | Test performed | Result | Notes for the build |
|---|---|---|---|
| **Groq** | `GET /openai/v1/models` | ✅ 200 — 14 models | `openai/gpt-oss-120b` (primary reasoning), `openai/gpt-oss-20b`, `groq/compound` (agentic), `whisper-large-v3` + `-turbo` (ASR), `qwen/qwen3.8-27b` |
| **Google Gemini** | `GET /v1beta/models` | ✅ 200 | Key is the **new `AQ.` format** — must be sent as the `x-goog-api-key` header, *not* `?key=`. `gemini-2.5-flash` + `gemini-2.5-pro`, 1 048 576-token context, `thinking: true` |
| **Copernicus Marine (CMEMS)** | OAuth password grant against `auth.marine.copernicus.eu/realms/MIS` | ✅ access_token issued | Username/password valid. `copernicusmarine` CLI and the raw OIDC flow both usable |
| **Copernicus Sentinel Hub (CDSE)** | `POST identity.dataspace.copernicus.eu/.../token`, client_credentials | ✅ access_token issued | Client id/secret valid |
| **NASA Earthdata (URS)** | `GET urs.earthdata.nasa.gov/api/users/tokens` (basic auth, `Accept: application/json`) | ✅ 200, `[]` | Credentials valid; **no EDL token generated yet** — we mint one on first ingest. Note `POST` to that path without `Accept: application/json` returns an HTML login page, which looks like a failure but is not |
| **Sarvam AI** | `POST /translate` en-IN → ta-IN | ✅ 200 | Header is `api-subscription-key`. Round-trip preserved the numeral: *"wave height 2.4 metres"* → *"அலை உயரம் 2.4 மீட்டர்"* — our number-integrity guard has a working primary |
| **Global Fishing Watch** | `GET /v3/datasets` | ✅ auth accepted (422 = missing query params, not an auth failure) | Token valid, expires 2036 |
| **AISStream** | WebSocket `wss://stream.aisstream.io/v0/stream` | ✅ `SubscriptionConfirmation` + live `PositionReport`s | See the caveat below |
| **Cesium ion** | `GET api.cesium.com/v1/me` | ✅ 200 | Token valid, audience `sih26` |
| **OpenRouter** | `GET /api/v1/key` | ✅ 200 | `is_free_tier: true` → **capped at 50 requests/day**. See action items |
| **Hugging Face** | `GET /api/whoami-v2` | ✅ 200 (`Soumyajit2005`) | Read access fine for AI4Bharat weights |
| **Supabase** | `GET /auth/v1/.well-known/jwks.json` | ✅ 200, ES256 key returned | Project is live and un-paused |
| **Supabase Postgres** | `asyncpg` connect + `create extension` + geodesic query | ✅ **PostgreSQL 17.6**, PostGIS **3.3.7** and pgvector **0.8.2** now installed | Reachable **only via the pooler** — see the correction below. `ST_Distance` on two points 0.009° apart returned **995.68 m**, i.e. true geodesic, so this is our reference oracle for `test_geo.py` |
| **Local Postgres (Docker)** | `docker compose up -d` → `psql` | ✅ **PostgreSQL 16.4 + PostGIS 3.4** healthy on `127.0.0.1:5433` | Offline/dev PostGIS target. `postgis/postgis:16-3.4-alpine` |
| **Local Redis (Docker)** | `redis-cli ping` | ✅ `PONG` on `127.0.0.1:6380` | `redis:7-alpine`, appendonly, 256 MB `allkeys-lru`. Optional accelerator only |

## Verified working — zero-auth data sources

| Source | Result |
|---|---|
| Open-Meteo **Marine** (`marine-api.open-meteo.com/v1/marine`) | ✅ 200, 1.5 s |
| Open-Meteo **Forecast** (`api.open-meteo.com/v1/forecast`) | ✅ 200, 0.9 s |
| **INCOIS ERDDAP** (`erddap.incois.gov.in`) | ✅ 200 — **15 griddap datasets** confirmed (see below) |
| NOAA CoastWatch ERDDAP | ✅ 200 |
| **NDMA SACHET** CAP RSS | ✅ 200 |
| **IMD** CAP mirror (`cap-sources.s3.amazonaws.com/in-imd-en/rss.xml`) | ✅ 200 |
| NASA **GIBS** WMTS capabilities | ✅ 200 |
| USGS earthquake GeoJSON | ✅ 200 |
| OpenFreeMap style (`tiles.openfreemap.org/styles/liberty`) | ✅ 200 |
| **Marine Regions** (`marineregions.org`, and `geo.vliz.be/geoserver/MarineRegions/wfs`) | ✅ 200 — the **WFS** endpoint is the better programmatic route for EEZ/IMBL than the download page |

### INCOIS ERDDAP griddap datasets actually available

This is the India-specific provenance the ISRO-side jury will look for, so the exact list matters:

```
incois_oceansat2_datasets            INCOIS Oceansat 2 OCM Data
IRS_chlorophyll_datasets             IRS P4 OCM-Chlorophyll
incois_tmi_3day_datasets             INCOIS TMI 3-Day SST
NOAA_AVHRR_AMSR_datasets             Daily-OI-V2 final (Ship, Buoy, AMSR-E, AVHRR, GSFC-ice)
AMSRE_MONTHLY_GLOBAL                 AMSR-E Monthly Global
ascat_daily_datasets / ascat_mnt_datasets      ASCAT global wind field (daily / monthly)
incois_quickscat_daily_datasets / _mnt_        QuikSCAT winds (daily / monthly)
incois_argo_sst_weekly               ARGO SST weekly
incois_argo_10day_McCreary / _10d_VAM          ARGO 10-day (two methodologies)
incois_argo_mnt_McCreary / _mnt_VAM            ARGO monthly (two methodologies)
incois_valueadded_products_datasets  INCOIS Value Added Products
```

## Corrections to the blueprint

| Item | Blueprint says | Reality | Fix |
|---|---|---|---|
| **Langfuse host** | `cloud.langfuse.com` | Your project is in the **US region**. EU host returns `401 Invalid credentials. Confirm that you've configured the correct host.` | Set `LANGFUSE_HOST=https://us.cloud.langfuse.com`. Verified: `GET /api/public/projects` → 200, project *"My Project"* under *"Soumyajit's Organization"* |
| **Gemini auth** | `GOOGLE_API_KEY` used as `?key=` | New `AQ.`-prefixed keys work via the `x-goog-api-key` **header** | Adapter sends the header |
| **Celestrak TLEs** | `celestrak.org/NORAD/elements/` | Host **resolves but does not connect** from this network (20 s timeout, both `.org` and `.com`) | Primary becomes `tle.ivanstanojevic.me/api/tle/{norad_id}` — verified 200, returns clean JSON with `line1`/`line2`. Celestrak stays as a secondary with a short timeout |
| **Bhuvan WMS** | `bhuvan-vec2.nrsc.gov.in/bhuvan/wms` | **Unreachable** from here (connection failure, not a 404) — exactly the flaky `.gov.in` TLS chain the blueprint warns about in C.6. `bhuvan-app1.nrsc.gov.in` responds 200 | Adapter written with a hard 8 s timeout and a graceful "source unreachable" provenance state, so a Bhuvan outage never blocks a response. Re-test from a normal laptop before declaring it broken |
| **AISStream coverage** | "Best free live AIS" | Auth and stream confirmed, but a 30 s subscription over the Indian-Ocean bbox (0–25 °N, 60–100 °E) yielded **zero** position reports; a global bbox delivered messages immediately (Sweden, Netherlands, Norway, Slovakia). The receiver network is Europe-heavy | Subscribe to the real Indian-Ocean bbox *and* run a deterministic simulated fleet for the demo, each tagged with its own provenance state (`live` vs `simulated`). Never mix them silently |
| **Supabase DB host** | `postgresql://postgres:…@db.<ref>.supabase.co:5432` | `db.vxzifftwdgharyjgmwhs.supabase.co` **does not resolve** (`getaddrinfo` 11004) — this project has no direct IPv4 endpoint. The **pooler** works, and the region is **`ap-southeast-1`**, not the `ap-south-1` you would guess from an Indian project | `postgresql://postgres.<ref>:<pw>@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres`. Use port **5432** (session mode — asyncpg prepared statements work); port 6543 is transaction mode and would require `statement_cache_size=0`. `@` in the password must be `%40` |
| **Docker** | "daemon not running, no container runtime" | Daemon is now **up (server 28.1.1)** | PostGIS and Redis are now live options. They stay **optional accelerators** behind the repository/cache/queue interfaces — the SQLite + in-process path remains the default so the demo survives a laptop with Docker off |
| **OpenRouter quota** | "Buy \$10 of credits — otherwise 50 req/day" | Confirmed `is_free_tier: true`, so you are on the 50/day cap today | Buy \$10 before demo week, or keep OpenRouter strictly as the third fallback |

## Not supplied / not yet obtainable

| Item | Impact | Plan |
|---|---|---|
| **MOSDAC** username/password | No Oceansat-3 / INSAT-3D direct pull | Register now — manual approval takes days. Until then, INCOIS ERDDAP's Oceansat-2 OCM + IRS P4 chlorophyll carry the Indian-satellite provenance story, which is genuinely sufficient |
| **IMD API key** (+ egress IP whitelisting) | No fishermen / port / cyclone / lightning **JSON** | Register at https://api.imd.gov.in/public/register.php and whitelist the deploy host egress IP. The public HTML lists endpoints, not a stealable key. Until the key arrives, ORCA reads the IMD **CAP RSS** (verified 200) and will not invent an all-clear |
| **Bhashini** `userID` + `ulcaApiKey` | Third language stack unavailable | Sarvam (verified) is primary, self-hosted AI4Bharat is the offline fallback. Bhashini is additive |
| **data.gov.in** API key | No annual fisheries statistics | Cosmetic — one afternoon whenever the key arrives |
| **Cloudflare R2** credentials | No object store | Designed out: derived rasters are written to local disk and served by our own API. See the plan's raster decision |

## Action items for you

1. **Buy \$10 of OpenRouter credits** — lifts 50 → 1 000 requests/day. The only paid item, and it is the difference between surviving Q&A and not.
2. **Register for MOSDAC and the IMD API today** — both have multi-day human approval loops. Use the IIT KGP address for MOSDAC; whitelist the *deploy host's* egress IP for IMD, not your laptop's.
3. ~~Send the Supabase database password~~ — **received and verified 2026-08-30.** PostGIS + pgvector are installed on the hosted project.
4. Optional: **Bhashini/ULCA** registration, for the "we use the government's own language platform" line.
5. **Free space on C:** — 5.96 GB free, below the 15 GB target. Docker is holding **2.99 GB of unused images and 1.84 GB of unused volumes** unrelated to ORCA (`orca-*` excluded). `docker system prune -a --volumes` would reclaim ~4.8 GB but **destroys other projects' data**, so I have not run it. Say the word if that is safe on your machine.
