"""Reference maritime geography: EEZ boundaries and the IMBL treaty lines.

Marine Regions (VLIZ) is the authoritative open source, and its **WFS endpoint**
is the right programmatic route — the download page needs a browser and a
click-through. Verified working.

This is ``CURATED`` data, not a measurement: it is a hand-checked edition of a
boundary that changes only by treaty. It never goes stale on a clock, and the
freshness model treats it accordingly.

**Why it is cached to disk and committed simplified.** The full India EEZ is
71,782 vertices and 1.85 MB. Three consequences:

* the browser gets a simplified edition, because 72k vertices of coastline is
  invisible detail at any zoom the console uses and a wasted megabyte on a
  harbour Wi-Fi connection;
* the geofence engine keeps the *full* resolution, because a simplified boundary
  would put the line in the wrong place, and the whole point of the IMBL check is
  telling a fisherman which side of it they are on;
* both are written to ``data/static`` so demo day does not depend on VLIZ being
  reachable.

The India-Sri Lanka boundary is the one that matters most. It is indexed under
``territory1='Sri Lanka'``, not India, so a query filtered on India alone finds
Bangladesh and the baselines and silently misses it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from orca.config import get_settings
from orca.provenance import Citation, Provenance, Provider, utcnow
from orca.sources.base import Source

log = logging.getLogger(__name__)

WFS_BASE = "https://geo.vliz.be/geoserver/MarineRegions/wfs"

#: The Indian EEZ. MRGID is Marine Regions' stable identifier.
INDIA_EEZ_MRGID = 8480

CITATION = Citation(
    label="Marine Regions — Maritime Boundaries Geodatabase v12 (VLIZ)",
    provider=Provider.MARINE_REGIONS,
    url="https://marineregions.org/eez.php",
    identifier=f"mrgid:{INDIA_EEZ_MRGID}",
    quote=(
        "Flanders Marine Institute (2023). Maritime Boundaries Geodatabase: "
        "Exclusive Economic Zones, version 12."
    ),
)

#: Simplification tolerance in degrees for the browser edition. 0.005 deg is
#: ~550 m — well below the width of a line on screen at the console's zooms, and
#: it takes the India EEZ from 1.85 MB to something a phone can load.
DISPLAY_TOLERANCE_DEG = 0.005


@dataclass(frozen=True, slots=True)
class Fence:
    """One geofence: a named boundary with a jurisdictional meaning."""

    key: str
    name: str
    kind: str  # eez | imbl | baseline | mpa | trawl_ban | crz
    #: GeoJSON geometry, full resolution.
    geometry: dict[str, Any]
    #: What crossing it means, in words a fisherman would use. This string is
    #: what the advisory says, so it is written here rather than generated.
    consequence: str
    authority: str
    mrgid: int | None = None
    length_km: float | None = None
    area_km2: float | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "consequence": self.consequence,
            "authority": self.authority,
            "mrgid": self.mrgid,
            "length_km": self.length_km,
            "area_km2": self.area_km2,
        }


class MarineRegions(Source):
    name = "marine_regions.wfs"
    provider = Provider.MARINE_REGIONS
    variables = ("eez", "imbl", "baseline")
    docs_url = "https://marineregions.org/"
    timeout_s = 90.0  # a 2 MB WFS response over a slow link

    async def _wfs(self, type_name: str, cql: str) -> dict[str, Any] | None:
        """One WFS GetFeature call, returning parsed GeoJSON.

        The CQL filter is passed as a param so httpx encodes it: hand-encoding it
        into the URL produced a request curl could not even complete.
        """
        result = await self.fetch(
            WFS_BASE,
            params={
                "service": "WFS",
                "version": "1.1.0",
                "request": "GetFeature",
                "typeName": f"MarineRegions:{type_name}",
                "outputFormat": "application/json",
                "CQL_FILTER": cql,
            },
        )
        if not result.ok:
            log.warning("Marine Regions %s failed: %s", type_name, result.error)
            return None
        try:
            return result.json()
        except ValueError as exc:
            log.warning("Marine Regions %s returned unparseable JSON: %s", type_name, exc)
            return None

    async def india_eez(self) -> Fence | None:
        payload = await self._wfs("eez", f"mrgid={INDIA_EEZ_MRGID}")
        if not payload or not payload.get("features"):
            return None
        feature = payload["features"][0]
        props = feature.get("properties", {})
        return Fence(
            key="eez_india",
            name="Indian Exclusive Economic Zone",
            kind="eez",
            geometry=feature["geometry"],
            consequence=(
                "Inside India's EEZ you are under Indian jurisdiction. Leaving it means you "
                "are in another state's waters or on the high seas, and your licence, your "
                "insurance and the Coast Guard's ability to reach you all change."
            ),
            authority="UNCLOS / Government of India",
            mrgid=props.get("mrgid"),
            area_km2=props.get("area_km2"),
        )

    async def india_boundaries(self) -> list[Fence]:
        """Every maritime boundary line involving India.

        Queries BOTH ``territory1`` and ``territory2``. India is territory2 on the
        Sri Lanka, Maldives, Bangladesh and Pakistan lines, so filtering on
        territory1 alone returns the baselines and the 200 NM line and silently
        misses every neighbour — including the Palk Bay line, which is the one the
        demo turns on.
        """
        fences: list[Fence] = []
        # Dedup key, NOT mrgid alone: this layer returns mrgid=None for the
        # treaty segments, so a set of mrgids let the first None swallow every
        # subsequent line — India-Sri Lanka included, which is the one the demo
        # is built on. Six of seven fences vanished and the two that remained
        # looked like a working result.
        seen: set[tuple[Any, ...]] = set()

        for cql in ("territory1='India'", "territory2='India'"):
            payload = await self._wfs("eez_boundaries", cql)
            if not payload:
                continue
            for feature in payload.get("features", []):
                props = feature.get("properties", {})
                mrgid = props.get("mrgid")
                identity = (
                    mrgid,
                    props.get("line_name"),
                    props.get("line_type"),
                    props.get("length_km"),
                )
                if identity in seen:
                    continue
                seen.add(identity)

                line_type = str(props.get("line_type") or "")
                name = str(props.get("line_name") or "unnamed boundary")
                neighbour = _neighbour(props)

                # Straight baselines and connection lines are not jurisdictional
                # boundaries a fisherman crosses; keeping them as fences would
                # produce alerts that mean nothing.
                if line_type in {"Straight baseline", "Connection line"}:
                    continue

                # Length disambiguates the four Sri Lanka treaty segments, which
                # share a name and have no mrgid.
                suffix = mrgid if mrgid is not None else f"{props.get('length_km')}km"
                fences.append(
                    Fence(
                        key=f"imbl_{_slug(name)}_{_slug(str(suffix))}",
                        name=name,
                        kind="imbl" if neighbour else "eez_outer",
                        geometry=feature["geometry"],
                        consequence=_consequence(neighbour, line_type),
                        authority=_authority(line_type),
                        mrgid=mrgid,
                        length_km=props.get("length_km"),
                    )
                )

        fences.sort(key=lambda f: -(f.length_km or 0))
        return fences


def _neighbour(props: dict[str, Any]) -> str | None:
    t1, t2 = props.get("territory1"), props.get("territory2")
    for candidate in (t1, t2):
        if candidate and candidate != "India":
            return str(candidate)
    return None


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


def _consequence(neighbour: str | None, line_type: str) -> str:
    if neighbour:
        return (
            f"Crossing this line puts you in {neighbour}'s waters. Fishing there without a "
            f"{neighbour} licence risks arrest and seizure of your boat, and Indian authorities "
            "cannot protect you on the far side of it."
        )
    if line_type == "200 NM":
        return (
            "This is the 200 nautical mile limit of India's EEZ. Beyond it you are on the high "
            "seas: no Indian licence applies, and rescue response times lengthen considerably."
        )
    return "Crossing this boundary changes which state's law applies to you."


def _authority(line_type: str) -> str:
    return {
        "Treaty": "Bilateral treaty (India-Sri Lanka agreements of 1974 and 1976)",
        "Median line": "Equidistance median line",
        "Court ruling": "International arbitral award",
        "200 NM": "UNCLOS Article 57",
    }.get(line_type, f"Marine Regions line_type: {line_type}")


marine_regions = MarineRegions()


# --------------------------------------------------------------------------- #
# disk cache
# --------------------------------------------------------------------------- #


def _cache_path(name: str):
    return get_settings().static_dir / f"{name}.geojson"


def load_cached(name: str) -> dict[str, Any] | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read cached %s: %s", name, exc)
        return None


def save_cached(name: str, payload: dict[str, Any]) -> None:
    path = _cache_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    log.info("cached %s (%d KB)", name, path.stat().st_size // 1024)


async def ensure_reference_geography(*, force: bool = False) -> dict[str, Any]:
    """Fetch and cache the fences, or load them from disk.

    Disk first, deliberately: this is CURATED data that changes by treaty, so a
    cached edition is as correct as a fresh fetch, and demo day should not depend
    on VLIZ being reachable from a conference hall.
    """
    cached = None if force else load_cached("fences")
    if cached:
        return {
            **cached,
            "provenance": Provenance.CURATED.value,
            "loaded_from": "disk",
        }

    eez = await marine_regions.india_eez()
    boundaries = await marine_regions.india_boundaries()

    if eez is None and not boundaries:
        return {
            "fences": [],
            "provenance": Provenance.UNAVAILABLE.value,
            "loaded_from": "none",
            "error": (
                "Marine Regions WFS is unreachable and nothing is cached on disk. "
                "Geofencing is unavailable; every other subsystem is unaffected."
            ),
        }

    fences = ([eez] if eez else []) + boundaries
    payload = {
        "fetched_at": utcnow().isoformat(),
        "citation": CITATION.model_dump(mode="json"),
        "fences": [{**fence.describe(), "geometry": fence.geometry} for fence in fences],
    }
    save_cached("fences", payload)
    return {**payload, "provenance": Provenance.CURATED.value, "loaded_from": "wfs"}
