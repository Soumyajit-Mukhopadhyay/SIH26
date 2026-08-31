"""Copernicus Marine Data Store adapter using its officially supported Toolbox."""

from __future__ import annotations

import asyncio
import importlib.util
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from pydantic import BaseModel

from orca.config import get_settings
from orca.obs.health import registry
from orca.provenance import Citation, Evidence, Freshness, Provenance, Provider, utcnow
from orca.sources.base import Source

WAVE_DATASET = "cmems_mod_glo_wav_anfc_0.083deg_PT3H-i"
WAVE_VARIABLE = "VHM0"
DOCS_URL = "https://help.marine.copernicus.eu/en/articles/8287609"


class CmemsDataset(BaseModel):
    product_id: str
    title: str
    dataset_ids: list[str]


class CmemsCatalogueResponse(BaseModel):
    query: str
    products: list[CmemsDataset]
    toolbox_installed: bool
    authenticated: bool
    provenance: str
    note: str


class CopernicusMarineSource(Source):
    name = "cmems.toolbox"
    provider = Provider.CMEMS
    variables = ("wave_height", "ocean_catalogue")
    requires = "has_cmems"
    docs_url = DOCS_URL

    @staticmethod
    def installed() -> bool:
        return importlib.util.find_spec("copernicusmarine") is not None

    def _credentials(self) -> tuple[str, str]:
        settings = get_settings()
        if not settings.has_cmems:
            raise RuntimeError("Copernicus Marine credentials are not configured")
        assert settings.cmems_username and settings.cmems_password
        return settings.cmems_username, settings.cmems_password.get_secret_value()

    async def catalogue(self, query: str = "Indian Ocean") -> CmemsCatalogueResponse:
        self._credentials()
        if not self.installed():
            raise RuntimeError("copernicusmarine package is not installed")

        def _describe() -> Any:
            import copernicusmarine

            return copernicusmarine.describe(contains=[query], disable_progress_bar=True)

        started = time.monotonic()
        try:
            catalogue = await asyncio.to_thread(_describe)
        except Exception as exc:
            registry.record_failure(self.name, f"{type(exc).__name__}: {exc}")
            raise RuntimeError(
                f"Copernicus Marine catalogue failed: {type(exc).__name__}: {exc}"
            ) from exc
        registry.record_success(
            self.name,
            provenance=Provenance.LIVE,
            latency_ms=(time.monotonic() - started) * 1000,
        )
        products: list[CmemsDataset] = []
        for product in getattr(catalogue, "products", [])[:20]:
            datasets = [
                str(dataset.dataset_id)
                for dataset in (getattr(product, "datasets", None) or [])
                if getattr(dataset, "dataset_id", None)
            ]
            products.append(
                CmemsDataset(
                    product_id=str(getattr(product, "product_id", "unknown")),
                    title=str(getattr(product, "title", "Untitled Copernicus Marine product")),
                    dataset_ids=datasets,
                )
            )
        return CmemsCatalogueResponse(
            query=query,
            products=products,
            toolbox_installed=True,
            authenticated=get_settings().has_cmems,
            provenance="live",
            note="Live catalogue metadata; no ocean grid was downloaded by this request.",
        )

    async def wave_at(self, lat: float, lon: float) -> Evidence:
        username, password = self._credentials()
        if not self.installed():
            return Evidence.unavailable(
                dataset_id=WAVE_DATASET,
                provider=Provider.CMEMS,
                variable="wave_height",
                reason="copernicusmarine package is not installed",
                url=DOCS_URL,
            )
        now = utcnow()

        def _read() -> tuple[float, datetime]:
            import copernicusmarine

            dataset = copernicusmarine.open_dataset(
                dataset_id=WAVE_DATASET,
                username=username,
                password=password,
                variables=[WAVE_VARIABLE],
                minimum_longitude=lon,
                maximum_longitude=lon,
                minimum_latitude=lat,
                maximum_latitude=lat,
                start_datetime=now - timedelta(hours=12),
                end_datetime=now + timedelta(hours=12),
                coordinates_selection_method="nearest",
            )
            try:
                array = dataset[WAVE_VARIABLE].load()
                times = np.asarray(dataset["time"].values).reshape(-1)
                if times.size == 0:
                    raise ValueError("CMEMS response has no time coordinate")
                target = np.datetime64(now.replace(tzinfo=None), "s")
                time_index = int(
                    np.argmin(
                        np.abs(times.astype("datetime64[s]") - target).astype("timedelta64[s]")
                    )
                )
                sample = array.isel(time=time_index) if "time" in array.dims else array
                values = np.asarray(sample.values, dtype=float).reshape(-1)
                finite = values[np.isfinite(values)]
                if finite.size == 0:
                    raise ValueError("CMEMS returned no finite VHM0 value at this point")
                value = float(finite[0])
                stamp = np.datetime_as_string(times[time_index], unit="s")
                valid_time = datetime.fromisoformat(stamp)
                if valid_time.tzinfo is None:
                    valid_time = valid_time.replace(tzinfo=UTC)
                return value, valid_time
            finally:
                dataset.close()

        started = time.monotonic()
        try:
            value, valid_time = await asyncio.to_thread(_read)
        except Exception as exc:  # noqa: BLE001 - Toolbox wraps transport/backend failures
            error = f"{type(exc).__name__}: {exc}"
            registry.record_failure(self.name, error)
            return Evidence.unavailable(
                dataset_id=WAVE_DATASET,
                provider=Provider.CMEMS,
                variable="wave_height",
                reason=error,
                url=DOCS_URL,
            )
        if not 0 <= value <= 30:
            error = f"CMEMS VHM0 {value} m is outside ORCA's physical validation range 0-30 m"
            registry.record_failure(self.name, error)
            return Evidence.unavailable(
                dataset_id=WAVE_DATASET,
                provider=Provider.CMEMS,
                variable="wave_height",
                reason=error,
                url=DOCS_URL,
            )
        registry.record_success(
            self.name,
            provenance=Provenance.LIVE,
            latency_ms=(time.monotonic() - started) * 1000,
        )
        return Evidence(
            dataset_id=WAVE_DATASET,
            provider=Provider.CMEMS,
            variable="wave_height",
            value=round(value, 3),
            unit="m",
            provenance=Provenance.LIVE,
            freshness=Freshness.of("wave_height", valid_time),
            url=DOCS_URL,
            location=(lon, lat),
            notes=(
                "Global Ocean Waves analysis/forecast VHM0 on the 0.083 degree grid. "
                "The nearest model cell is a cross-check, not an in-situ buoy."
            ),
            citations=[
                Citation(
                    label="Copernicus Marine Global Ocean Waves analysis and forecast",
                    provider=Provider.CMEMS,
                    url=DOCS_URL,
                    identifier=WAVE_DATASET,
                )
            ],
        )


cmems = CopernicusMarineSource()
