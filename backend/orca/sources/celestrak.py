"""Current general-perturbations element sets from CelesTrak.

CelesTrak calls these records GP data; TLE is one supported serialization.  The
adapter keeps a short in-process cache because an orbit does not improve by
downloading the same element set for every map click.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from sgp4.api import Satrec
from sgp4.conveniences import sat_epoch_datetime

from orca.provenance import Freshness, Provenance, Provider, utcnow
from orca.sources.base import Source

GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
CACHE_FOR = timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class TleRecord:
    name: str
    norad_id: int
    line1: str
    line2: str
    epoch: datetime
    retrieved_at: datetime
    provenance: Provenance

    @property
    def freshness(self) -> Freshness:
        return Freshness.of("tle", self.epoch, retrieved_at=self.retrieved_at)


class CelesTrakSource(Source):
    name = "celestrak.gp"
    provider = Provider.CELESTRAK
    variables = ("tle",)
    docs_url = "https://celestrak.org/NORAD/documentation/gp-data-formats.php"

    def __init__(self) -> None:
        self._cache: dict[int, TleRecord] = {}
        self._lock = asyncio.Lock()

    async def tle(self, norad_id: int) -> TleRecord:
        if norad_id <= 0:
            raise ValueError("NORAD catalogue id must be positive")

        cached = self._cache.get(norad_id)
        if cached and utcnow() - cached.retrieved_at < CACHE_FOR:
            return TleRecord(
                name=cached.name,
                norad_id=cached.norad_id,
                line1=cached.line1,
                line2=cached.line2,
                epoch=cached.epoch,
                retrieved_at=cached.retrieved_at,
                provenance=Provenance.CACHED,
            )

        async with self._lock:
            cached = self._cache.get(norad_id)
            if cached and utcnow() - cached.retrieved_at < CACHE_FOR:
                return TleRecord(
                    name=cached.name,
                    norad_id=cached.norad_id,
                    line1=cached.line1,
                    line2=cached.line2,
                    epoch=cached.epoch,
                    retrieved_at=cached.retrieved_at,
                    provenance=Provenance.CACHED,
                )

            result = await self.fetch(
                GP_URL,
                params={"CATNR": norad_id, "FORMAT": "TLE"},
                conditional=False,
            )
            if not result.ok or not result.text:
                raise RuntimeError(result.error or f"CelesTrak returned HTTP {result.status}")

            lines = [line.strip() for line in result.text.splitlines() if line.strip()]
            if len(lines) < 3 or not lines[-2].startswith("1 ") or not lines[-1].startswith("2 "):
                raise ValueError(f"CelesTrak returned no valid TLE for NORAD {norad_id}")

            line1, line2 = lines[-2:]
            satellite = Satrec.twoline2rv(line1, line2)
            parsed_id = int(satellite.satnum)
            if parsed_id != norad_id:
                raise ValueError(
                    f"CelesTrak requested NORAD {norad_id} but returned NORAD {parsed_id}"
                )
            retrieved = utcnow()
            record = TleRecord(
                name=lines[-3].removeprefix("0 ").strip(),
                norad_id=norad_id,
                line1=line1,
                line2=line2,
                epoch=sat_epoch_datetime(satellite),
                retrieved_at=retrieved,
                provenance=result.provenance,
            )
            self._cache[norad_id] = record
            return record


celestrak = CelesTrakSource()
