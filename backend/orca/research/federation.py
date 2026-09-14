"""Federated dataset search across the public ERDDAP network.

## Why federate rather than curate

ORCA's hand-written catalogue holds fifteen datasets. That is the right size for
a list somebody checked — every row carries caveats a person wrote — and it is
the wrong size for a researcher, who needs whatever exists for their question,
not whatever we happened to integrate.

ERDDAP solves this. It is the de-facto standard server for oceanographic data,
roughly a hundred institutions run one, and every instance exposes the same
machine-readable search endpoint::

    {server}/erddap/search/advanced.json
        ?searchFor=chlorophyll&protocol=griddap
        &minLon=60&maxLon=100&minLat=0&maxLat=25
        &minTime=2025-01-01&maxTime=2025-12-31

The bounding box and the time range are applied **server-side**, so a search for
"chlorophyll over the Indian EEZ in 2025" returns datasets that genuinely cover
that box and that period rather than everything with the word in its title.
Measured against eleven reachable servers, one broad query reaches roughly six
thousand datasets, with no key and no registration.

## What this does NOT do

It does not copy anybody's data. ORCA stores **no** federated dataset: a search
returns identifiers and subsetting URLs, and the optional preview pulls a few
dozen cells so a researcher can see the shape and the units before committing to
a download. Mirroring other institutions' archives would be a licensing problem,
a storage problem, and a staleness problem, and it would make ORCA the stale copy
of a live product.

## Honesty rules carried over from the curated catalogue

A federated result is clearly marked as **not reviewed by us**. The curated
entries carry caveats a human checked; these carry the provider's own summary and
nothing more. Presenting the two identically would imply a level of vetting that
does not exist, so they are tagged, sorted and rendered differently.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from orca.sources.tls import ssl_context

log = logging.getLogger(__name__)

FEDERATION_VERSION = "orca-erddap-federation-2026.09"


@dataclass(frozen=True, slots=True)
class ErddapServer:
    """One public ERDDAP instance."""

    key: str
    name: str
    base: str
    #: Roughly where this server's holdings are centred. Used to explain a result
    #: ("a regional server for the North Atlantic"), never to filter — a global
    #: product served from Ireland is still global.
    focus: str
    region_hint: str = "global"


#: Verified reachable on 2026-09-14 by calling `search/advanced.json` on each.
#: Servers that failed the probe are listed in UNREACHABLE below rather than
#: deleted, so the next person does not spend the afternoon rediscovering it.
SERVERS: tuple[ErddapServer, ...] = (
    ErddapServer(
        "coastwatch_west",
        "NOAA CoastWatch West Coast",
        "https://coastwatch.pfeg.noaa.gov/erddap",
        "Satellite ocean colour, SST, winds, currents",
    ),
    ErddapServer(
        "coastwatch",
        "NOAA CoastWatch",
        "https://coastwatch.noaa.gov/erddap",
        "Operational satellite products",
    ),
    ErddapServer(
        "incois",
        "INCOIS (India)",
        "https://erddap.incois.gov.in/erddap",
        "Indian Ocean SST, winds, Argo, Oceansat",
        region_hint="Indian Ocean",
    ),
    ErddapServer(
        "ncei",
        "NOAA NCEI",
        "https://www.ncei.noaa.gov/erddap",
        "Archived climate and ocean records",
    ),
    ErddapServer(
        "ifremer",
        "IFREMER (France)",
        "https://www.ifremer.fr/erddap",
        "Argo floats, European seas, moorings",
    ),
    ErddapServer(
        "emodnet",
        "EMODnet Physics",
        "https://erddap.emodnet-physics.eu/erddap",
        "European in-situ physical observations",
        region_hint="European seas",
    ),
    ErddapServer(
        "marine_ie",
        "Marine Institute (Ireland)",
        "https://erddap.marine.ie/erddap",
        "North Atlantic buoys and models",
        region_hint="North Atlantic",
    ),
    ErddapServer(
        "pacioos",
        "PacIOOS (Hawaii)",
        "https://pae-paha.pacioos.hawaii.edu/erddap",
        "Pacific islands, reefs, wave models",
        region_hint="Pacific",
    ),
    ErddapServer(
        "ooi",
        "Ocean Observatories Initiative",
        "https://erddap.dataexplorer.oceanobservatories.org/erddap",
        "Cabled observatory and glider time series",
    ),
    ErddapServer(
        "bcodmo",
        "BCO-DMO",
        "https://erddap.bco-dmo.org/erddap",
        "Biological and chemical oceanography from funded projects",
    ),
    ErddapServer(
        "secoora",
        "SECOORA",
        "https://erddap.secoora.org/erddap",
        "US south-east coastal observing",
        region_hint="US South Atlantic",
    ),
)

#: Probed and not usable on 2026-09-14. Recorded so the failure is documented
#: rather than rediscovered: an empty result from a server nobody can reach looks
#: identical to an empty result from a server with nothing to offer.
UNREACHABLE: dict[str, str] = {
    "https://erddap.aodn.org.au/erddap": "connection refused (IMOS Australia)",
    "https://tds.marine.rutgers.edu/erddap": "connection timeout",
    "https://www.marine.csiro.au/erddap": "HTTP 404 — moved or retired",
    "https://www.neracoos.org/erddap": "HTTP 403 — blocks unknown clients",
    "https://erddap.sensors.ioos.us/erddap": "read timeout (Axiom)",
    "https://rsg.pml.ac.uk/erddap": "search endpoint returns HTML, not JSON",
}

BY_KEY = {server.key: server for server in SERVERS}

#: Per-server timeout. Deliberately short: a federated search is only as fast as
#: its slowest member, and a researcher waiting thirty seconds for one sluggish
#: institution has been served badly by the other ten.
SERVER_TIMEOUT_S = 12.0

#: Results requested per server. The merge trims to the caller's limit; asking
#: for a handful from each keeps the payload small and the ranking meaningful.
PER_SERVER = 25


@dataclass(slots=True)
class FederatedDataset:
    """A dataset found on somebody else's server."""

    dataset_id: str
    title: str
    institution: str
    summary: str
    server_key: str
    server_name: str
    protocol: str
    #: The base griddap/tabledap URL. Append `.csv?...` to subset it.
    url: str
    info_url: str
    #: True when the row came back under a bbox/time constrained query, so the
    #: server itself asserted coverage rather than us inferring it from words.
    coverage_checked: bool
    #: Other servers found holding the same dataset id. A researcher whose
    #: nearest mirror is down still wants to know where else it lives.
    also_on: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        return {
            "id": f"{self.server_key}:{self.dataset_id}",
            "dataset_id": self.dataset_id,
            "title": self.title,
            "provider": self.institution or self.server_name,
            "server": self.server_name,
            "server_key": self.server_key,
            "protocol": self.protocol,
            "summary": self.summary[:600],
            "endpoint": self.url,
            "info": self.info_url,
            "coverage_checked": self.coverage_checked,
            "also_on": list(self.also_on),
            "curated": False,
            "caveats": (
                "Found by federated search and NOT reviewed by ORCA. The summary is the "
                "provider's own. Check the dataset's own metadata page for units, quality flags "
                "and licence before using it — ORCA vouches for the curated entries only."
            ),
        }


def _search_params(
    *,
    terms: str,
    protocol: str,
    bbox: tuple[float, float, float, float] | None,
    start: str | None,
    end: str | None,
    limit: int,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "searchFor": terms,
        "protocol": protocol,
        "page": 1,
        "itemsPerPage": limit,
    }
    if bbox:
        west, south, east, north = bbox
        params |= {"minLon": west, "maxLon": east, "minLat": south, "maxLat": north}
    # ERDDAP wants both ends or neither; sending one silently returns everything.
    if start and end:
        params |= {"minTime": start, "maxTime": end}
    return params


async def _search_one(
    client: httpx.AsyncClient,
    server: ErddapServer,
    *,
    terms: str,
    protocol: str,
    bbox: tuple[float, float, float, float] | None,
    start: str | None,
    end: str | None,
    limit: int,
) -> list[FederatedDataset]:
    url = f"{server.base}/search/advanced.json"
    params = _search_params(
        terms=terms, protocol=protocol, bbox=bbox, start=start, end=end, limit=limit
    )
    try:
        response = await client.get(url, params=params, timeout=SERVER_TIMEOUT_S)
    except (httpx.HTTPError, OSError) as exc:
        log.info("federation: %s unreachable (%s)", server.key, type(exc).__name__)
        return []

    # 404 is ERDDAP's "nothing matched", not an error. Treating it as a failure
    # would make an empty result look like a broken server.
    if response.status_code in (404, 500):
        return []
    if response.status_code != 200:
        log.info("federation: %s returned HTTP %s", server.key, response.status_code)
        return []

    try:
        table = response.json()["table"]
        columns = table["columnNames"]
        rows = table["rows"]
    except (ValueError, KeyError, TypeError):
        # Some instances serve an HTML error page with a 200. Not fatal.
        log.info("federation: %s returned an unparseable search payload", server.key)
        return []

    found: list[FederatedDataset] = []
    for row in rows:
        record = dict(zip(columns, row, strict=False))
        dataset_id = str(record.get("Dataset ID") or "").strip()
        if not dataset_id:
            continue
        endpoint = str(record.get(protocol) or "").strip()
        if not endpoint:
            continue
        found.append(
            FederatedDataset(
                dataset_id=dataset_id,
                title=str(record.get("Title") or dataset_id).strip(),
                institution=str(record.get("Institution") or "").strip(),
                summary=str(record.get("Summary") or "").strip(),
                server_key=server.key,
                server_name=server.name,
                protocol=protocol,
                url=endpoint,
                info_url=str(record.get("Info") or f"{server.base}/info/{dataset_id}/index.html"),
                coverage_checked=bool(bbox) or bool(start and end),
            )
        )
    return found


async def search(
    *,
    terms: str,
    bbox: tuple[float, float, float, float] | None = None,
    start: str | None = None,
    end: str | None = None,
    protocols: tuple[str, ...] = ("griddap", "tabledap"),
    servers: tuple[ErddapServer, ...] = SERVERS,
    limit: int = 40,
) -> dict[str, Any]:
    """Search every server at once and merge.

    Concurrent on purpose. Eleven servers queried in series at up to twelve
    seconds each is a two-minute wait; queried together it is as slow as the
    slowest one, and a server that times out simply contributes nothing.
    """
    if not terms.strip():
        terms = "ocean"

    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=ssl_context(),
        headers={"User-Agent": "ORCA/0.1 (marine decision support; federated ERDDAP search)"},
    ) as client:
        jobs = [
            _search_one(
                client,
                server,
                terms=terms,
                protocol=protocol,
                bbox=bbox,
                start=start,
                end=end,
                limit=PER_SERVER,
            )
            for server in servers
            for protocol in protocols
        ]
        batches = await asyncio.gather(*jobs, return_exceptions=True)

    results: list[FederatedDataset] = []
    seen: dict[str, FederatedDataset] = {}
    mirrors: dict[str, list[str]] = {}
    reached: set[str] = set()
    for batch in batches:
        if isinstance(batch, BaseException):
            continue
        for item in batch:
            reached.add(item.server_key)
            # Deduplicated by dataset id ACROSS servers, not within one. NOAA
            # mirrors the same products on its west-coast and main instances, so
            # a per-server key let identical rows fill the first page twice and
            # pushed genuinely different datasets off it. The other servers
            # holding a copy are recorded instead of discarded, because a
            # researcher whose nearest mirror is down still wants the alternative.
            if item.dataset_id in seen:
                mirrors.setdefault(item.dataset_id, []).append(item.server_name)
                continue
            seen[item.dataset_id] = item
            results.append(item)

    for item in results:
        item.also_on = tuple(dict.fromkeys(mirrors.get(item.dataset_id, ())))

    ranked = _rank(results, terms=terms, region_bbox=bbox)
    return {
        "federation_version": FEDERATION_VERSION,
        "servers_queried": len(servers),
        "servers_responding": len(reached),
        "found": len(ranked),
        "datasets": [item.describe() for item in ranked[:limit]],
        "note": (
            f"Searched {len(servers)} public ERDDAP servers concurrently. Bounding box and date "
            "range were applied server-side where given, so these datasets assert coverage of "
            "your area and period rather than merely mentioning it. ORCA stores none of this "
            "data — these are identifiers and subsetting URLs on the providers' own servers."
        ),
    }


def _rank(
    items: list[FederatedDataset],
    *,
    terms: str,
    region_bbox: tuple[float, float, float, float] | None,
) -> list[FederatedDataset]:
    """Order results so an Indian-Ocean question surfaces Indian-Ocean holdings.

    Deterministic, like the curated matcher: a model never orders these.
    """
    words = [w for w in terms.lower().split() if len(w) > 2]
    indian = bool(region_bbox) and region_bbox[0] < 100 and region_bbox[2] > 60

    def score(item: FederatedDataset) -> float:
        value = 0.0
        haystack = f"{item.title} {item.summary}".lower()
        value += sum(2.0 for word in words if word in item.title.lower())
        value += sum(0.5 for word in words if word in haystack)
        if item.coverage_checked:
            value += 1.0
        # A regional server is more likely to hold the high-resolution product
        # for its own waters than a global archive is.
        server = BY_KEY.get(item.server_key)
        if server and indian and server.region_hint == "Indian Ocean":
            value += 3.0
        if item.protocol == "griddap":
            value += 0.4
        return value

    return sorted(items, key=lambda i: (-score(i), i.title))


# ------------------------------------------------------------------- preview


#: Cells a preview may return. Small on purpose — a preview exists to show shape
#: and units, not to become a download path that quietly mirrors a provider.
PREVIEW_MAX_ROWS = 60


def _griddap_selector(dds: str) -> tuple[str, str] | None:
    """Build a valid griddap subset selector from the dataset's own DDS.

    Returns ``(variable, selector)`` or None.

    This exists because griddap has no equivalent of "just give me a few rows".
    Every axis needs an explicit subscript, and a ``.csv`` with none is rejected
    outright — which is what made the first version of this preview apologise on
    every gridded dataset instead of working. The axis names and sizes differ per
    product (some carry a singleton ``altitude`` between time and latitude), so
    they are read rather than assumed.
    """
    import re

    # The gridded variable and its axes, e.g.
    #   Float32 chlor_a[time = 3151][altitude = 1][latitude = 8640][longitude = 17280];
    match = re.search(r"ARRAY:\s+\w+\s+(\w+)((?:\[[^\]]+\])+)", dds)
    if not match:
        return None
    variable = match.group(1)
    axes = re.findall(r"\[\s*(\w+)\s*=\s*(\d+)\s*\]", match.group(2))
    if not axes:
        return None

    parts: list[str] = []
    for name, size_text in axes:
        size = int(size_text)
        last = max(size - 1, 0)
        lowered = name.lower()
        if lowered.startswith("time"):
            # Only the most recent step. A preview of a 3000-step archive should
            # not be a decision about which decade to show.
            parts.append(f"[{last}]")
        elif size <= 1:
            parts.append("[0]")
        else:
            # Sample the MIDDLE third of each spatial axis, not from index 0.
            # Index 0 on a global grid is the pole or the dateline, so a preview
            # starting there came back as a column of NaN — technically correct
            # and completely useless for judging whether a dataset is worth
            # downloading. Roughly five samples: enough to show shape and units,
            # small enough that the provider does not notice.
            start = size // 3
            stop = min(last, (2 * size) // 3)
            span = max(stop - start, 1)
            stride = max(1, span // 5)
            parts.append(f"[{start}:{stride}:{stop}]")
    return variable, "".join(parts)


async def preview(
    *,
    server_key: str,
    dataset_id: str,
    protocol: str = "griddap",
    rows: int = PREVIEW_MAX_ROWS,
) -> dict[str, Any]:
    """Pull a handful of real values so a researcher can see what they would get.

    Nothing is stored. This is a pass-through to the provider with a hard row
    cap, which is what keeps ORCA an index rather than an unauthorised mirror of
    somebody else's archive.
    """
    server = BY_KEY.get(server_key)
    if server is None:
        return {"ok": False, "error": f"unknown server {server_key!r}"}

    rows = max(1, min(rows, PREVIEW_MAX_ROWS))
    base = f"{server.base}/{protocol}/{dataset_id}"

    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=ssl_context(),
        headers={"User-Agent": "ORCA/0.1 (dataset preview)"},
    ) as client:
        if protocol == "tabledap":
            # tabledap honours a row limit directly, which is exactly what a
            # preview wants and what griddap has no equivalent of.
            url = f"{base}.csv?&orderByLimit(%22{rows}%22)"
        else:
            # Read the structure first: guessing a variable name and a subscript
            # produces an ERDDAP error that reads like a bad bounding box.
            try:
                dds = await client.get(f"{base}.dds", timeout=SERVER_TIMEOUT_S)
            except (httpx.HTTPError, OSError) as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if dds.status_code == 429:
                return {"ok": False, "error": "HTTP 429", "detail": _RATE_LIMITED}
            if dds.status_code != 200:
                return {"ok": False, "error": f"structure lookup HTTP {dds.status_code}"}

            built = _griddap_selector(dds.text)
            if built is None:
                return {
                    "ok": False,
                    "error": "could not read the dataset's axes from its DDS",
                    "hint": "Open the provider metadata page and build the selector there.",
                }
            variable, selector = built
            url = f"{base}.csv?{variable}{selector}".replace("[", "%5B").replace("]", "%5D")

        try:
            response = await client.get(url, timeout=SERVER_TIMEOUT_S * 2)
        except (httpx.HTTPError, OSError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "attempted": url}

    if response.status_code == 429:
        return {"ok": False, "error": "HTTP 429", "detail": _RATE_LIMITED, "attempted": url}
    if response.status_code != 200:
        return {
            "ok": False,
            "error": f"HTTP {response.status_code}",
            "attempted": url,
            "detail": response.text[:220],
        }

    lines = response.text.splitlines()[: rows + 2]
    return {
        "ok": True,
        "server": server.name,
        "dataset_id": dataset_id,
        "protocol": protocol,
        "url": url,
        "rows_returned": max(0, len(lines) - 2),
        "csv": "\n".join(lines),
        "note": (
            f"A sample of at most {PREVIEW_MAX_ROWS} rows, fetched live from {server.name} and "
            "not stored by ORCA. The first two CSV lines are column names and units."
        ),
    }


#: Said in full rather than as a bare status code: a 429 from a public research
#: server is that institution asking us to slow down, and the right response is
#: to wait rather than to retry harder.
_RATE_LIMITED = (
    "The provider is rate-limiting requests. That is their server asking us to slow down, "
    "not a fault — wait a moment and try again, or fetch it directly with the endpoint above."
)


def servers_report() -> dict[str, Any]:
    """What the federation covers, and what it is known not to reach."""
    return {
        "federation_version": FEDERATION_VERSION,
        "servers": [
            {
                "key": s.key,
                "name": s.name,
                "base": s.base,
                "focus": s.focus,
                "region_hint": s.region_hint,
            }
            for s in SERVERS
        ],
        "unreachable": [{"base": base, "reason": why} for base, why in UNREACHABLE.items()],
        "note": (
            "Servers are probed and recorded rather than assumed. An empty result from a server "
            "nobody can reach looks identical to an empty result from a server with nothing to "
            "offer, so the ones that failed are listed with the reason."
        ),
    }
