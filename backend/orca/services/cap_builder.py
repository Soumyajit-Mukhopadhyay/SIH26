"""CAP 1.2 alert construction.

Common Alerting Protocol 1.2 is the OASIS standard NDMA's SACHET and IMD both
publish in. Emitting it means an ORCA advisory can be consumed by the systems
that already exist — a state disaster management authority's dashboard, a
cell-broadcast gateway — without anyone writing an adapter for us.

Two things this module is careful about, because getting either wrong makes the
output *look* like CAP while being unusable:

* **The namespace and element order are fixed by the schema.** CAP 1.2 uses a
  sequence, not a choice, so ``<info>`` children must appear in the order the XSD
  declares. A validator rejects a correctly-spelled document with shuffled
  elements.
* **``identifier`` must be globally unique and never reused**, and ``sent`` must
  carry a timezone offset. An alert with a colliding identifier is treated as a
  duplicate and silently dropped by downstream systems.

ORCA's own honesty requirement rides along: every alert carries the rule engine's
verdict, the thresholds version, and a ``<parameter>`` naming the derivation, so
a recipient can tell an ORCA advisory from an IMD bulletin.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from xml.etree import ElementTree as ET

from orca.provenance import utcnow

CAP_NAMESPACE = "urn:oasis:names:tc:emergency:cap:1.2"

#: ORCA's identifier prefix. A recipient can filter on it, and it makes clear at
#: a glance that the alert is not from IMD or NDMA.
IDENTIFIER_PREFIX = "orca.sih2026"

Urgency = Literal["Immediate", "Expected", "Future", "Past", "Unknown"]
Severity = Literal["Extreme", "Severe", "Moderate", "Minor", "Unknown"]
Certainty = Literal["Observed", "Likely", "Possible", "Unlikely", "Unknown"]
Status = Literal["Actual", "Exercise", "System", "Test", "Draft"]
MsgType = Literal["Alert", "Update", "Cancel", "Ack", "Error"]


@dataclass(slots=True)
class Area:
    """One affected area. CAP allows a polygon, a circle, or a geocode."""

    description: str
    #: Closed ring of (lat, lon) pairs. CAP polygons are lat,lon — the opposite of
    #: GeoJSON's lon,lat, and swapping them puts the alert in the wrong ocean.
    polygon: list[tuple[float, float]] | None = None
    #: (lat, lon, radius_km).
    circle: tuple[float, float, float] | None = None
    geocodes: dict[str, str] = field(default_factory=dict)

    def to_xml(self, parent: ET.Element) -> None:
        area = ET.SubElement(parent, "area")
        ET.SubElement(area, "areaDesc").text = self.description
        if self.polygon:
            ring = list(self.polygon)
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            ET.SubElement(area, "polygon").text = " ".join(
                f"{lat:.4f},{lon:.4f}" for lat, lon in ring
            )
        if self.circle:
            lat, lon, radius_km = self.circle
            ET.SubElement(area, "circle").text = f"{lat:.4f},{lon:.4f} {radius_km:.1f}"
        for name, value in self.geocodes.items():
            geocode = ET.SubElement(area, "geocode")
            ET.SubElement(geocode, "valueName").text = name
            ET.SubElement(geocode, "value").text = value


@dataclass(slots=True)
class Info:
    """One ``<info>`` block: the alert in one language."""

    headline: str
    description: str
    instruction: str
    event: str
    urgency: Urgency
    severity: Severity
    certainty: Certainty
    language: str = "en-IN"
    categories: list[str] = field(default_factory=lambda: ["Met", "Safety"])
    response_types: list[str] = field(default_factory=lambda: ["Avoid"])
    sender_name: str = "ORCA (Marine EcOsystem Reasoning with Collaborative Agents)"
    effective: datetime | None = None
    onset: datetime | None = None
    expires: datetime | None = None
    web: str | None = None
    contact: str | None = None
    parameters: dict[str, str] = field(default_factory=dict)
    areas: list[Area] = field(default_factory=list)

    def to_xml(self, parent: ET.Element) -> None:
        info = ET.SubElement(parent, "info")
        # The XSD declares a sequence, so this order is not stylistic. A
        # validator rejects the same elements in a different order.
        ET.SubElement(info, "language").text = self.language
        for category in self.categories:
            ET.SubElement(info, "category").text = category
        ET.SubElement(info, "event").text = self.event
        for response in self.response_types:
            ET.SubElement(info, "responseType").text = response
        ET.SubElement(info, "urgency").text = self.urgency
        ET.SubElement(info, "severity").text = self.severity
        ET.SubElement(info, "certainty").text = self.certainty
        if self.effective:
            ET.SubElement(info, "effective").text = _cap_time(self.effective)
        if self.onset:
            ET.SubElement(info, "onset").text = _cap_time(self.onset)
        if self.expires:
            ET.SubElement(info, "expires").text = _cap_time(self.expires)
        ET.SubElement(info, "senderName").text = self.sender_name
        ET.SubElement(info, "headline").text = self.headline
        ET.SubElement(info, "description").text = self.description
        ET.SubElement(info, "instruction").text = self.instruction
        if self.web:
            ET.SubElement(info, "web").text = self.web
        if self.contact:
            ET.SubElement(info, "contact").text = self.contact
        for name, value in self.parameters.items():
            parameter = ET.SubElement(info, "parameter")
            ET.SubElement(parameter, "valueName").text = name
            ET.SubElement(parameter, "value").text = value
        for area in self.areas:
            area.to_xml(info)


@dataclass(slots=True)
class Alert:
    """A complete CAP 1.2 alert."""

    infos: list[Info]
    identifier: str = field(default_factory=lambda: new_identifier())
    sender: str = "orca@sih2026.in"
    sent: datetime = field(default_factory=utcnow)
    status: Status = "Actual"
    msg_type: MsgType = "Alert"
    scope: str = "Public"
    references: str | None = None
    note: str | None = None

    def to_xml(self) -> ET.Element:
        alert = ET.Element("alert", {"xmlns": CAP_NAMESPACE})
        ET.SubElement(alert, "identifier").text = self.identifier
        ET.SubElement(alert, "sender").text = self.sender
        ET.SubElement(alert, "sent").text = _cap_time(self.sent)
        ET.SubElement(alert, "status").text = self.status
        ET.SubElement(alert, "msgType").text = self.msg_type
        ET.SubElement(alert, "scope").text = self.scope
        if self.references:
            ET.SubElement(alert, "references").text = self.references
        if self.note:
            ET.SubElement(alert, "note").text = self.note
        for info in self.infos:
            info.to_xml(alert)
        return alert

    def to_string(self, *, pretty: bool = True) -> str:
        element = self.to_xml()
        if pretty:
            ET.indent(element, space="  ")
        body = ET.tostring(element, encoding="unicode", xml_declaration=False)
        return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}'


def new_identifier() -> str:
    """A globally unique, never-reused identifier.

    A colliding identifier makes downstream systems treat the alert as a
    duplicate and drop it silently, so this is a uuid4 rather than anything
    derived from content or a counter.
    """
    return f"{IDENTIFIER_PREFIX}.{utcnow():%Y%m%dT%H%M%S}.{uuid.uuid4().hex[:12]}"


def _cap_time(moment: datetime) -> str:
    """CAP requires an explicit timezone offset — ``Z`` is not permitted.

    Times are stored in UTC throughout ORCA, so this renders ``+00:00``.
    """
    if moment.tzinfo is None:
        from datetime import UTC

        moment = moment.replace(tzinfo=UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S%z")[:-2] + ":" + moment.strftime("%z")[-2:]


# --------------------------------------------------------------------------- #
# ORCA-specific construction
# --------------------------------------------------------------------------- #

#: Rule-engine verdict -> CAP severity/urgency/certainty. Mapped explicitly
#: rather than guessed, because CAP severity has an agreed meaning to the
#: agencies that consume it and inflating it is how an alerting channel loses
#: credibility.
_VERDICT_TO_CAP: dict[str, tuple[Severity, Urgency, Certainty]] = {
    "NO-GO": ("Severe", "Immediate", "Likely"),
    "CAUTION": ("Moderate", "Expected", "Possible"),
    "UNVERIFIABLE": ("Minor", "Unknown", "Unknown"),
    "GO": ("Minor", "Future", "Unlikely"),
}


def from_risk(
    risk: dict[str, Any],
    *,
    lat: float,
    lon: float,
    place: str | None = None,
    radius_km: float = 25.0,
    language: str = "en-IN",
    translated: dict[str, str] | None = None,
    expires_hours: float = 6.0,
) -> Alert:
    """Build a CAP 1.2 alert from a rule-engine verdict.

    ``translated`` optionally supplies ``{"headline", "description", "instruction"}``
    in a second language, which becomes a second ``<info>`` block — CAP's own
    mechanism for multilingual alerts, and better than sending two alerts.
    """
    verdict = str(risk.get("verdict", "UNVERIFIABLE"))
    severity, urgency, certainty = _VERDICT_TO_CAP.get(verdict, ("Unknown", "Unknown", "Unknown"))

    vetoes: list[str] = list(risk.get("vetoes") or [])
    index = risk.get("index")
    where = place or f"{lat:.3f}N {lon:.3f}E"

    headline = f"{verdict} for small craft near {where}"
    if verdict == "NO-GO" and vetoes:
        headline = f"NO-GO near {where}: {vetoes[0]}"

    description_parts = [
        f"ORCA deterministic risk assessment for {where}: {verdict}"
        + (f", safety index {index}/100." if index is not None else "."),
    ]
    if vetoes:
        description_parts.append("Hard limits exceeded: " + "; ".join(vetoes) + ".")
    for component in risk.get("components", []):
        if component.get("value") is not None:
            description_parts.append(
                f"{component['name']}: {component['value']} {component['unit']} "
                f"(class limit {component['limit']} {component['unit']})."
            )

    instruction = (
        "Do not put to sea in a vessel of this class. Await an improvement and confirm with "
        "your fisheries office or the coastal VHF channel."
        if verdict == "NO-GO"
        else (
            "Conditions are marginal. Shorten your window, stay within VHF range, and monitor "
            "the forecast."
            if verdict == "CAUTION"
            else (
                "ORCA could not verify conditions. Confirm with your fisheries office or the "
                "coastal VHF channel before sailing."
                if verdict == "UNVERIFIABLE"
                else "Conditions are within your vessel class limits. Normal precautions apply."
            )
        )
    )

    parameters = {
        # These four are what let a recipient tell an ORCA advisory apart from an
        # official bulletin, and audit it afterwards.
        "verdict_source": str(risk.get("verdict_source", "rule_engine")),
        "thresholds_version": str(risk.get("thresholds_version", "unknown")),
        "safety_index": str(index),
        "boat_class": str(risk.get("boat_class_code", "unknown")),
        "confidence": str(risk.get("confidence", "unknown")),
        "data_age_hours": str(risk.get("data_age_hours", "unknown")),
        "disclaimer": (
            "ORCA supplements, never replaces, official IMD and INCOIS bulletins. The verdict "
            "is computed by a deterministic, versioned rule engine, not by a language model."
        ),
    }
    if risk.get("escalation_message"):
        parameters["escalation"] = str(risk["escalation_message"])

    area = Area(
        description=f"Sea area within {radius_km:.0f} km of {where}",
        circle=(lat, lon, radius_km),
        geocodes={"ORCA:AOI": "indian-eez"},
    )

    now = utcnow()
    from datetime import timedelta

    infos = [
        Info(
            headline=headline,
            description=" ".join(description_parts),
            instruction=instruction,
            event="Small craft marine safety advisory",
            urgency=urgency,
            severity=severity,
            certainty=certainty,
            language=language,
            effective=now,
            onset=now,
            expires=now + timedelta(hours=expires_hours),
            parameters=parameters,
            areas=[area],
        )
    ]

    if translated:
        infos.append(
            Info(
                headline=translated.get("headline", headline),
                description=translated.get("description", " ".join(description_parts)),
                instruction=translated.get("instruction", instruction),
                event=translated.get("event", "Small craft marine safety advisory"),
                urgency=urgency,
                severity=severity,
                certainty=certainty,
                language=translated.get("language", "ta-IN"),
                effective=now,
                onset=now,
                expires=now + timedelta(hours=expires_hours),
                parameters=parameters,
                areas=[area],
            )
        )

    return Alert(
        infos=infos,
        status="Actual",
        msg_type="Alert",
        note=(
            "Generated by ORCA, an experimental decision-support system built for Smart India "
            "Hackathon 2026. Not a substitute for an official IMD or INCOIS bulletin."
        ),
    )


def from_geofence(
    event: dict[str, Any],
    *,
    lat: float,
    lon: float,
    language: str = "en-IN",
) -> Alert:
    """A CAP alert for a boundary crossing.

    Severity is ``Severe`` rather than ``Extreme``: crossing an IMBL is a serious
    legal and safety exposure, but ``Extreme`` is reserved in practice for
    extraordinary threat to life, and using it here would devalue it.
    """
    from datetime import timedelta

    now = utcnow()
    name = str(event.get("name", "a maritime boundary"))

    return Alert(
        infos=[
            Info(
                headline=f"Maritime boundary crossed: {name}",
                description=(
                    f"ORCA detected a crossing of {name} at {lat:.4f}N {lon:.4f}E. "
                    f"{event.get('consequence', '')}"
                ),
                instruction=(
                    "Alter course to return to Indian waters immediately and contact the "
                    "nearest Coast Guard station on VHF channel 16."
                ),
                event="Maritime boundary crossing",
                urgency="Immediate",
                severity="Severe",
                certainty="Observed",
                language=language,
                categories=["Security", "Safety"],
                response_types=["Evacuate"],
                effective=now,
                onset=now,
                expires=now + timedelta(hours=3),
                parameters={
                    "fence": str(event.get("fence", "unknown")),
                    "authority": str(event.get("authority", "unknown")),
                    "detection": "shapely track-segment intersection against Marine Regions v12",
                    "verdict_source": "rule_engine",
                },
                areas=[
                    Area(
                        description=f"Vicinity of the crossing point on {name}",
                        circle=(lat, lon, 10.0),
                    )
                ],
            )
        ],
        note="Generated by ORCA for Smart India Hackathon 2026.",
    )


def validate(xml: str) -> dict[str, Any]:
    """Structural validation, without needing the XSD on disk.

    Checks what actually breaks downstream consumers: the namespace, the required
    elements, the ``<info>`` child order that the sequence-based schema demands,
    and that ``sent`` carries an offset.
    """
    problems: list[str] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return {"valid": False, "problems": [f"not well-formed XML: {exc}"]}

    namespace = root.tag[1:].split("}")[0] if root.tag.startswith("{") else None
    if namespace != CAP_NAMESPACE:
        problems.append(f"root namespace is {namespace!r}, expected {CAP_NAMESPACE!r}")
    if root.tag.split("}")[-1] != "alert":
        problems.append(f"root element is {root.tag!r}, expected 'alert'")

    def child_text(parent: ET.Element, name: str) -> str | None:
        found = parent.find(f"{{{CAP_NAMESPACE}}}{name}")
        return found.text if found is not None else None

    for required in ("identifier", "sender", "sent", "status", "msgType", "scope"):
        if child_text(root, required) is None:
            problems.append(f"missing required <{required}>")

    sent = child_text(root, "sent")
    if sent and not (sent.endswith(("+00:00", "Z")) or "+" in sent[10:] or "-" in sent[10:]):
        problems.append(f"<sent> must carry a timezone offset, got {sent!r}")

    infos = root.findall(f"{{{CAP_NAMESPACE}}}info")
    if not infos:
        problems.append("no <info> block")

    # The XSD sequence. Elements present must appear in this relative order.
    order = [
        "language",
        "category",
        "event",
        "responseType",
        "urgency",
        "severity",
        "certainty",
        "audience",
        "eventCode",
        "effective",
        "onset",
        "expires",
        "senderName",
        "headline",
        "description",
        "instruction",
        "web",
        "contact",
        "parameter",
        "resource",
        "area",
    ]
    for i, info in enumerate(infos):
        names = [child.tag.split("}")[-1] for child in info]
        positions = [order.index(n) for n in names if n in order]
        if positions != sorted(positions):
            problems.append(f"<info>[{i}] children are out of the CAP 1.2 sequence order: {names}")
        for required in ("urgency", "severity", "certainty"):
            if child_text(info, required) is None:
                problems.append(f"<info>[{i}] missing required <{required}>")

    return {
        "valid": not problems,
        "problems": problems,
        "info_blocks": len(infos),
        "languages": [child_text(info, "language") for info in infos],
        "namespace": namespace,
    }
