"""Search-and-rescue drift: where a person or a boat will have moved to.

This is the module with the most direct human consequence in ORCA, so it is the
one most careful about what it claims. It computes a **search area**, never a
position. A single predicted point is the most dangerous output a drift model can
produce: it is wrong, it looks authoritative, and a coordinated search will
concentrate on it.

## The model

Total drift is the vector sum of two terms, which is the standard formulation
used by the IAMSAR manual and implemented in NOAA's SAROPS and the EU's Leeway
model:

    drift = current + leeway(wind)

**Current** comes from the surface current field ORCA already ingests, sampled
along the trajectory rather than taken once at the datum — a parcel of water 6
hours downstream is in a different current, and a constant-current assumption is
what makes a long drift estimate diverge from reality.

**Leeway** is the object's own movement through the water under wind. It is not
"3% of wind speed in the wind direction": empirically, objects move both downwind
and *across* wind, and the divergence angle flips sign unpredictably between
individual objects of the same class. Allen & Plourde (2005), the study the
coefficients below come from, measured this for dozens of object classes and it
is the reason a search area is a cloud rather than an ellipse aligned with the
wind.

Coefficients are Downwind Leeway (DWL) and Crosswind Leeway (CWL), both linear in
the 10 m wind speed:

    DWL = a·W10 + b     (a in % of wind speed)
    CWL = ±(c·W10 + d)  (sign chosen per-particle, and it can flip)

## The Monte Carlo

Each particle carries its own leeway coefficients drawn from the class's measured
spread, its own crosswind sign, and its own random walk for unresolved eddies.
The output is the set of particle positions, plus the polygon that contains a
stated fraction of them. That fraction is reported, because "the 95% area" and
"the 50% area" are very different search plans.

## What this does not model

Tides (the AOI is open ocean, where the tidal ellipse is small relative to the
current), Stokes drift as a separate term (it is folded into the measured leeway
coefficients, which were derived from objects drifting in real seas), and
grounding. All three matter close inshore, and the response says so.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from orca.provenance import utcnow

log = logging.getLogger(__name__)

DRIFT_VERSION = "orca-drift-2026.08"


#: Leeway coefficients per object class.
#:
#: `dwl_slope` is downwind leeway as a fraction of the 10 m wind speed;
#: `dwl_intercept` is in m/s. `cwl_slope`/`cwl_intercept` are the crosswind
#: component, whose SIGN is drawn per particle. `spread` is the 1-sigma relative
#: scatter on the slopes, which is what turns a line into a search area.
#:
#: Values follow Allen & Plourde's leeway taxonomy for the classes relevant to
#: Indian coastal fisheries. They are reported to the caller with their source so
#: a coordinator can substitute local values rather than inheriting ours.
@dataclass(frozen=True, slots=True)
class LeewayClass:
    code: str
    label: str
    dwl_slope: float
    dwl_intercept: float
    cwl_slope: float
    cwl_intercept: float
    spread: float
    #: Sub-grid eddy velocity scale, m/s. The current grid is ~0.08°, so
    #: structure below ~10 km is absent from it entirely. Unlike the field's bulk
    #: error this term genuinely does decorrelate quickly, so it is resampled
    #: every step and its displacement grows as sqrt(steps).
    diffusion_ms: float
    note: str


LEEWAY_CLASSES: dict[str, LeewayClass] = {
    "PIW-VERTICAL": LeewayClass(
        code="PIW-VERTICAL",
        label="Person in water, vertical (lifejacket, upright)",
        dwl_slope=0.011,
        dwl_intercept=0.068,
        cwl_slope=0.004,
        cwl_intercept=0.030,
        spread=0.35,
        diffusion_ms=0.06,
        note=(
            "The lowest leeway of any class: a person presents almost no sail area, so a PIW "
            "drifts very nearly with the water. This is why a PIW search area is dominated by "
            "the current field's own uncertainty rather than by the wind."
        ),
    ),
    "PIW-SURVIVAL-SUIT": LeewayClass(
        code="PIW-SURVIVAL-SUIT",
        label="Person in water, survival suit (floating on back)",
        dwl_slope=0.017,
        dwl_intercept=0.048,
        cwl_slope=0.006,
        cwl_intercept=0.024,
        spread=0.32,
        diffusion_ms=0.06,
        note="More exposed surface than an upright PIW, so slightly more wind-driven.",
    ),
    "FISHING-VESSEL-SMALL": LeewayClass(
        code="FISHING-VESSEL-SMALL",
        label="Small fishing vessel, disabled (under 10 m)",
        dwl_slope=0.041,
        dwl_intercept=0.069,
        cwl_slope=0.012,
        cwl_intercept=0.055,
        spread=0.28,
        diffusion_ms=0.05,
        note=(
            "A disabled FRP boat with freeboard and a wheelhouse: enough sail area that at "
            "20 kn of wind the leeway term rivals the current."
        ),
    ),
    "FISHING-VESSEL-MEDIUM": LeewayClass(
        code="FISHING-VESSEL-MEDIUM",
        label="Mechanised fishing vessel, disabled (10–24 m)",
        dwl_slope=0.038,
        dwl_intercept=0.081,
        cwl_slope=0.011,
        cwl_intercept=0.062,
        spread=0.26,
        diffusion_ms=0.05,
        note="Heavier and deeper: less wind-driven per metre of freeboard than a small FRP boat.",
    ),
    "LIFE-RAFT-CANOPY": LeewayClass(
        code="LIFE-RAFT-CANOPY",
        label="Life raft with canopy, no drogue",
        dwl_slope=0.058,
        dwl_intercept=0.108,
        cwl_slope=0.020,
        cwl_intercept=0.088,
        spread=0.30,
        diffusion_ms=0.07,
        note=(
            "The highest leeway here, and the widest crosswind scatter: an undrogued canopied "
            "raft is essentially a sail, and which way it slips is close to a coin toss."
        ),
    ),
    "DEBRIS": LeewayClass(
        code="DEBRIS",
        label="Wreckage or debris, low freeboard",
        dwl_slope=0.020,
        dwl_intercept=0.020,
        cwl_slope=0.008,
        cwl_intercept=0.020,
        spread=0.45,
        diffusion_ms=0.08,
        note=(
            "Deliberately the widest spread of any class. 'Debris' is not one object type, and "
            "a narrow search area for it would be a false claim about how much is known."
        ),
    ),
}

#: 1-sigma error on the surface current field itself, m/s.
#:
#: This is the term that dominates a search area, and the first version of this
#: module effectively left it out. Two things about it matter:
#:
#: **Its size.** Global ocean-current analyses at this resolution sit around
#: 0.1–0.2 m/s RMS against drifter observations. Over three hours a 0.15 m/s
#: error is 1.6 km of position error, which is far larger than anything the
#: object's own leeway scatter contributes for a person in the water.
#:
#: **Its structure.** It is CORRELATED in time — a current analysis that is
#: 0.15 m/s too fast in one cell is usually too fast in the next hour too. So it
#: is drawn once per particle and held for the whole run, exactly like the leeway
#: coefficients. Modelling it as fresh noise each step underestimates the spread
#: by a factor of sqrt(steps): a 3-hour PIW search area came out at 2.3 km²,
#: which would send a boat crew to look at one wave.
CURRENT_ERROR_MS = 0.15

#: Fraction of the local current speed added to the floor above.
#:
#: The flat 0.15 m/s was measured as a basin-wide RMS, and using it everywhere
#: made the search AREA almost independent of conditions: a person in the water
#: off Chennai and one in a slack corner of the Bay of Bengal came back with 95%
#: areas of 198 and 197 km2, while their drift DISPLACEMENTS differed by a factor
#: of sixteen. That is not how current analyses behave. Error scales with the
#: flow — a fast, sheared current is exactly where a 0.08 deg grid smooths away
#: the structure that matters, and where two analyses of the same day disagree
#: most.
#:
#: 25% of the local speed, on top of the floor. At 0.1 m/s the term is
#: essentially the floor; at 1 m/s in a western boundary current it roughly
#: doubles, and the search area grows with it — which is the behaviour a search
#: coordinator expects and did not previously get.
CURRENT_ERROR_SPEED_FRACTION = 0.25

#: Particles. 2000 is where the 95% hull stops moving materially between runs and
#: the whole computation still finishes inside a request.
DEFAULT_PARTICLES = 2000

#: Integration step. Leeway is linear in wind, so error is dominated by how often
#: the fields are re-sampled rather than by the integrator's order — 15 minutes
#: with forward Euler is well inside the fields' own hourly resolution.
STEP_MINUTES = 15

#: How far the cloud's centre must move before the fields are re-sampled, km.
#:
#: The integration steps every 15 minutes, but re-sampling that often buys
#: nothing: the current grid is ~0.08° (about 9 km) and the fields are hourly, so
#: a centre that has moved 3 km lands in the same cell at the same hour and gets
#: the same numbers back. A 12-hour run was making 96 upstream calls and taking
#: 14 seconds to resolve differences the data does not contain.
#:
#: Half a cell, so the sampled cell is never more than one cell stale.
RESAMPLE_KM = 4.5

#: And re-sample at least this often regardless, because the fields change with
#: time as well as position — a stationary cloud still needs the next hour's wind.
RESAMPLE_MINUTES = 60


@dataclass(slots=True)
class DriftResult:
    particles: np.ndarray  # (n, 2) as lon, lat
    track: list[tuple[float, float]]  # the mean position over time, lon/lat
    hours: float
    leeway: LeewayClass
    #: Field samples actually used, for provenance.
    samples: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    #: The current-error sigma actually used, in m/s. Reported rather than
    #: assumed constant: it scales with the local flow, so it differs between
    #: a slack corner and a western boundary current.
    current_sigma: float = CURRENT_ERROR_MS


def _metres_per_degree(lat: float) -> tuple[float, float]:
    """Local metres per degree of longitude and latitude.

    A local flat-earth conversion is right here and a full geodesic integration
    would be false precision: a 24-hour drift covers tens of kilometres, over
    which the error from this approximation is metres — far below the search
    area's own width.
    """
    lat_rad = math.radians(lat)
    per_lat = 111_132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
    per_lon = 111_412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)
    return per_lon, per_lat


async def _sample_fields(lat: float, lon: float) -> dict[str, float | None]:
    """Current and wind as u/v components at a point, in m/s.

    Returns the vectors the drift integration needs, already in the sign
    convention where u is eastward and v northward. The conversion is delegated
    to `vectorfield.to_uv` rather than done here, because wind direction is where
    it comes FROM and current direction is where it flows TO, and inlining that
    twice is how the two ended up with the same sign once already.
    """
    from orca.science.vectorfield import DIRECTION_CONVENTION, to_uv
    from orca.sources.open_meteo import conditions_at

    evidence = await conditions_at(lat, lon)

    def value(name: str) -> float | None:
        item = evidence.get(name)
        if item is None or item.value is None:
            return None
        return float(item.value)

    out: dict[str, float | None] = {
        "current_u": None,
        "current_v": None,
        "wind_u": None,
        "wind_v": None,
        "wind_speed_ms": None,
    }

    speed = value("sea_surface_current")
    direction = value("sea_surface_current_direction")
    if speed is not None and direction is not None:
        u, v = to_uv(speed, direction, DIRECTION_CONVENTION["current"])
        out["current_u"], out["current_v"] = u, v

    wind_kn = value("wind_speed")
    wind_dir = value("wind_direction")
    if wind_kn is not None and wind_dir is not None:
        wind_ms = wind_kn * 0.514444
        u, v = to_uv(wind_ms, wind_dir, DIRECTION_CONVENTION["wind"])
        out["wind_u"], out["wind_v"] = u, v
        out["wind_speed_ms"] = wind_ms

    return out


async def simulate(
    *,
    lat: float,
    lon: float,
    hours: float = 6.0,
    object_class: str = "PIW-VERTICAL",
    particles: int = DEFAULT_PARTICLES,
    seed: int | None = None,
) -> DriftResult:
    """Advect a cloud of particles from a last-known position.

    The fields are re-sampled as the cloud's centre moves, so a long drift follows
    the current it is actually in rather than the one it started in — but only
    when it has actually moved a meaningful fraction of a grid cell, or when an
    hour of model time has passed. See :data:`RESAMPLE_KM`.

    Sampling is at the CENTRE, not per particle: for the first several hours the
    cloud is far narrower than the current field's own cell, so per-particle
    sampling would cost hundreds of upstream calls to resolve a difference the
    data does not contain.
    """
    leeway = LEEWAY_CLASSES.get(object_class)
    if leeway is None:
        raise ValueError(f"unknown object class {object_class!r}")

    rng = np.random.default_rng(seed)
    steps = max(1, round(hours * 60 / STEP_MINUTES))
    dt = STEP_MINUTES * 60.0

    # Per-particle leeway. Each particle gets its own coefficients and its own
    # crosswind sign, because that scatter IS the search area — a run with the
    # class's mean coefficients produces a line, and a line is not a search plan.
    dwl_slope = leeway.dwl_slope * (1.0 + rng.normal(0.0, leeway.spread, particles))
    cwl_slope = leeway.cwl_slope * (1.0 + rng.normal(0.0, leeway.spread, particles))
    # The sign of the crosswind component flips unpredictably between individual
    # objects of the same class, so it is drawn once per particle and held.
    cwl_sign = rng.choice([-1.0, 1.0], size=particles)

    # The current field's own error, drawn once per particle and held for the
    # whole run because it is correlated in time. See CURRENT_ERROR_MS: this is
    # the term that actually sets the size of a search area.
    #
    # Scaled by the local flow rather than fixed, so a fast current produces a
    # genuinely larger area. The speed is read from the first field sample below;
    # until that exists the floor is used, which is also the right value when the
    # current is unknown.
    # `_sample_fields` returns u/v COMPONENTS, not a speed — it exists to feed the
    # integration, which needs vectors. The speed is their magnitude.
    first = await _sample_fields(lat, lon)
    u, v = first.get("current_u"), first.get("current_v")
    local_speed = (
        math.hypot(float(u), float(v))
        if isinstance(u, (int, float)) and isinstance(v, (int, float))
        else 0.0
    )
    current_sigma = CURRENT_ERROR_MS + CURRENT_ERROR_SPEED_FRACTION * local_speed
    current_bias = rng.normal(0.0, current_sigma, (particles, 2))

    positions = np.empty((particles, 2), dtype=np.float64)
    positions[:, 0] = lon
    positions[:, 1] = lat

    track: list[tuple[float, float]] = [(lon, lat)]
    samples: list[dict[str, Any]] = []
    missing: set[str] = set()

    from orca.services.geo import geodesic_m

    fields: dict[str, float | None] = {}
    sampled_at: tuple[float, float] | None = None
    sampled_step = -(10**6)

    for step in range(steps):
        centre_lon = float(np.mean(positions[:, 0]))
        centre_lat = float(np.mean(positions[:, 1]))

        # Re-sample only when the cloud has left the neighbourhood of the last
        # sample, or when enough model time has passed. See RESAMPLE_KM.
        moved_km = (
            math.inf
            if sampled_at is None
            else geodesic_m(sampled_at[1], sampled_at[0], centre_lat, centre_lon) / 1000.0
        )
        elapsed_min = (step - sampled_step) * STEP_MINUTES
        if moved_km >= RESAMPLE_KM or elapsed_min >= RESAMPLE_MINUTES:
            fields = await _sample_fields(centre_lat, centre_lon)
            sampled_at = (centre_lon, centre_lat)
            sampled_step = step
            samples.append(
                {
                    "at_hours": round(step * STEP_MINUTES / 60, 2),
                    "lat": round(centre_lat, 4),
                    "lon": round(centre_lon, 4),
                    "reason": "moved"
                    if moved_km < math.inf and moved_km >= RESAMPLE_KM
                    else "time",
                    **{k: (round(v, 4) if v is not None else None) for k, v in fields.items()},
                }
            )

        current_u = fields["current_u"]
        current_v = fields["current_v"]
        wind_u = fields["wind_u"]
        wind_v = fields["wind_v"]
        wind_speed = fields["wind_speed_ms"]

        if current_u is None or current_v is None:
            missing.add("surface current")
            current_u = current_v = 0.0
        if wind_u is None or wind_v is None or wind_speed is None:
            missing.add("wind")
            wind_u = wind_v = 0.0
            wind_speed = 0.0

        # Downwind unit vector, and the crosswind unit vector 90° to its right.
        if wind_speed > 0.05:
            down_x, down_y = wind_u / wind_speed, wind_v / wind_speed
        else:
            down_x, down_y = 0.0, 0.0
        cross_x, cross_y = down_y, -down_x

        dwl = dwl_slope * wind_speed + leeway.dwl_intercept
        cwl = cwl_sign * (cwl_slope * wind_speed + leeway.cwl_intercept)

        u = current_u + current_bias[:, 0] + dwl * down_x + cwl * cross_x
        v = current_v + current_bias[:, 1] + dwl * down_y + cwl * cross_y

        # Sub-grid eddies, resampled each step because they genuinely decorrelate
        # fast. A velocity drawn per step and applied for `dt` gives a
        # displacement whose standard deviation is `sigma * dt` per step and grows
        # as sqrt(steps) overall, which is what a diffusive process does.
        walk = rng.normal(0.0, leeway.diffusion_ms * dt, (particles, 2))

        per_lon, per_lat = _metres_per_degree(centre_lat)
        positions[:, 0] += (u * dt + walk[:, 0]) / per_lon
        positions[:, 1] += (v * dt + walk[:, 1]) / per_lat

        track.append((float(np.mean(positions[:, 0])), float(np.mean(positions[:, 1]))))

    diagnostics: dict[str, Any] = {
        "steps": steps,
        "step_minutes": STEP_MINUTES,
        "particles": particles,
        "missing_fields": sorted(missing),
        "field_samples_taken": len(samples),
        "seed": seed,
        # Published so a coordinator can see which term is setting the area's
        # size, rather than being handed a polygon with no error budget.
        "spread_sources": {
            "current_field_error_ms": round(current_sigma, 3),
            "current_field_error_floor_ms": CURRENT_ERROR_MS,
            "current_field_error_km_1sigma": round(current_sigma * hours * 3.6, 2),
            "scaled_by_local_current": current_sigma > CURRENT_ERROR_MS + 1e-9,
            "leeway_coefficient_spread": leeway.spread,
            "subgrid_eddy_ms": leeway.diffusion_ms,
            "dominant": (
                "the current field's own error"
                if current_sigma > leeway.dwl_slope * 8.0
                else "the object's leeway scatter"
            ),
        },
    }
    if missing:
        diagnostics["warning"] = (
            f"{', '.join(sorted(missing))} was unavailable for at least one step and was treated "
            "as zero. A drift estimate with a missing forcing term is an UNDERESTIMATE of both "
            "distance and spread — search wider than this area, not narrower."
        )

    return DriftResult(
        particles=positions,
        track=track,
        hours=hours,
        leeway=leeway,
        samples=samples,
        diagnostics=diagnostics,
        current_sigma=current_sigma,
    )


def containment_polygon(
    particles: np.ndarray, *, fraction: float = 0.95
) -> tuple[list[list[float]], dict[str, Any]]:
    """Convex hull of the innermost ``fraction`` of particles, by distance to the
    median.

    The median rather than the mean, and distance-ranked rather than a fitted
    ellipse, for one reason: a real drift cloud under a veering wind is banana-
    shaped, and an ellipse fitted to it covers water the object cannot be in while
    missing water it can. A hull of the actual particles cannot make that mistake.
    """
    if particles.size == 0:
        return [], {"fraction": fraction, "kept": 0}

    centre = np.median(particles, axis=0)
    # Scale longitude by cos(lat) before ranking, so "distance to the median" is
    # a real distance rather than a degree-space one that over-weights longitude.
    scale = np.array([math.cos(math.radians(float(centre[1]))), 1.0])
    offsets = (particles - centre) * scale
    radii = np.hypot(offsets[:, 0], offsets[:, 1])
    cutoff = np.quantile(radii, min(1.0, max(0.05, fraction)))
    kept = particles[radii <= cutoff]
    if len(kept) < 3:
        kept = particles

    hull = _convex_hull([(float(p[0]), float(p[1])) for p in kept])
    ring = [[x, y] for x, y in hull]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])

    per_lon, per_lat = _metres_per_degree(float(centre[1]))
    stats = {
        "fraction": fraction,
        "kept": len(kept),
        "of": len(particles),
        "centre": [round(float(centre[0]), 4), round(float(centre[1]), 4)],
        "radius_km": round(float(cutoff) * per_lat / 1000.0, 2),
        "area_km2": round(_polygon_area_km2(hull, per_lon, per_lat), 1),
    }
    return ring, stats


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain. O(n log n), no dependency, exact."""
    unique = sorted(set(points))
    if len(unique) < 3:
        return unique

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    return lower[:-1] + upper[:-1]


def _polygon_area_km2(ring: list[tuple[float, float]], per_lon: float, per_lat: float) -> float:
    """Shoelace area, with degrees converted to metres locally."""
    if len(ring) < 3:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        total += (x1 * per_lon) * (y2 * per_lat) - (x2 * per_lon) * (y1 * per_lat)
    return abs(total) / 2.0 / 1e6


def describe(result: DriftResult, *, fractions: tuple[float, ...] = (0.5, 0.95)) -> dict[str, Any]:
    """The response payload: nested containment areas, never a single point."""
    from orca.services.geo import bearing_deg, compass_point, geodesic_m

    origin = result.track[0]
    centre = result.track[-1]
    displacement_m = geodesic_m(origin[1], origin[0], centre[1], centre[0])
    heading = bearing_deg(origin[1], origin[0], centre[1], centre[0])

    areas = []
    for fraction in sorted(fractions):
        ring, stats = containment_polygon(result.particles, fraction=fraction)
        areas.append({"ring": ring, **stats})

    return {
        "drift_version": DRIFT_VERSION,
        "hours": result.hours,
        "object_class": {
            "code": result.leeway.code,
            "label": result.leeway.label,
            "downwind_leeway_pct_of_wind": round(result.leeway.dwl_slope * 100, 2),
            "crosswind_leeway_pct_of_wind": round(result.leeway.cwl_slope * 100, 2),
            "coefficient_spread": result.leeway.spread,
            "note": result.leeway.note,
        },
        "last_known_position": [round(origin[0], 4), round(origin[1], 4)],
        "mean_position": [round(centre[0], 4), round(centre[1], 4)],
        "displacement_km": round(displacement_m / 1000.0, 2),
        "displacement_nm": round(displacement_m / 1852.0, 2),
        "bearing_deg": round(heading, 1),
        "bearing": compass_point(heading),
        "track": [[round(x, 4), round(y, 4)] for x, y in result.track],
        "areas": areas,
        "field_samples": result.samples,
        "diagnostics": result.diagnostics,
        "spread_sources": result.diagnostics.get("spread_sources"),
        "model": (
            "drift = surface current + leeway(wind), integrated forward at "
            f"{STEP_MINUTES}-minute steps with the fields re-sampled as the cloud moves. Leeway "
            "is split into downwind and crosswind components, both linear in the 10 m wind "
            "speed, with per-particle coefficients drawn from the class's measured spread and "
            "the crosswind sign drawn per particle. The surface current field's own error is "
            f"drawn per particle too, at {result.current_sigma:.2f} m/s and held for the whole run "
            "because it is correlated in time — for a person in the water that term, not the "
            "object's leeway, is what sets the size of the area."
        ),
        "not_modelled": [
            "tidal streams (small relative to the current in the open AOI, significant inshore)",
            "Stokes drift as a separate term (folded into measured leeway coefficients)",
            "grounding, and interaction with the coast",
        ],
        "disclaimer": (
            "A SEARCH AREA, never a position. ORCA does not predict where the object is; it "
            "reports where a physical model says it could be, with the spread that model "
            "implies. This supplements and never replaces the Indian Coast Guard's own SAR "
            "planning — call 1554 or the nearest MRCC."
        ),
        "generated_at": utcnow().isoformat(),
    }


def roster() -> list[dict[str, Any]]:
    """The object classes, with their coefficients exposed.

    Published rather than hidden so a search coordinator can see exactly what
    leeway ORCA assumed and substitute local values if they have them.
    """
    return [
        {
            "code": c.code,
            "label": c.label,
            "downwind_leeway_pct_of_wind": round(c.dwl_slope * 100, 2),
            "downwind_intercept_ms": c.dwl_intercept,
            "crosswind_leeway_pct_of_wind": round(c.cwl_slope * 100, 2),
            "crosswind_intercept_ms": c.cwl_intercept,
            "coefficient_spread": c.spread,
            "diffusion_ms": c.diffusion_ms,
            "note": c.note,
        }
        for c in LEEWAY_CLASSES.values()
    ]
