"""Who to call, and which of them is actually responsible.

## Why this is a hand-entered table and not an API

There is no machine-readable feed of Indian maritime rescue centres. I checked
OpenStreetMap first, because that is the source ORCA already uses for landmarks:
a full Overpass query over the Indian box for ``amenity=coast_guard``,
``emergency=coast_guard``, ``seamark:type=rescue_station`` and
``emergency=lifeguard`` returned **22 features, exactly one of which is an
Indian Coast Guard facility** (Regional Headquarters West, Mumbai). The rest are
beach lifeguard huts, most of them in Sri Lanka and the Maldives. Routing a
distress call on that would send a sinking fisherman's position to a lifeguard
shed in Bakkhali.

So the table below is transcribed from **Appendix 'A' of the National Maritime
Search and Rescue Plan 2022**, the SAR Point of Contact list that the Director
General Indian Coast Guard publishes in their capacity as National Maritime SAR
Coordinating Authority. That is the authoritative list: 3 MRCCs and 36 MRSCs.
Every telephone number, fax, Inmarsat-C ID and email here comes from that
document.

## The coordinates are ORCA's, and that distinction matters

The NMSAR plan gives contact details, **not positions**. The coordinates below
were geocoded through OpenStreetMap Nominatim against the port or town each
centre is named for, and they locate the *port*, not the building. They are good
to roughly a kilometre — fine for "which centre is nearest" and for starting a
transit leg, useless as a street address. The `position_note` field says so on
every record rather than leaving it to be assumed.

The one public alternative, sarcontacts.info, carries coordinates but they are
wrong often enough to be dangerous: it places MRCC Mumbai at 19.10N 73.16E,
which is inland near Kalyan, and MRSC Haldia at 22.58N 88.35E, which is Kolkata.

## Responsibility is decided by the register, not by a drawn line

The ISRR is divided between the three MRCCs, but the dividing lines are not
published as coordinates anywhere I could reach. Rather than invent a boundary
and dress it up as policy, `responsible_mrcc` returns the parent MRCC of the
**nearest centre** — which is how it works in practice, because the sub-centre
that takes the call reports to its own MRCC. It is derived from the register we
actually have instead of from a line we would have had to make up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from orca.provenance import Citation, Provider

#: The source for every contact detail in this module.
NMSAR_CITATION = Citation(
    label="National Maritime Search and Rescue Plan 2022, Appendix 'A' — MRCC/MRSC SAR Points of Contact",
    provider="Indian Coast Guard (National Maritime SAR Coordinating Authority)",
    url="https://rsmcnewdelhi.imd.gov.in/images/pdf/NMSAR_Plan_2022.pdf",
    identifier="NMSAR Plan 2022, in force 18 Nov 2022",
    quote=(
        "A SAR distress emergency telephone number 1554 is established nationwide which when "
        "dialed, the call reaches the respective MRCC/MRSC."
    ),
)

#: Where the coordinates came from — separately, because they are NOT from the
#: NMSAR plan and must not inherit its authority.
POSITION_CITATION = Citation(
    label="Centre positions geocoded from OpenStreetMap Nominatim",
    provider=Provider.ORCA,
    url="https://nominatim.openstreetmap.org/",
    quote=(
        "Locates the port or town each centre is named for, not the building. Accurate to "
        "roughly a kilometre."
    ),
)

#: The nationwide toll-free maritime distress number. NMSAR Plan 2022 para 54(b).
MSAR_DISTRESS_NUMBER = "1554"

#: India's international distress/emergency number for the same purpose from a
#: mobile ashore. Kept separate from 1554, which is the maritime SAR line.
NATIONAL_EMERGENCY_NUMBER = "112"


@dataclass(frozen=True, slots=True)
class RescueCentre:
    """One Maritime Rescue Coordination Centre or Sub-Centre."""

    #: "MRCC Chennai", exactly as the NMSAR plan names it.
    name: str
    #: "MRCC" or "MRSC". An MRCC coordinates; an MRSC executes under one.
    kind: str
    lat: float
    lon: float
    #: Which MRCC this centre answers to. An MRCC is its own parent.
    mrcc: str
    #: The sea area heading it appears under in Appendix 'A'.
    sea_area: str
    state: str
    #: Landline(s), as printed, without the +91 that heads the column.
    telephone: tuple[str, ...]
    email: tuple[str, ...]
    fax: tuple[str, ...] = ()
    #: Inmarsat-C ship earth station ID, where the plan gives one. This is the
    #: link that still works when the mobile network does not.
    inmarsat_c: str | None = None
    #: AFTN address — MRCCs only, for aeronautical coordination.
    aftn: str | None = None

    @property
    def is_mrcc(self) -> bool:
        return self.kind == "MRCC"

    def dial(self) -> list[str]:
        """Numbers in international form, distress line first."""
        return [MSAR_DISTRESS_NUMBER, *(f"+91-{number}" for number in self.telephone)]


_AS = "Arabian Sea / Indian Ocean"
_BOB = "Bay of Bengal"
_AN = "Andaman & Nicobar Seas"

_MUM = "MRCC Mumbai"
_CHE = "MRCC Chennai"
_PTB = "MRCC Port Blair"


def _icg(local: str) -> str:
    return f"{local}@indiancoastguard.nic.in"


CENTRES: tuple[RescueCentre, ...] = (
    # ---------------- Arabian Sea / Indian Ocean — MRCC Mumbai ----------------
    RescueCentre(
        _MUM,
        "MRCC",
        19.0168,
        72.8169,
        _MUM,
        _AS,
        "Maharashtra",
        ("22-24388065", "22-24383592"),
        (_icg("mrcc-west"), "mrccwest@gmail.com"),
        ("22-24316558",),
        "441907210",
        "VABBYXYC",
    ),
    RescueCentre(
        "MRSC Jakhau",
        "MRSC",
        23.2382,
        68.6085,
        _MUM,
        _AS,
        "Gujarat",
        ("2831-286302", "2831-286304"),
        (_icg("mrsc-jakhau"), _icg("cgs-jkh")),
        ("2831-286432",),
        "441900444",
    ),
    RescueCentre(
        "MRSC Mundra",
        "MRSC",
        22.7530,
        69.6847,
        _MUM,
        _AS,
        "Gujarat",
        ("2838-271403",),
        (_icg("mrsc-mundra"), _icg("cgs-mdr")),
        ("2838-271404",),
        "441901016",
    ),
    RescueCentre(
        "MRSC Vadinar",
        "MRSC",
        22.3943,
        69.7185,
        _MUM,
        _AS,
        "Gujarat",
        ("2833-256560",),
        (_icg("mrsc-vadinar"), _icg("cgs-vdr")),
        ("2833-256560",),
        "441900448",
    ),
    RescueCentre(
        "MRSC Okha",
        "MRSC",
        22.4716,
        69.0765,
        _MUM,
        _AS,
        "Gujarat",
        ("2892-262259",),
        (_icg("mrsc-okha"), _icg("cgs-okh")),
        ("2892-263421",),
        "441923271",
    ),
    RescueCentre(
        "MRSC Porbandar",
        "MRSC",
        21.6390,
        69.5894,
        _MUM,
        _AS,
        "Gujarat",
        ("286-2242451",),
        (_icg("mrsc-dhq1"), _icg("dhq1")),
        ("286-2210559",),
        "441908210",
    ),
    RescueCentre(
        "MRSC Veraval",
        "MRSC",
        20.9044,
        70.3760,
        _MUM,
        _AS,
        "Gujarat",
        ("2876-241352",),
        (_icg("mrsc-veraval"), _icg("cgs-vrl")),
        ("2876-241353",),
        "441912210",
    ),
    RescueCentre(
        "MRSC Pipavav",
        "MRSC",
        20.9253,
        71.4979,
        _MUM,
        _AS,
        "Gujarat",
        ("2794-221603",),
        (_icg("mrsc-pipavav"), _icg("cgs-ppv")),
        ("2794-221600",),
    ),
    RescueCentre(
        "MRSC Dahanu",
        "MRSC",
        19.9887,
        72.7338,
        _MUM,
        _AS,
        "Maharashtra",
        ("2528-250004",),
        (_icg("mrsc-dahanu"), _icg("cgs-dah")),
        ("2528-250003",),
        "441901019",
    ),
    RescueCentre(
        "MRSC Murud Janjira",
        "MRSC",
        18.3220,
        72.9605,
        _MUM,
        _AS,
        "Maharashtra",
        ("2144-274421",),
        (_icg("mrsc-mjr"), _icg("cgs-mjr")),
        ("2144-274420",),
    ),
    RescueCentre(
        # Mirya Bandar, not Ratnagiri town: the town centre is 20 km inland and
        # would have started every transit leg on land.
        "MRSC Ratnagiri",
        "MRSC",
        17.0014,
        73.2861,
        _MUM,
        _AS,
        "Maharashtra",
        ("2352-299230",),
        (_icg("mrsc-ratnagiri"), _icg("cgs-rtn")),
        ("2352-299231",),
    ),
    RescueCentre(
        "MRSC Karwar",
        "MRSC",
        14.7997,
        74.1149,
        _MUM,
        _AS,
        "Karnataka",
        ("8382-263100",),
        (_icg("mrsc-karwar"), _icg("cgs-kwr")),
        ("8382-263100",),
        "441925162",
    ),
    RescueCentre(
        "MRSC Goa",
        "MRSC",
        15.4102,
        73.7936,
        _MUM,
        _AS,
        "Goa",
        ("832-2950274",),
        (_icg("mrsc-goa"), _icg("dhq11")),
        ("832-2950277",),
        "441907410",
    ),
    RescueCentre(
        "MRSC New Mangalore",
        "MRSC",
        12.9298,
        74.8225,
        _MUM,
        _AS,
        "Karnataka",
        ("824-2405278",),
        (_icg("mrsc-newmaglore"), _icg("dhq3")),
        ("824-2405267",),
        "441908310",
    ),
    RescueCentre(
        "MRSC Kochi",
        "MRSC",
        9.9648,
        76.2721,
        _MUM,
        _AS,
        "Kerala",
        ("484-2218969",),
        (_icg("mrsc-kochi"), _icg("dhq4")),
        ("484-2217164",),
        "441907310",
    ),
    RescueCentre(
        "MRSC Beypore",
        "MRSC",
        11.1669,
        75.8076,
        _MUM,
        _AS,
        "Kerala",
        ("495-2417995",),
        (_icg("mrsc-beypore"), _icg("cgs-bpe")),
        ("495-2417994",),
    ),
    RescueCentre(
        "MRSC Vizhinjam",
        "MRSC",
        8.3818,
        76.9916,
        _MUM,
        _AS,
        "Kerala",
        ("471-2481855",),
        (_icg("mrsc-vizhinjam"), _icg("cgsvzm")),
        ("471-2486484",),
        "441900449",
    ),
    RescueCentre(
        "MRSC Minicoy",
        "MRSC",
        8.2952,
        73.0648,
        _MUM,
        _AS,
        "Lakshadweep",
        ("4892-222477",),
        (_icg("mrsc-minicoy"), _icg("cgs-mcy")),
        ("4892-223232",),
    ),
    RescueCentre(
        "MRSC Androth",
        "MRSC",
        10.8132,
        73.6805,
        _MUM,
        _AS,
        "Lakshadweep",
        ("4893-232224",),
        (_icg("mrsc-androth"), _icg("cgs-adr")),
        ("4893-232645",),
    ),
    RescueCentre(
        "MRSC Kavaratti",
        "MRSC",
        10.5672,
        72.6395,
        _MUM,
        _AS,
        "Lakshadweep",
        ("4896-263491",),
        (_icg("mrsc-kavaratti"), _icg("dhq12")),
        ("4896-263497",),
        "441900453",
    ),
    # ---------------------- Bay of Bengal — MRCC Chennai ----------------------
    RescueCentre(
        _CHE,
        "MRCC",
        13.0964,
        80.3036,
        _CHE,
        _BOB,
        "Tamil Nadu",
        ("44-25395018",),
        (_icg("mrcc-east"), "mrccchennai@gmail.com"),
        ("44-23460405",),
        "441922669",
        "VOMMYXCG",
    ),
    RescueCentre(
        "MRSC Frazerganj",
        "MRSC",
        21.5825,
        88.2583,
        _CHE,
        _BOB,
        "West Bengal",
        ("8373099183",),
        (_icg("mrsc-frazerganj"), _icg("cgs-fzr")),
    ),
    RescueCentre(
        "MRSC Haldia",
        "MRSC",
        22.0281,
        88.0633,
        _CHE,
        _BOB,
        "West Bengal",
        ("3224-267755",),
        (_icg("mrsc-haldia"), _icg("dhq8")),
        ("3224-264541", "3224-263407"),
        "441907110",
    ),
    RescueCentre(
        "MRSC Paradip",
        "MRSC",
        20.2762,
        86.6838,
        _CHE,
        _BOB,
        "Odisha",
        ("6722-223359", "6722-222279"),
        (_icg("mrsc-paradip"), _icg("dhq7")),
        ("6722-220174",),
        "441907710",
    ),
    RescueCentre(
        "MRSC Gopalpur",
        "MRSC",
        19.2983,
        84.9534,
        _CHE,
        _BOB,
        "Odisha",
        ("6811-295513",),
        (_icg("mrsc-gopalpur"), _icg("g-pur")),
        (),
        "441912310",
    ),
    RescueCentre(
        "MRSC Visakhapatnam",
        "MRSC",
        17.7194,
        83.2550,
        _CHE,
        _BOB,
        "Andhra Pradesh",
        ("891-2745806",),
        (_icg("mrsc-vizag"), _icg("dhq6")),
        ("891-2741130",),
        "441907010",
    ),
    RescueCentre(
        "MRSC Kakinada",
        "MRSC",
        16.9537,
        82.2409,
        _CHE,
        _BOB,
        "Andhra Pradesh",
        ("884-2342175",),
        (_icg("mrsc-kakinada"), _icg("cgs-knd")),
        ("884-2342171",),
        "441913210",
    ),
    RescueCentre(
        "MRSC Nizampatnam",
        "MRSC",
        15.9046,
        80.6668,
        _CHE,
        _BOB,
        "Andhra Pradesh",
        ("8648-257357",),
        (_icg("mrsc-npatnam"), _icg("cgs-nzm")),
        ("8648-294257",),
        "441925034",
    ),
    RescueCentre(
        "MRSC Krishnapatnam",
        "MRSC",
        14.2631,
        80.1206,
        _CHE,
        _BOB,
        "Andhra Pradesh",
        ("861-2377730",),
        (_icg("mrsc-kpatnam"), _icg("cgs-kpm")),
        ("861-2377740",),
        "441925069",
    ),
    RescueCentre(
        "MRSC Puducherry",
        "MRSC",
        11.9180,
        79.8261,
        _CHE,
        _BOB,
        "Puducherry",
        ("413-2257950",),
        (_icg("mrsc-puducherry"), _icg("cgs-pon")),
        ("413-2257956",),
        "441901355",
    ),
    RescueCentre(
        "MRSC Karaikal",
        "MRSC",
        10.8411,
        79.8461,
        _CHE,
        _BOB,
        "Puducherry",
        ("4368-299150",),
        (_icg("mrsc-karaikal"), _icg("cgs-kkl")),
        ("4368-238101",),
        "441925046",
    ),
    RescueCentre(
        "MRSC Tuticorin",
        "MRSC",
        8.7444,
        78.1706,
        _CHE,
        _BOB,
        "Tamil Nadu",
        ("461-2352046",),
        (_icg("mrsc-tuticorin"), _icg("cgs-tut")),
        ("461-2353503",),
        "441928126",
    ),
    RescueCentre(
        "MRSC Mandapam",
        "MRSC",
        9.2827,
        79.1527,
        _CHE,
        _BOB,
        "Tamil Nadu",
        ("4573-241634",),
        (_icg("mrsc-mandapam"), _icg("cgs-mdp")),
        ("4573-241142",),
        "441907810",
    ),
    # ------------------ Andaman & Nicobar — MRCC Port Blair ------------------
    RescueCentre(
        _PTB,
        "MRCC",
        11.6645,
        92.7390,
        _PTB,
        _AN,
        "Andaman & Nicobar Islands",
        ("3192-245530", "3192-246081"),
        (_icg("mrcc-ptb"),),
        ("3192-242948",),
        "441922666",
        "VOPBYXCG",
    ),
    RescueCentre(
        "MRSC Campbell Bay",
        "MRSC",
        7.0088,
        93.9328,
        _PTB,
        _AN,
        "Andaman & Nicobar Islands",
        ("3193-264666", "3193-264235"),
        (_icg("mrsc-cbay"), _icg("dhq10")),
        ("3193-264215",),
        "441907910",
    ),
    RescueCentre(
        "MRSC Hutbay",
        "MRSC",
        10.6047,
        92.5337,
        _PTB,
        _AN,
        "Andaman & Nicobar Islands",
        ("3192-211480",),
        (_icg("mrsc-hutbay"), _icg("cgs-htb")),
        ("3192-284194",),
    ),
    RescueCentre(
        "MRSC Kamorta",
        "MRSC",
        8.0385,
        93.5441,
        _PTB,
        _AN,
        "Andaman & Nicobar Islands",
        ("3192-263053",),
        (_icg("mrsc-kamorta"), _icg("cgs-kmt")),
        ("3192-263030",),
        "441912710",
    ),
    RescueCentre(
        "MRSC Mayabundar",
        "MRSC",
        12.7702,
        92.8313,
        _PTB,
        _AN,
        "Andaman & Nicobar Islands",
        ("3192-276449",),
        (_icg("mrsc-mbunder"), _icg("myb")),
        ("3192-276449",),
        "441912810",
    ),
    RescueCentre(
        "MRSC Diglipur",
        "MRSC",
        13.2444,
        92.9720,
        _PTB,
        _AN,
        "Andaman & Nicobar Islands",
        ("3192-272315",),
        (_icg("mrsc-diglipur"), _icg("dhq9")),
        ("3192-272345",),
        "441908110",
    ),
)

BY_NAME: dict[str, RescueCentre] = {c.name: c for c in CENTRES}

_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def nearest(lat: float, lon: float, *, limit: int = 3) -> list[tuple[RescueCentre, float, float]]:
    """The closest rescue centres, as ``(centre, great-circle km, bearing to it)``.

    Great-circle, not steaming distance. A centre 40 km away across a headland is
    further by sea than the number here, which is why the distress flow routes the
    transit properly rather than dividing this by a speed.
    """
    ranked = sorted(
        ((centre, haversine_km(lat, lon, centre.lat, centre.lon)) for centre in CENTRES),
        key=lambda pair: pair[1],
    )
    return [
        (centre, round(km, 1), round(_bearing_deg(lat, lon, centre.lat, centre.lon), 1))
        for centre, km in ranked[: max(1, limit)]
    ]


def responsible_mrcc(lat: float, lon: float) -> RescueCentre:
    """The MRCC that would coordinate an incident here.

    Derived from the nearest centre's parent rather than from an ISRR boundary
    polygon, because no published coordinates for those boundaries were
    reachable. See the module docstring: this is the register we have, not a
    line invented to look authoritative.
    """
    closest, _, _ = nearest(lat, lon, limit=1)[0]
    return BY_NAME[closest.mrcc]


def describe_contact(
    centre: RescueCentre, distance_km: float, bearing_deg: float
) -> dict[str, Any]:
    """One centre, shaped for an API response and for a human reading it aloud."""
    return {
        "name": centre.name,
        "kind": centre.kind,
        "coordinates": {"lat": centre.lat, "lon": centre.lon},
        "distance_km": distance_km,
        "bearing_from_incident_deg": bearing_deg,
        "state": centre.state,
        "sea_area": centre.sea_area,
        "coordinating_mrcc": centre.mrcc,
        "telephone": centre.dial(),
        "email": list(centre.email),
        "fax": [f"+91-{f}" for f in centre.fax],
        "inmarsat_c": centre.inmarsat_c,
        "aftn": centre.aftn,
        "position_note": (
            "Position geocoded to the port this centre is named for, not to the building. "
            "Good to about a kilometre."
        ),
    }


def summary() -> dict[str, Any]:
    """What the register contains, for a capability endpoint."""
    return {
        "centres": len(CENTRES),
        "mrcc": [c.name for c in CENTRES if c.is_mrcc],
        "mrsc": sum(1 for c in CENTRES if not c.is_mrcc),
        "distress_number": MSAR_DISTRESS_NUMBER,
        "national_emergency_number": NATIONAL_EMERGENCY_NUMBER,
        "sea_areas": sorted({c.sea_area for c in CENTRES}),
        "source": NMSAR_CITATION.model_dump(mode="json"),
        "positions": POSITION_CITATION.model_dump(mode="json"),
    }
