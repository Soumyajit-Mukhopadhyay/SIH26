"""Full-earth imagery, for the globe.

One route, and it exists for two reasons that are both about the demo not
failing:

* **CORS.** NASA GIBS does not send permissive CORS headers on every path, and a
  WebGL texture from a cross-origin image without them is either refused or
  silently tainted. Proxying makes it same-origin and the question goes away.
* **The venue's network.** A 1 MB texture fetched live is a 1 MB texture that can
  fail live. The first fetch writes it to disk and every fetch after that is a
  file read, so the globe still comes up on a hall Wi-Fi that has given out.

The imagery itself is NASA's Blue Marble shaded relief with bathymetry, served
through GIBS. It is chosen over a true-colour composite deliberately: true colour
has day/night terminators and cloud, which would put weather on the globe that
ORCA has not been asked about and cannot vouch for.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from orca.config import get_settings
from orca.obs.health import registry
from orca.provenance import Provider, utcnow
from orca.sources.base import get_client

log = logging.getLogger(__name__)

router = APIRouter(tags=["imagery"])

GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"

#: The layers we will serve, and nothing else — the layer name goes into an
#: upstream URL, so an open parameter here is an open proxy.
LAYERS: dict[str, dict[str, Any]] = {
    "bluemarble": {
        "gibs": "BlueMarble_ShadedRelief_Bathymetry",
        "label": "NASA Blue Marble — shaded relief and bathymetry",
        "note": (
            "Static composite. Chosen over a true-colour product on purpose: true colour "
            "carries a day/night terminator and cloud, which would put weather on the globe "
            "that ORCA has not been asked about."
        ),
    },
    "nightlights": {
        "gibs": "VIIRS_CityLights_2012",
        "label": "NASA VIIRS city lights (2012)",
        "note": "Used only to light the globe's night side during the opening shot.",
    },
}

#: Equirectangular, 2:1. 4096×2048 is the point where the Indian coastline reads
#: cleanly at the closest camera position in the opening shot; 8192 quadruples the
#: download for detail the descent never lingers on.
WIDTH = 4096
HEIGHT = 2048


def _cache_path(layer: str) -> Path:
    directory = get_settings().static_dir / "earth"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{layer}-{WIDTH}x{HEIGHT}.jpg"


async def fetch_layer(layer: str, *, refresh: bool = False) -> tuple[Path, bool]:
    """Return the cached file, fetching it if absent. ``(path, from_cache)``."""
    spec = LAYERS[layer]
    path = _cache_path(layer)
    if path.exists() and path.stat().st_size > 0 and not refresh:
        return path, True

    source = f"nasa.gibs.{layer}"
    registry.declare(source, provider=Provider.NASA, variables=["imagery"])
    client = await get_client()
    response = await client.get(
        GIBS_WMS,
        params={
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetMap",
            "LAYERS": spec["gibs"],
            # WMS 1.3.0 with EPSG:4326 takes BBOX in LATITUDE-FIRST order. Sending
            # lon-first returns a 200 with a plausible-looking image of the wrong
            # part of the world, which is the worst kind of wrong.
            "CRS": "EPSG:4326",
            "BBOX": "-90,-180,90,180",
            "WIDTH": str(WIDTH),
            "HEIGHT": str(HEIGHT),
            "FORMAT": "image/jpeg",
        },
        timeout=60.0,
    )
    if response.status_code >= 400 or not response.content:
        registry.record_failure(source, f"HTTP {response.status_code}")
        raise HTTPException(
            status_code=502,
            detail=f"GIBS returned HTTP {response.status_code} for {spec['gibs']}",
        )
    # GIBS reports errors as XML with a 200, so the content type is the real check.
    if "image" not in response.headers.get("content-type", ""):
        registry.record_failure(source, "non-image response")
        raise HTTPException(
            status_code=502,
            detail=f"GIBS returned {response.headers.get('content-type')} rather than an image",
        )

    path.write_bytes(response.content)
    registry.record_success(source)
    log.info("cached %s earth texture: %d KB", layer, len(response.content) // 1024)
    return path, False


@router.get("/imagery/earth/{layer}.jpg", summary="Full-earth texture for the globe")
async def earth_texture(layer: str, refresh: bool = Query(default=False)) -> FileResponse:
    if layer not in LAYERS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown layer {layer!r}; available: {', '.join(sorted(LAYERS))}",
        )
    path, from_cache = await fetch_layer(layer, refresh=refresh)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            # Immutable: the Blue Marble composite is static, and the browser
            # re-downloading a megabyte on every reload is the one thing that would
            # make the opening shot feel slow.
            "Cache-Control": "public, max-age=604800, immutable",
            "X-Orca-Source": "NASA GIBS",
            "X-Orca-Layer": LAYERS[layer]["gibs"],
            "X-Orca-From-Cache": str(from_cache).lower(),
        },
    )


@router.get("/imagery/earth", summary="What the globe is textured with, and where it came from")
async def earth_catalogue() -> dict[str, Any]:
    """Provenance for the imagery, so the opening shot is attributable too."""
    return {
        "layers": [
            {
                "name": name,
                "url": f"/imagery/earth/{name}.jpg",
                "gibs_layer": spec["gibs"],
                "label": spec["label"],
                "note": spec["note"],
                "width": WIDTH,
                "height": HEIGHT,
                "cached": _cache_path(name).exists(),
                "attribution": "NASA Global Imagery Browse Services (GIBS), EOSDIS",
            }
            for name, spec in LAYERS.items()
        ],
        "generated_at": utcnow().isoformat(),
    }
