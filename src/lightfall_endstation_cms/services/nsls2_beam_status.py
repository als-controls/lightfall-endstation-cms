"""NSLS-II storage-ring status service for the CMS (11-BM) endstation.

Subscribes to NSLS-II accelerator status PVs over EPICS Channel Access and
exposes the current ring state (current, lifetime, mode, beam availability)
to the Lightfall status bar.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Signal

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

# Short enum / status PVs requested as DBR_STRING over CA. NOTE: SR_SHUTTER_PV
# is NOT here -- it is a numeric PV (1.0 = open, 0.0 = closed), read natively
# and interpreted numerically (see shutter_means_available).
STRING_PVS: frozenset[str] = frozenset({SR_MODE_PV, TOPOFF_PV})

# Long-string ".VAL$" message PVs. DBR_STRING caps at 40 chars, so these are
# requested as a native CHAR waveform and assembled past the cap (see
# _decode_long_string).
LONG_STRING_PVS: frozenset[str] = frozenset({OPS_MSG1_PV, OPS_MSG2_PV})

# Enum-string form (case-insensitive) of a shutter status that means open.
_SHUTTER_OPEN_TOKEN = "open"


def shutter_means_available(value: object) -> bool:
    """Return True if a shutter-status value indicates beam is available.

    SR-OPS{}Shutter-Sts is numeric (1.0 = open, 0.0 = closed), so any nonzero
    value means beam is available. (It is sometimes read back as a DBR_STRING
    like ``"1.0000"``, which parses the same way.) An enum-string form
    containing "open" is also accepted so the check stays robust across
    deployments -- the previous code ONLY did the "open" substring test, so a
    numeric ``"1.0000"`` never matched and beam always read as Unavailable.
    """
    text = str(value).strip().lower()
    if _SHUTTER_OPEN_TOKEN in text:
        return True
    try:
        return float(value) != 0.0
    except (ValueError, TypeError):
        return False


def _safe_float(value: object) -> float:
    """Parse a float from a PV value, returning 0.0 on failure."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0.0


def _decode_long_string(data: object) -> str:
    """Assemble a CHAR-waveform long string ('.VAL$') into a Python str.

    A long-string field arrives as a NUL-terminated array of byte values;
    caproto may hand it over as a list/array of ints or as bytes. Cut at the
    first NUL and decode -- this is what lifts the 40-char DBR_STRING cap.
    """
    if isinstance(data, str):
        return data.split("\x00", 1)[0]
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    else:
        try:
            raw = bytes(int(c) & 0xFF for c in data)
        except TypeError:
            return str(data)
    nul = raw.find(0)
    if nul != -1:
        raw = raw[:nul]
    return raw.decode(errors="replace")


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


# Beam is "nominal" (healthy user operations) above these thresholds. When
# nominal the status bar shows just the green icon; below them (or when beam is
# unavailable) it also shows the current/lifetime text so operators notice
# off-nominal values at a glance. Full detail is always in the tooltip. Tunable.
NOMINAL_CURRENT_MA = 450.0
NOMINAL_LIFETIME_H = 8.9


def is_nominal(data: NSLS2BeamData) -> bool:
    """True when beam is available and both current and lifetime are above the
    nominal thresholds (healthy user operations)."""
    return (
        data.beam_available
        and data.beam_current > NOMINAL_CURRENT_MA
        and data.lifetime > NOMINAL_LIFETIME_H
    )


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
        data.beam_available = shutter_means_available(value)
        # Raw PV is numeric (1.0/0.0); show a human-readable label in the UI.
        data.shutter_status = "Open" if data.beam_available else "Closed"
    elif pv_name == TOPOFF_PV:
        data.topoff_state = str(value)
    elif pv_name == NEXT_INJ_PV:
        data.next_injection = str(value)
    elif pv_name == OPS_MSG1_PV:
        data.ops_message_1 = str(value)
    elif pv_name == OPS_MSG2_PV:
        data.ops_message_2 = str(value)


class NSLS2BeamStatusService(QObject):
    """Singleton service polling NSLS-II ring status PVs over Channel Access.

    Opens a caproto threading-client Context, subscribes to the status PVs,
    and emits Qt signals as values arrive. caproto callbacks fire on worker
    threads; the cross-thread signal emission is delivered on the GUI thread
    by Qt's auto (queued) connection.
    """

    status_changed = Signal(object)  # NSLS2BeamData
    connection_changed = Signal(bool)

    _instance: NSLS2BeamStatusService | None = None
    _singleton_lock = threading.RLock()

    def __init__(self) -> None:
        super().__init__()
        self._data: NSLS2BeamData | None = None
        self._data_lock = threading.RLock()
        self._context = None
        self._pvs: list = []
        self._subs: list = []
        self._connected_pvs: set[str] = set()
        self._conn_lock = threading.Lock()
        self._running = False
        self._last_error: str | None = None

    @classmethod
    def get_instance(cls) -> NSLS2BeamStatusService:
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._singleton_lock:
            if cls._instance is not None:
                cls._instance.stop()
                cls._instance.deleteLater()
            cls._instance = None

    @property
    def current_data(self) -> NSLS2BeamData | None:
        with self._data_lock:
            if self._data is None:
                return None
            return replace(self._data)

    @property
    def is_connected(self) -> bool:
        return bool(self._connected_pvs)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> None:
        """Open the caproto context and subscribe to all status PVs."""
        if self._running:
            return
        try:
            from caproto.threading.client import Context

            self._context = Context()
            self._pvs = list(
                self._context.get_pvs(
                    *ALL_PVS, connection_state_callback=self._on_connection
                )
            )
            from caproto import ChannelType

            self._subs = []
            for pv in self._pvs:
                # caproto's subscribe(data_type=...) accepts None (native), a
                # ChannelType enum, or a DBR *category* string ("native", ...).
                # Short enum/status PVs use DBR_STRING so the response decodes
                # to a Python str. The ".VAL$" message PVs are long strings:
                # DBR_STRING would truncate them at 40 chars, so request the
                # native CHAR waveform and assemble it in _decode.
                if pv.name in LONG_STRING_PVS:
                    data_type = ChannelType.CHAR
                elif pv.name in STRING_PVS:
                    data_type = ChannelType.STRING
                else:
                    data_type = None
                sub = pv.subscribe(data_type=data_type)
                sub.add_callback(self._on_monitor)
                self._subs.append(sub)
            self._running = True
        except Exception as e:  # pragma: no cover - defensive, off-network
            self._last_error = str(e)
            self._running = False

    def stop(self) -> None:
        if not self._running and self._context is None:
            return
        if self._context is not None:
            try:
                self._context.disconnect()
            except Exception:
                pass
        self._context = None
        self._pvs = []
        self._subs = []
        self._connected_pvs.clear()
        self._running = False

    # -- caproto callbacks (fire on worker threads) --------------------

    def _on_monitor(self, sub, response) -> None:
        try:
            value = self._decode(sub.pv.name, response)
        except Exception:  # pragma: no cover - defensive decode guard
            return
        self._on_value(sub.pv.name, value)

    @staticmethod
    def _decode(pv_name: str, response) -> object:
        """Decode a caproto monitor response into a Python value."""
        data = getattr(response, "data", response)
        if pv_name in LONG_STRING_PVS:
            return _decode_long_string(data)
        try:
            value = data[0]
        except (TypeError, IndexError, KeyError):
            value = data
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        return value

    def _on_value(self, pv_name: str, value: object) -> None:
        with self._data_lock:
            if self._data is None:
                self._data = NSLS2BeamData()
            apply_pv_value(self._data, pv_name, value)
            self._data.timestamp = datetime.now()
            snapshot = replace(self._data)
        self.status_changed.emit(snapshot)

    def _on_connection(self, pv, state) -> None:
        name = getattr(pv, "name", None)
        if name is None:
            return
        should_emit = False
        emit_value = False
        with self._conn_lock:
            was = bool(self._connected_pvs)
            if state == "connected":
                self._connected_pvs.add(name)
            else:
                self._connected_pvs.discard(name)
            now = bool(self._connected_pvs)
            if now != was:
                if not now:
                    self._last_error = "EPICS PVs disconnected"
                else:
                    self._last_error = None
                should_emit = True
                emit_value = now
        if should_emit:
            self.connection_changed.emit(emit_value)

    def get_introspection_data(self) -> dict[str, Any]:
        with self._data_lock:
            snap = replace(self._data) if self._data is not None else None
        result: dict[str, Any] = {
            "is_connected": self.is_connected,
            "is_running": self._running,
        }
        if snap is not None:
            result["beam_current_mA"] = snap.beam_current
            result["beam_available"] = snap.beam_available
            result["mode"] = snap.mode
            result["lifetime_hours"] = snap.lifetime
            result["topoff_state"] = snap.topoff_state
            result["next_injection"] = snap.next_injection
            result["ops_message"] = snap.ops_message
            if snap.timestamp:
                result["timestamp"] = snap.timestamp.isoformat()
        if self._last_error:
            result["last_error"] = self._last_error
        return result
