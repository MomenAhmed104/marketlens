"""Deterministic specification categories (Stage 6.1).

A specification is categorised from its name, and for an ambiguous name such as
"Power" or "Output", from its value:

- POWER_SUPPLY: "Power supply", "Battery", "Power: 9V AC Adapter/Battery".
- OUTPUT_POWER: "Output power", "Wattage", "Power: 50W". Output wattage is never
  inferred from a supply voltage; a value that mixes watts with supply wording
  is OTHER.
- CONTROLS: "Controls", "Knobs", "Switches". Never evidence of the type of
  electronics (active/passive, preamp).
- PICKUP_CONFIGURATION: a pickup spec whose value states a layout code (HSS,
  SSS, HH, HSH, SSH), or a "Pickup configuration / layout" spec.
- ELECTRONICS: "Electronics", "Preamp", "Pickup system", "Pickups", "Neck pickup".
- OTHER: anything else.

Only ELECTRONICS and PICKUP_CONFIGURATION specs take part in the electronics
matching rules. Nothing here turns missing evidence into a confirmation.
"""

from __future__ import annotations

import re

from app.agents.intelligence.schemas import SpecCategory as C


def _norm(text: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9/&+]+", " ", (text or "").casefold()).split())


_WATTS = re.compile(r"\b\d+(?:\.\d+)?\s?(?:w|watts?)\b", re.IGNORECASE)
_SUPPLY_VALUE = re.compile(
    r"\b\d+(?:\.\d+)?\s?v\b|\bvolts?\b|\badapt(?:o|e)r\b|\bbatter(?:y|ies)\b|\bpsu\b|\bpower\s+supply\b"
    r"|\b\d+\s?m?a\b|\bmilliamps?\b|\b(?:ac|dc)\b|\bmains\b|\busb\b",
    re.IGNORECASE,
)
_LAYOUT = re.compile(r"(?<![A-Za-z0-9])(?:HSS|SSS|HSH|HH|SSH)(?![A-Za-z0-9])", re.IGNORECASE)

_SUPPLY_NAMES = {"power supply", "power requirements", "power requirement", "power adapter", "power adaptor",
                 "adapter", "adaptor", "battery", "batteries", "battery life", "voltage", "current draw",
                 "current consumption", "power consumption", "psu"}  # fmt: skip
_OUTPUT_NAMES = {"output power", "power output", "wattage", "watts", "rms power", "rms output", "amplifier power",
                 "amp power"}  # fmt: skip
_CONTROL_NAMES = {"controls", "control", "knobs", "switches", "switching", "control layout", "controls layout"}
_ELECTRONICS_NAMES = {"electronics", "preamp", "pre amp", "onboard electronics", "on board electronics",
                      "pickup system", "electronics type", "active/passive", "eq", "onboard eq"}  # fmt: skip
_LAYOUT_NAMES = {"pickup configuration", "pickup layout", "pickup config", "pickup combination"}
_PICKUP_WORDS = {"pickup", "pickups"}
# Control hardware named after the pickups ("Pickup selector: 3-way", "Pickup switch: 5-way blade",
# "Pickup blend") is a control, not electronics evidence (Stage 6.1.1).
_CONTROL_WORDS = {"selector", "selectors", "switch", "switches", "switching", "toggle", "toggles", "blend",
                  "knob", "knobs", "control", "controls", "pot", "pots", "potentiometer", "potentiometers"}  # fmt: skip

# What makes an ELECTRONICS spec value actual evidence of electronics (an electro-acoustic preamp or
# pickup system, active/passive): a device or type, or a known electronics maker. Generic control
# lists such as "Volume, Tone" are not.
_MEANINGFUL_ELECTRONICS = re.compile(
    r"\b(?:pre[\s-]?amp|pickups?|piezo|transducers?|under[\s-]?saddle|ust|magnetic|condenser|microphone|mic"
    r"|humbuckers?|single[\s-]?coils?|p[\s-]?90s?|active|passive|\d[\s-]?band|eq|equali[sz]er|tuner"
    r"|fishman|baggs|b[\s-]?band|shadow|presys|sonitone|isys|k\s?&\s?k|emg|seymour|duncan|dimarzio|bartolini"
    r"|aguilar|nordstrand|custombucker|probucker|alnico|ceramic)\b",
    re.IGNORECASE,
)


def is_meaningful_electronics(value: str | None) -> bool:
    """Whether an ELECTRONICS spec value names an actual device, type or maker ("Fishman Presys II",
    "Active 3-band EQ", "Under-saddle piezo"), as opposed to generic controls ("Volume, Tone")."""
    return bool(_MEANINGFUL_ELECTRONICS.search(value or ""))


def spec_category(name: str | None, value: str | None) -> C:
    n = _norm(name).strip(" :")
    v = value or ""
    words = set(n.split())
    if n in _LAYOUT_NAMES:
        return C.PICKUP_CONFIGURATION
    if words & _PICKUP_WORDS:
        if words & _CONTROL_WORDS:
            return C.CONTROLS
        return C.PICKUP_CONFIGURATION if _LAYOUT.search(v) else C.ELECTRONICS
    if n in _ELECTRONICS_NAMES:
        return C.ELECTRONICS
    if n in _CONTROL_NAMES:
        return C.CONTROLS
    if n in _SUPPLY_NAMES:
        return C.POWER_SUPPLY
    if n in _OUTPUT_NAMES:
        return C.OUTPUT_POWER
    if n in ("power", "output"):  # decided by the value; mixed or unclear stays OTHER
        watts, supply = bool(_WATTS.search(v)), bool(_SUPPLY_VALUE.search(v))
        if n == "power" and supply and not watts:
            return C.POWER_SUPPLY
        if watts and not supply:
            return C.OUTPUT_POWER
    return C.OTHER


ELECTRONICS_CATEGORIES = frozenset({C.ELECTRONICS, C.PICKUP_CONFIGURATION})
