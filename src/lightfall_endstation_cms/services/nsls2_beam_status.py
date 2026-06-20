"""NSLS-II storage-ring status service for the CMS (11-BM) endstation.

Subscribes to NSLS-II accelerator status PVs over EPICS Channel Access and
exposes the current ring state (current, lifetime, mode, beam availability)
to the Lightfall status bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# --- Source PVs -----------------------------------------------------------

SR_CURRENT_PV = "SR:C03-BI{DCCT:1}I:Real-I"
SR_LIFETIME_PV = "SR:C03-BI{DCCT:1}Lifetime-I"
SR_MODE_PV = "SR-OPS{}Mode-Sts"
SR_SHUTTER_PV = "SR-OPS{}Shutter-Sts"
TOPOFF_PV = "INJ{TOC}OpControl-Sel"
NEXT_INJ_PV = "INJ{TOC-SM}Cnt:Next-I"
OPS_MSG1_PV = "OP{1}Message.VAL$"
OPS_MSG2_PV = "OP{2}Message.VAL$"

ALL_PVS: tuple[str, ...] = (
    SR_CURRENT_PV,
    SR_LIFETIME_PV,
    SR_MODE_PV,
    SR_SHUTTER_PV,
    TOPOFF_PV,
    NEXT_INJ_PV,
    OPS_MSG1_PV,
    OPS_MSG2_PV,
)

# Enum / long-string PVs that should be requested as strings over CA.
STRING_PVS: frozenset[str] = frozenset(
    {SR_MODE_PV, SR_SHUTTER_PV, TOPOFF_PV, OPS_MSG1_PV, OPS_MSG2_PV}
)

# Substring (case-insensitive) in SR-OPS{}Shutter-Sts meaning beam is available.
_SHUTTER_OPEN_TOKEN = "open"


def shutter_means_available(value: object) -> bool:
    """Return True if a shutter-status value indicates beam is available."""
    return _SHUTTER_OPEN_TOKEN in str(value).strip().lower()


def _safe_float(value: object) -> float:
    """Parse a float from a PV value, returning 0.0 on failure."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0.0


@dataclass
class NSLS2BeamData:
    """Structured NSLS-II storage-ring status."""

    beam_current: float = 0.0
    beam_available: bool = False
    shutter_status: str = ""
    mode: str = ""
    lifetime: float = 0.0
    topoff_state: str = ""
    next_injection: str = ""
    ops_message_1: str = ""
    ops_message_2: str = ""
    timestamp: datetime | None = None

    @property
    def ops_message(self) -> str:
        """The two operations messages joined, dropping blank lines."""
        return "\n".join(m for m in (self.ops_message_1, self.ops_message_2) if m)


def apply_pv_value(data: NSLS2BeamData, pv_name: str, value: object) -> None:
    """Apply a single decoded PV value to ``data`` in place.

    Pure mapping logic: no Qt, no network. ``value`` is an already-decoded
    Python scalar (float for numeric PVs, str for enum / message PVs).
    """
    if pv_name == SR_CURRENT_PV:
        data.beam_current = _safe_float(value)
    elif pv_name == SR_LIFETIME_PV:
        data.lifetime = _safe_float(value)
    elif pv_name == SR_MODE_PV:
        data.mode = str(value)
    elif pv_name == SR_SHUTTER_PV:
        data.shutter_status = str(value)
        data.beam_available = shutter_means_available(value)
    elif pv_name == TOPOFF_PV:
        data.topoff_state = str(value)
    elif pv_name == NEXT_INJ_PV:
        data.next_injection = str(value)
    elif pv_name == OPS_MSG1_PV:
        data.ops_message_1 = str(value)
    elif pv_name == OPS_MSG2_PV:
        data.ops_message_2 = str(value)
