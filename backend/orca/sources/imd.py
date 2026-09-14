"""India Meteorological Department official warnings.

The public catalogue at https://api.imd.gov.in/public/api_reference.html lists
**endpoints**, not API keys. JSON products (fishermen warning, port warning,
cyclone track, lightning, …) require a registered ``IMD_API_KEY`` and usually
an egress-IP whitelist. That credential cannot be scraped from the HTML.

The IMD CAP RSS mirror is zero-auth and already verified live. ORCA uses it so
official warnings are not silently UNKNOWN just because the JSON key is absent.
A failed fetch stays UNKNOWN — it is never rewritten as "no warning".
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from orca.config import get_settings
from orca.provenance import Citation, Evidence, Freshness, Provenance, Provider, utcnow
from orca.sources.base import Fetched, Source

log = logging.getLogger(__name__)

DOCS_URL = "https://api.imd.gov.in/public/api_reference.html"
REGISTER_URL = "https://api.imd.gov.in/public/register.php"
CAP_RSS_URL = "https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml"
API_BASE = "https://api.imd.gov.in/api/v1"

#: Catalogue from the public IMD API index. These are the products ORCA may
#: call — not a list of stealable secrets.
IMD_ENDPOINTS: dict[str, str] = {
    "cityforecast": f"{API_BASE}/cityforecast",
    "cityforecastloc": f"{API_BASE}/cityforecastloc",
    "cityforecast_mapping": f"{API_BASE}/cityforecast_mapping",
    "current_wx": f"{API_BASE}/current_wx",
    "districtnowcast": f"{API_BASE}/districtnowcast",
    "stationnowcast": f"{API_BASE}/stationnowcast",
    "aws_data": f"{API_BASE}/aws_data",
    "districtwarning": f"{API_BASE}/districtwarning",
    "subdivisionwarning": f"{API_BASE}/subdivisionwarning",
    "districtrainfall": f"{API_BASE}/districtrainfall",
    "staterainfall": f"{API_BASE}/staterainfall",
    "basinqpf": f"{API_BASE}/basinqpf",
    "subdivision_rainfall_forecast": f"{API_BASE}/subdivision_rainfall_forecast",
    "state_district_rainfall_forecast": f"{API_BASE}/state_district_rainfall_forecast",
    "portwarning": f"{API_BASE}/portwarning",
    "seabulletin": f"{API_BASE}/seabulletin",
    "coastalbulletin": f"{API_BASE}/coastalbulletin",
    "fishermenwarning": f"{API_BASE}/fishermenwarning",
    "cyclone_track": f"{API_BASE}/cyclone_track",
    "cyclone_wind": f"{API_BASE}/cyclone_wind",
    "cyclone_cou": f"{API_BASE}/cyclone_cou",
    "lightning": f"{API_BASE}/lightning",
    "sunmoon": f"{API_BASE}/sunmoon",
}

#: Products that can change a marine GO / NO-GO. City 7-day max/min is land
#: weather and is deliberately not in this set.
MARINE_ALERT_PRODUCTS: tuple[str, ...] = (
    "fishermenwarning",
    "portwarning",
    "seabulletin",
    "coastalbulletin",
    "cyclone_track",
    "cyclone_wind",
    "cyclone_cou",
    "lightning",
    "districtwarning",
    "subdivisionwarning",
)

_NIL = frozenset(
    {
        "",
        "nil",
        "none",
        "n/a",
        "na",
        "null",
        "false",
        "unavailable",
        "no warning",
        "no-warning",
        "no warnings",
    }
)

_CYCLONE_WORDS = (
    "cyclone",
    "cyclonic",
    "depression",
    "landfall",
    "storm surge",
)
_LIGHTNING_WORDS = ("lightning", "thunderstorm", "thunder storm", "squall")
_FISHER_WORDS = ("fishermen", "fisherman", "fisher ")
_GALE_WORDS = ("gale", "port warning", "hoist", "signal")
_RAIN_WORDS = ("rainfall", "heavy rain", "very heavy")

#: Rough coastal boxes used only to decide whether an inland CAP rainfall
#: item is relevant to a sea point. Cyclone / fishermen items skip this filter.
_COASTAL_AREAS: dict[str, tuple[float, float, float, float]] = {
    "gujarat": (20.0, 24.8, 68.0, 73.6),
    "maharashtra": (15.5, 20.5, 72.0, 73.8),
    "goa": (14.8, 15.9, 73.6, 74.3),
    "karnataka": (12.0, 15.0, 73.8, 75.0),
    "kerala": (8.1, 12.8, 74.8, 77.3),
    "tamil nadu": (8.0, 13.6, 77.5, 80.6),
    "tamilnadu": (8.0, 13.6, 77.5, 80.6),
    "andhra": (13.4, 19.2, 79.8, 85.2),
    "odisha": (17.7, 22.6, 84.2, 87.6),
    "orissa": (17.7, 22.6, 84.2, 87.6),
    "west bengal": (21.2, 22.7, 87.4, 89.2),
    "andaman": (6.5, 14.0, 92.0, 94.5),
    "nicobar": (6.5, 10.0, 92.5, 94.3),
    "lakshadweep": (8.0, 12.5, 71.5, 74.2),
    "puducherry": (11.7, 12.1, 79.7, 80.0),
}


_CAP_CITATION = Citation(
    label="IMD CAP alert feed",
    provider=Provider.IMD,
    url=CAP_RSS_URL,
    identifier="in-imd-en",
)
_API_CITATION = Citation(
    label="IMD API — marine, cyclone and lightning products",
    provider=Provider.IMD,
    url=DOCS_URL,
)


def is_active_alert(value: Any) -> bool:
    """True when a field is a real warning, not NIL / none / unavailable."""
    if value is None:
        return False
    text = str(value).strip().lower()
    return text not in _NIL


@dataclass(slots=True)
class MarineAlerts:
    """Normalised official-alert snapshot for one point."""

    verified: bool
    lightning_alert: str
    cyclone_alert: str
    fishermen_warning: str
    port_warning: str
    sea_area_warning: str
    coastal_warning: str
    rainfall_advisory: str
    items: list[dict[str, Any]] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    reason: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    keyed_api_ok: bool = False
    cap_ok: bool = False

    def as_tool_data(self) -> dict[str, Any]:
        return {
            "official_alerts_verified": self.verified,
            "lightning_alert": self.lightning_alert,
            "cyclone_alert": self.cyclone_alert,
            "fishermen_warning": self.fishermen_warning,
            "port_warning": self.port_warning,
            "sea_area_warning": self.sea_area_warning,
            "coastal_warning": self.coastal_warning,
            "rainfall_advisory": self.rainfall_advisory,
            "items": self.items,
            "sources_used": self.sources_used,
            "keyed_api_ok": self.keyed_api_ok,
            "cap_ok": self.cap_ok,
            "reason": self.reason,
            "imd_endpoints": {name: IMD_ENDPOINTS[name] for name in MARINE_ALERT_PRODUCTS},
            "imd_register": REGISTER_URL,
        }


class ImdCap(Source):
    """Zero-auth IMD CAP RSS. Always callable."""

    name = "imd.cap_rss"
    provider = Provider.IMD
    variables = ("cap_alert", "cyclone_track", "lightning_alert")
    requires = None
    docs_url = CAP_RSS_URL
    timeout_s = 10.0


class ImdApi(Source):
    """Keyed IMD JSON gateway. Dormant without ``IMD_API_KEY``."""

    name = "imd.api"
    provider = Provider.IMD
    variables = (
        "fishermen_warning",
        "port_warning",
        "cyclone_track",
        "lightning_alert",
    )
    requires = "has_imd"
    docs_url = DOCS_URL
    timeout_s = 10.0


imd_cap = ImdCap()
imd_api = ImdApi()


def _auth_headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.has_imd or settings.imd_api_key is None:
        return {}
    key = settings.imd_api_key.get_secret_value()
    # IMD's public HTML does not name the header. The gateway accepts a
    # registered key; these are the three forms Indian weather APIs use.
    return {
        "key": key,
        "X-API-Key": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }


def _unavailable(
    variable: str,
    reason: str,
    *,
    url: str = DOCS_URL,
    dataset_id: str = "imd.api",
) -> Evidence:
    return Evidence.unavailable(
        dataset_id=dataset_id,
        provider=Provider.IMD,
        variable=variable,
        reason=reason,
        url=url,
    )


def _evidence(
    variable: str,
    value: str,
    *,
    url: str,
    dataset_id: str,
    citation: Citation,
    valid_time: datetime | None = None,
    notes: str | None = None,
) -> Evidence:
    freshness_var = "cyclone_track" if "cyclone" in variable else "cap_alert"
    return Evidence(
        dataset_id=dataset_id,
        provider=Provider.IMD,
        variable=variable,
        value=value,
        provenance=Provenance.LIVE,
        freshness=Freshness.of(freshness_var, valid_time or utcnow()),
        url=url,
        citations=[citation],
        notes=notes,
    )


def _contains_any(text: str, words: Iterable[str]) -> bool:
    hay = text.lower()
    return any(word in hay for word in words)


def classify_alert_text(title: str, description: str = "") -> str:
    """Return a coarse event class for a bulletin headline."""
    blob = f"{title} {description}"
    if _contains_any(blob, _CYCLONE_WORDS):
        return "cyclone"
    if _contains_any(blob, _FISHER_WORDS):
        return "fishermen"
    if _contains_any(blob, _GALE_WORDS):
        return "gale"
    if _contains_any(blob, _LIGHTNING_WORDS):
        return "lightning"
    if _contains_any(blob, _RAIN_WORDS):
        return "rainfall"
    return "other"


def _point_in_box(lat: float, lon: float, box: tuple[float, float, float, float]) -> bool:
    lat_min, lat_max, lon_min, lon_max = box
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def coastal_area_relevant(text: str, lat: float, lon: float) -> bool:
    """Whether a land/coast bulletin names a coast near this point."""
    hay = text.lower()
    for name, box in _COASTAL_AREAS.items():
        if name in hay and _point_in_box(lat, lon, box):
            return True
    return False


def parse_cap_rss(xml_text: str) -> list[dict[str, str]]:
    """Extract RSS items from the IMD CAP mirror."""
    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for item in root.iter("item"):

        def _text(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        items.append(
            {
                "title": _text("title"),
                "link": _text("link"),
                "description": _text("description"),
                "guid": _text("guid"),
                "pubDate": _text("pubDate"),
            }
        )
    return items


def _parse_rss_time(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        from datetime import UTC

        return parsed.replace(tzinfo=UTC)
    return parsed


def _headline(title: str, description: str) -> str:
    title = title.strip()
    description = description.strip()
    if title and description and description.lower() != title.lower():
        return f"{title}: {description}"
    return title or description


def _first_active(*values: str) -> str:
    for value in values:
        if is_active_alert(value):
            return value
    return "none"


def _json_texts(payload: Any) -> list[str]:
    """Collect human bulletin strings from an IMD JSON body of unknown shape."""
    found: list[str] = []

    def walk(node: Any, key: str | None = None) -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                walk(child, str(child_key))
            return
        if isinstance(node, list):
            for child in node:
                walk(child, key)
            return
        if node is None:
            return
        text = str(node).strip()
        if not text or len(text) > 400:
            return
        interesting = key is not None and key.lower() in {
            "warning",
            "ttt warning",
            "message",
            "bulletin",
            "signal",
            "port signal",
            "weather",
            "sea condition",
            "synoptic situation",
            "day1_warning",
            "day_1",
            "cyclone_name",
            "category",
        }
        if interesting or is_active_alert(text) and classify_alert_text(text) != "other":
            found.append(text)

    walk(payload)
    return found


def _cyclone_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    names: list[str] = []
    points: list[Any] = []
    if isinstance(data, dict):
        for key in ("observed", "forecast"):
            block = data.get(key)
            if isinstance(block, list):
                points.extend(block)
        if data.get("type") in {"Polygon", "MultiPolygon"} and data.get("coordinates"):
            return "IMD cyclone wind / cone geometry is active"
        if any(
            isinstance(data.get(key), dict) and data[key].get("coordinates")
            for key in ("27kt", "34kt", "50kt", "64kt")
        ):
            return "IMD cyclone wind-warning polygons are active"
    elif isinstance(data, list):
        points = data
    for point in points:
        if not isinstance(point, dict):
            continue
        name = point.get("CYCLONE_NAME") or point.get("cyclone_name") or point.get("name")
        if name:
            names.append(str(name))
    unique = sorted({name for name in names if name})
    if unique:
        return f"Active IMD cyclone track: {', '.join(unique)}"
    if points:
        return "Active IMD cyclone track reported"
    return None


def _active_from_texts(texts: Iterable[str], kind: str) -> str | None:
    for text in texts:
        if not is_active_alert(text):
            continue
        if classify_alert_text(text) == kind or kind == "any":
            return text
        if kind == "fishermen" and classify_alert_text(text) in {"fishermen", "gale", "cyclone"}:
            return text
        if kind == "lightning" and classify_alert_text(text) == "lightning":
            return text
    return None


async def _fetch_cap() -> tuple[Fetched, list[dict[str, str]]]:
    fetched = await imd_cap.fetch(CAP_RSS_URL, headers={"Accept": "application/rss+xml, application/xml, text/xml"})
    if not fetched.ok or not fetched.text:
        return fetched, []
    try:
        items = parse_cap_rss(fetched.text)
    except ET.ParseError as exc:
        log.warning("IMD CAP RSS did not parse: %s", exc)
        return Fetched(ok=False, source=imd_cap.name, url=CAP_RSS_URL, error=f"CAP RSS parse error: {exc}"), []
    return fetched, items


async def _fetch_json(product: str) -> Fetched:
    url = IMD_ENDPOINTS[product]
    return await imd_api.fetch(
        url,
        headers=_auth_headers(),
        conditional=False,
        retries=1,
    )


def _apply_cap_items(
    items: list[dict[str, str]],
    lat: float,
    lon: float,
) -> dict[str, str]:
    cyclone = "none"
    lightning = "none"
    fishermen = "none"
    rainfall = "none"
    for item in items:
        title = item.get("title") or ""
        description = item.get("description") or ""
        kind = classify_alert_text(title, description)
        headline = _headline(title, description)
        if kind == "cyclone":
            cyclone = _first_active(cyclone, headline)
            if _contains_any(f"{title} {description}", _FISHER_WORDS):
                fishermen = _first_active(fishermen, headline)
        elif kind == "lightning":
            lightning = _first_active(lightning, headline)
        elif kind in {"fishermen", "gale"}:
            fishermen = _first_active(fishermen, headline)
        elif kind == "rainfall":
            blob = f"{title} {description}"
            if coastal_area_relevant(blob, lat, lon):
                rainfall = _first_active(rainfall, headline)
    return {
        "cyclone_alert": cyclone,
        "lightning_alert": lightning,
        "fishermen_warning": fishermen,
        "rainfall_advisory": rainfall,
    }


async def alerts_at(lat: float, lon: float) -> MarineAlerts:
    """Official IMD warning snapshot for a point.

    CAP RSS is always attempted. Keyed JSON products run only when
    ``IMD_API_KEY`` is configured. Verification is true if at least one feed
    returned a parseable body.
    """
    settings = get_settings()
    evidence: list[Evidence] = []
    items: list[dict[str, Any]] = []
    sources_used: list[str] = []

    cyclone = "unavailable"
    lightning = "unavailable"
    fishermen = "unavailable"
    port = "unavailable"
    sea = "unavailable"
    coastal = "unavailable"
    rainfall = "none"
    cap_ok = False
    keyed_ok = False
    failures: list[str] = []

    cap_fetched, cap_items = await _fetch_cap()
    if cap_fetched.ok:
        cap_ok = True
        sources_used.append("imd.cap_rss")
        classified = _apply_cap_items(cap_items, lat, lon)
        cyclone = classified["cyclone_alert"]
        lightning = classified["lightning_alert"]
        fishermen = classified["fishermen_warning"]
        rainfall = classified["rainfall_advisory"]
        latest = _parse_rss_time(cap_items[0]["pubDate"]) if cap_items else utcnow()
        for variable, value in (
            ("cyclone_alert", cyclone),
            ("lightning_alert", lightning),
            ("fishermen_warning", fishermen),
        ):
            evidence.append(
                _evidence(
                    variable,
                    value,
                    url=CAP_RSS_URL,
                    dataset_id="imd.cap_rss",
                    citation=_CAP_CITATION,
                    valid_time=latest,
                    notes=f"{len(cap_items)} CAP item(s) in the IMD feed",
                )
            )
        for raw in cap_items[:8]:
            items.append(
                {
                    "source": "imd.cap_rss",
                    "kind": classify_alert_text(raw.get("title", ""), raw.get("description", "")),
                    "title": raw.get("title"),
                    "description": raw.get("description"),
                    "url": raw.get("link"),
                    "published": raw.get("pubDate"),
                }
            )
    else:
        failures.append(cap_fetched.error or "IMD CAP RSS unavailable")
        evidence.append(
            _unavailable(
                "cap_alert",
                cap_fetched.error or "IMD CAP RSS unavailable",
                url=CAP_RSS_URL,
                dataset_id="imd.cap_rss",
            )
        )

    if settings.has_imd:
        import asyncio

        products = (
            "fishermenwarning",
            "portwarning",
            "seabulletin",
            "coastalbulletin",
            "cyclone_track",
            "cyclone_wind",
            "cyclone_cou",
            "lightning",
        )
        fetched_list = await asyncio.gather(*(_fetch_json(name) for name in products))
        for product, fetched in zip(products, fetched_list, strict=True):
            if not fetched.ok:
                failures.append(f"{product}: {fetched.error or fetched.status}")
                continue
            keyed_ok = True
            if "imd.api" not in sources_used:
                sources_used.append("imd.api")
            try:
                payload = fetched.json()
            except ValueError as exc:
                failures.append(f"{product}: {exc}")
                continue
            texts = _json_texts(payload)
            if product == "cyclone_track" or product.startswith("cyclone_"):
                found = _cyclone_from_payload(payload)
                if found:
                    cyclone = found
                    items.append({"source": f"imd.api:{product}", "kind": "cyclone", "title": found})
            elif product == "fishermenwarning":
                found = _active_from_texts(texts, "fishermen") or (
                    texts[0] if texts and is_active_alert(texts[0]) else None
                )
                if found:
                    fishermen = found
                    items.append({"source": "imd.api:fishermenwarning", "kind": "fishermen", "title": found})
            elif product == "portwarning":
                found = _active_from_texts(texts, "any")
                if found and is_active_alert(found):
                    port = found
                    items.append({"source": "imd.api:portwarning", "kind": "port", "title": found})
            elif product == "seabulletin":
                found = _active_from_texts(texts, "any")
                if found and is_active_alert(found):
                    sea = found
                    items.append({"source": "imd.api:seabulletin", "kind": "sea", "title": found})
            elif product == "coastalbulletin":
                found = _active_from_texts(texts, "any")
                if found and is_active_alert(found):
                    coastal = found
                    items.append({"source": "imd.api:coastalbulletin", "kind": "coastal", "title": found})
            elif product == "lightning":
                found = _active_from_texts(texts, "lightning")
                if found:
                    lightning = found
                    items.append({"source": "imd.api:lightning", "kind": "lightning", "title": found})
            evidence.append(
                _evidence(
                    {
                        "fishermenwarning": "fishermen_warning",
                        "portwarning": "port_warning",
                        "seabulletin": "sea_area_warning",
                        "coastalbulletin": "coastal_warning",
                        "cyclone_track": "cyclone_alert",
                        "cyclone_wind": "cyclone_alert",
                        "cyclone_cou": "cyclone_alert",
                        "lightning": "lightning_alert",
                    }[product],
                    {
                        "fishermenwarning": fishermen,
                        "portwarning": port if is_active_alert(port) else "none",
                        "seabulletin": sea if is_active_alert(sea) else "none",
                        "coastalbulletin": coastal if is_active_alert(coastal) else "none",
                        "cyclone_track": cyclone if is_active_alert(cyclone) else "none",
                        "cyclone_wind": cyclone if is_active_alert(cyclone) else "none",
                        "cyclone_cou": cyclone if is_active_alert(cyclone) else "none",
                        "lightning": lightning if is_active_alert(lightning) else "none",
                    }[product],
                    url=IMD_ENDPOINTS[product],
                    dataset_id="imd.api",
                    citation=_API_CITATION,
                )
            )
    else:
        failures.append(
            "IMD_API_KEY is not configured — fishermen / port / cyclone JSON "
            f"need a registered key at {REGISTER_URL} (the public catalogue lists endpoints, not keys)"
        )

    verified = cap_ok or keyed_ok
    if not verified:
        reason = (
            "Official IMD warnings could not be verified. "
            + "; ".join(failures[:3])
        )
        return MarineAlerts(
            verified=False,
            lightning_alert="unavailable",
            cyclone_alert="unavailable",
            fishermen_warning="unavailable",
            port_warning="unavailable",
            sea_area_warning="unavailable",
            coastal_warning="unavailable",
            rainfall_advisory="unavailable",
            items=items,
            sources_used=sources_used,
            reason=reason,
            evidence=evidence,
            keyed_api_ok=False,
            cap_ok=False,
        )

    if not is_active_alert(port):
        port = "none"
    if not is_active_alert(sea):
        sea = "none"
    if not is_active_alert(coastal):
        coastal = "none"
    if cyclone == "unavailable":
        cyclone = "none"
    if lightning == "unavailable":
        lightning = "none"
    if fishermen == "unavailable":
        fishermen = "none"

    extras = []
    if not settings.has_imd:
        extras.append(
            "Keyed IMD JSON (fishermen warning, cyclone track, lightning) is still "
            "waiting on IMD_API_KEY + IP whitelist; CAP RSS was used instead."
        )
    elif not keyed_ok:
        extras.append("IMD_API_KEY is set but the JSON gateway rejected the request.")
    reason = " ".join(
        [
            "Official IMD warning feeds were checked.",
            *extras,
        ]
    )
    return MarineAlerts(
        verified=True,
        lightning_alert=lightning,
        cyclone_alert=cyclone,
        fishermen_warning=fishermen,
        port_warning=port,
        sea_area_warning=sea,
        coastal_warning=coastal,
        rainfall_advisory=rainfall,
        items=items,
        sources_used=sources_used,
        reason=reason,
        evidence=evidence,
        keyed_api_ok=keyed_ok,
        cap_ok=cap_ok,
    )
