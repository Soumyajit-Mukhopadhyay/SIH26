"""India's fishing harbours, as the Department of Fisheries lists them.

## Where this list comes from

The **PMMSY Fishing Harbour Map** published by the Department of Fisheries,
Ministry of Fisheries, Animal Husbandry and Dairying — the harbours funded for
construction, modernisation or dredging under the Pradhan Mantri Matsya Sampada
Yojana. It is the government's own register of the harbours that matter, which
is a far better basis for a coastal advisory than a list assembled from whatever
happened to be searchable.

OpenStreetMap was checked first, as it was for the rescue centres, and is again
unusable: a full Overpass query for `seamark:harbour:category=fishing` across the
Indian box returned **ten features, six of them named, three of those in Sri
Lanka**. Indian marine tagging in OSM is too thin to build an advisory on.

## The coordinates are ORCA's, and three kinds of caveat apply

The PMMSY map gives names and districts, **not coordinates**. Each harbour was
geocoded through Nominatim and then probed against Open-Meteo's marine model to
confirm a sea cell answers there.

1. **Sixty of sixty-three resolved.** Navabandar (Gir Somnath), Mudhunagar
   (Cuddalore) and Azhagankuppam (Villupuram) could not be found under any
   spelling tried and are recorded in :data:`UNRESOLVED` rather than dropped
   silently, so the next person knows the absence was checked.
2. **Five sit at a proxy position.** Nominatim does not know "Madhwad" but does
   know Madhavpur, 25 km along the same Saurashtra shore. At that separation the
   sea state is effectively identical, so the row is worth having — but
   `position_proxy` says so on the record rather than letting the name imply a
   precision that is not there.
3. **These are harbour positions, not offshore points.** Open-Meteo's marine
   endpoint is queried with `cell_selection="sea"`, so it answers from the
   nearest sea cell; wind and visibility come from the forecast endpoint with
   `cell_selection="land"`, so they are the value at the coast rather than
   twenty miles out. That is the same combination the router and every point
   verdict already use, so the board is consistent with the rest of ORCA — but a
   coastal wind is not an offshore wind, and an advisory built on it is about
   conditions at the harbour mouth.

## Order

Stored in coastal order: down the west coast from Kutch to Kanyakumari, then up
the east coast to the Sundarbans. That is the order a boat passes them and the
order an advisory reads in, and it is what makes a run of NO-GO harbours legible
as a stretch of coast rather than as a scatter of rows.

The east coast is ordered by latitude only as far as Odisha. In the Bengal delta
the shore turns and runs west to east, and plain latitude put Frasergunj east of
Shankarpur ahead of it -- backwards along the shore, and enough to split a
contiguous stretch into two runs. Above 21 N the order follows longitude.
"""

from __future__ import annotations

from dataclasses import dataclass

from orca.provenance import Citation, Provider

PMMSY_CITATION = Citation(
    label="PMMSY Fishing Harbour Map — Department of Fisheries, Government of India",
    provider="Department of Fisheries, Ministry of Fisheries, Animal Husbandry and Dairying",
    url="https://pmmsy.dof.gov.in/static/harbour/harbours.html",
    identifier="Pradhan Mantri Matsya Sampada Yojana fishing harbour register",
    quote=(
        "Fishing harbours and fish landing centres sanctioned for construction, modernisation "
        "or dredging under PMMSY."
    ),
)

POSITION_CITATION = Citation(
    label="Harbour positions geocoded from OpenStreetMap Nominatim",
    provider=Provider.ORCA,
    url="https://nominatim.openstreetmap.org/",
    quote=(
        "The PMMSY register gives names and districts, not coordinates. Each position was "
        "geocoded and then confirmed against Open-Meteo's marine model, which answers from the "
        "nearest sea cell. These are harbour positions, not offshore points."
    ),
)


@dataclass(frozen=True, slots=True)
class Harbour:
    """One fishing harbour on the PMMSY register."""

    name: str
    district: str
    state: str
    lat: float
    lon: float
    #: "west" or "east". Sets the coastal ordering and the offshore direction.
    coast: str
    #: Set when the position is a nearby place rather than the harbour itself.
    position_proxy: str | None = None


#: Could not be geocoded under any spelling tried. Recorded so nobody assumes
#: the register is complete, and so the absence reads as checked rather than
#: overlooked.
UNRESOLVED: dict[str, str] = {
    "Navabandar": "Gir Somnath, Gujarat",
    "Mudhunagar": "Cuddalore, Tamil Nadu",
    "Azhagankuppam": "Villupuram, Tamil Nadu",
}

#: In coastal order — Kutch round to the Sundarbans.
HARBOURS: tuple[Harbour, ...] = (
    Harbour(
        "Jakhau",
        "Kutch",
        "Gujarat",
        23.2197985,
        68.7142064,
        "west",
    ),
    Harbour(
        "Porbandar",
        "Porbandar",
        "Gujarat",
        21.6505229,
        69.6565371,
        "west",
    ),
    Harbour(
        "Madhwad",
        "Gir Somnath",
        "Gujarat",
        21.2556395,
        69.9653396,
        "west",
        position_proxy="Madhavpur, Porbandar — the nearest place Nominatim knows on the same shore",
    ),
    Harbour(
        "Mangrol",
        "Junagadh",
        "Gujarat",
        21.1241507,
        70.1199203,
        "west",
    ),
    Harbour(
        "Veraval",
        "Gir Somnath",
        "Gujarat",
        20.9101099,
        70.365279,
        "west",
    ),
    Harbour(
        "Sutrapada",
        "Gir Somnath",
        "Gujarat",
        20.8428692,
        70.4799081,
        "west",
    ),
    Harbour(
        "Vanakbara",
        "Diu",
        "Daman and Diu",
        20.7161868,
        70.8758436,
        "west",
    ),
    Harbour(
        "Satpati",
        "Palghar",
        "Maharashtra",
        19.7221423,
        72.7053909,
        "west",
    ),
    Harbour(
        "Mallet Bunder",
        "Sindhudurg",
        "Maharashtra",
        18.9579267,
        72.8473081,
        "west",
    ),
    Harbour(
        "Sassoon Dock",
        "Mumbai",
        "Maharashtra",
        18.9144076,
        72.8242278,
        "west",
    ),
    Harbour(
        "Karanja",
        "Raigad",
        "Maharashtra",
        18.8482919,
        72.947498,
        "west",
    ),
    Harbour(
        "Bharadkhol",
        "Raigad",
        "Maharashtra",
        18.1388322,
        72.9842001,
        "west",
    ),
    Harbour(
        "Jeevana",
        "Raigad",
        "Maharashtra",
        18.055747,
        73.033543,
        "west",
        position_proxy="Shrivardhan, Raigad — the nearest town on the same stretch",
    ),
    Harbour(
        "Harnai",
        "Ratnagiri",
        "Maharashtra",
        17.8125695,
        73.0924809,
        "west",
    ),
    Harbour(
        "Sakhari Nate",
        "Ratnagiri",
        "Maharashtra",
        16.6343054,
        73.3618494,
        "west",
    ),
    Harbour(
        "Anandwadi",
        "Sindhudurg",
        "Maharashtra",
        16.320263,
        73.721298,
        "west",
    ),
    Harbour(
        "Amadalli",
        "Uttara Kannada",
        "Karnataka",
        14.7619646,
        74.2202022,
        "west",
    ),
    Harbour(
        "Tadri",
        "Uttara Kannada",
        "Karnataka",
        14.525444,
        74.3535818,
        "west",
    ),
    Harbour(
        "Gangolli",
        "Udupi",
        "Karnataka",
        13.6528405,
        74.6703777,
        "west",
    ),
    Harbour(
        "Malpe",
        "Udupi",
        "Karnataka",
        13.3507423,
        74.7036914,
        "west",
    ),
    Harbour(
        "Hejamadi Kodi",
        "Udupi",
        "Karnataka",
        13.1091716,
        74.778277,
        "west",
    ),
    Harbour(
        "Kulai",
        "Dakshina Kannada",
        "Karnataka",
        12.9674838,
        74.8063798,
        "west",
    ),
    Harbour(
        "Mangalore",
        "Dakshina Kannada",
        "Karnataka",
        12.8698101,
        74.8430082,
        "west",
    ),
    Harbour(
        "Kasaragod",
        "Kasaragod",
        "Kerala",
        12.5035577,
        74.9907022,
        "west",
    ),
    Harbour(
        "Koyilandy",
        "Kozhikode",
        "Kerala",
        11.4383564,
        75.696891,
        "west",
    ),
    Harbour(
        "Puthiyappa",
        "Kozhikode",
        "Kerala",
        11.3184577,
        75.747209,
        "west",
    ),
    Harbour(
        "Beypore",
        "Kozhikode",
        "Kerala",
        11.1790668,
        75.8101768,
        "west",
    ),
    Harbour(
        "Ponnani",
        "Malappuram",
        "Kerala",
        10.7800691,
        75.9189338,
        "west",
    ),
    Harbour(
        "Chettuva",
        "Thrissur",
        "Kerala",
        10.5251791,
        76.0424382,
        "west",
    ),
    Harbour(
        "Thoppumpady",
        "Ernakulam",
        "Kerala",
        9.9385142,
        76.2627339,
        "west",
    ),
    Harbour(
        "Arthunkal",
        "Alappuzha",
        "Kerala",
        9.66114,
        76.2994454,
        "west",
    ),
    Harbour(
        "Kayamkulam",
        "Alappuzha",
        "Kerala",
        9.1723603,
        76.500061,
        "west",
    ),
    Harbour(
        "Muthalapozhi",
        "Thiruvananthapuram",
        "Kerala",
        8.6308825,
        76.7870362,
        "west",
    ),
    Harbour(
        "Thengapattinam",
        "Kanyakumari",
        "Tamil Nadu",
        8.2387015,
        77.1730989,
        "west",
    ),
    Harbour(
        "Colachel",
        "Kanyakumari",
        "Tamil Nadu",
        8.1752656,
        77.2519232,
        "west",
    ),
    Harbour(
        "Pazhayar",
        "Mayiladuthurai",
        "Tamil Nadu",
        8.1789044,
        77.4501634,
        "east",
    ),
    Harbour(
        "Tharuvaikulam",
        "Thoothukudi",
        "Tamil Nadu",
        8.8938845,
        78.1720442,
        "east",
    ),
    Harbour(
        "Vellapallam",
        "Nagapattinam",
        "Tamil Nadu",
        10.52144,
        79.83842,
        "east",
    ),
    Harbour(
        "Arcottuthurai",
        "Nagapattinam",
        "Tamil Nadu",
        10.7463387,
        79.8464759,
        "east",
        position_proxy="Akkaraipettai, Nagapattinam — the adjacent landing centre",
    ),
    Harbour(
        "Karaikal",
        "Karaikal",
        "Puducherry",
        10.9099808,
        79.8474812,
        "east",
    ),
    Harbour(
        "Tharangambadi",
        "Mayiladuthurai",
        "Tamil Nadu",
        11.029929,
        79.852196,
        "east",
    ),
    Harbour(
        "Thengaithittu",
        "Puducherry",
        "Puducherry",
        11.9145252,
        79.8152719,
        "east",
    ),
    Harbour(
        "Chennai",
        "Chennai",
        "Tamil Nadu",
        13.1254513,
        80.2957424,
        "east",
    ),
    Harbour(
        "Thiruvottiyur Kuppam",
        "Tiruvallur",
        "Tamil Nadu",
        13.1722287,
        80.3045553,
        "east",
    ),
    Harbour(
        "Juvvaladinne",
        "Nellore",
        "Andhra Pradesh",
        14.8075951,
        80.0703968,
        "east",
    ),
    Harbour(
        "Kothapatnam",
        "Prakasam",
        "Andhra Pradesh",
        15.4459511,
        80.1661405,
        "east",
    ),
    Harbour(
        "Vodarevu",
        "Prakasam",
        "Andhra Pradesh",
        15.7975139,
        80.4109809,
        "east",
    ),
    Harbour(
        "Nizampatnam",
        "Bapatla",
        "Andhra Pradesh",
        15.9045922,
        80.6668354,
        "east",
    ),
    Harbour(
        "Machilipatnam",
        "Krishna",
        "Andhra Pradesh",
        16.1537774,
        81.1796924,
        "east",
    ),
    Harbour(
        "Uppada",
        "Kakinada",
        "Andhra Pradesh",
        17.0812722,
        82.3337972,
        "east",
    ),
    Harbour(
        "Pudimadaka",
        "Anakapalli",
        "Andhra Pradesh",
        17.4924469,
        83.0030712,
        "east",
    ),
    Harbour(
        "Visakhapatnam",
        "Visakhapatnam",
        "Andhra Pradesh",
        17.6935526,
        83.2921297,
        "east",
    ),
    Harbour(
        "Budugatlapalem",
        "Srikakulam",
        "Andhra Pradesh",
        18.5712126,
        84.3524478,
        "east",
        position_proxy="Bhavanapadu, Srikakulam — the adjacent landing centre",
    ),
    Harbour(
        "Astaranga",
        "Puri",
        "Odisha",
        19.9783703,
        86.3098519,
        "east",
    ),
    Harbour(
        "Paradip",
        "Jagatsinghpur",
        "Odisha",
        20.286946,
        86.6739979,
        "east",
    ),
    Harbour(
        "Chandipur",
        "Balasore",
        "Odisha",
        21.4755542,
        86.9650555,
        "east",
    ),
    Harbour(
        "Shankarpur",
        "Purba Medinipur",
        "West Bengal",
        21.6391458,
        87.5741159,
        "east",
    ),
    Harbour(
        "Petuaghat",
        "Purba Medinipur",
        "West Bengal",
        21.7253771,
        87.8106522,
        "east",
        position_proxy="Junput, Purba Medinipur — the nearest place on the same stretch",
    ),
    Harbour(
        "Kakdwip",
        "South 24 Parganas",
        "West Bengal",
        21.8610761,
        88.246636,
        "east",
    ),
    Harbour(
        "Frasergunj",
        "South 24 Parganas",
        "West Bengal",
        21.5806671,
        88.2554319,
        "east",
    ),
)

BY_NAME: dict[str, Harbour] = {h.name: h for h in HARBOURS}

STATES: tuple[str, ...] = tuple(dict.fromkeys(h.state for h in HARBOURS))
