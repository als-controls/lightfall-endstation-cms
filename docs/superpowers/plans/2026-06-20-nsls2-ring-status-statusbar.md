# NSLS-II Ring Status StatusBar Plugin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Lightfall status-bar indicator showing NSLS-II storage-ring status (beam current, lifetime, operating mode, beam availability) to the CMS (11-BM) endstation package.

**Architecture:** A singleton `QObject` service subscribes to NSLS-II accelerator status PVs over EPICS Channel Access using `caproto.threading.client` monitors, decodes each value, and emits Qt signals. A `StatusBarPlugin` renders those values (icon + text + color + tooltip), toasts on beam-availability transitions, and opens the public status page on click. Both units live in `lightfall-endstation-cms`; one manifest entry registers the plugin. No changes to core `lightfall`.

**Tech Stack:** Python 3.10+, PySide6 (Qt), caproto 1.3, qtawesome, pytest + pytest-qt.

## Global Constraints

- Package root: `lightfall-endstation-cms`; source under `src/lightfall_endstation_cms/`, tests under `tests/`.
- Run tests with the venv Python only: from the `ncs` repo root, `.venv/Scripts/python -m pytest <path> -v`. Never bare `pytest` (system 3.10 can't import the package stack).
- Every test file starts with `sys.path.insert(0, str(Path(__file__).parent.parent / "src"))` before importing `lightfall_endstation_cms` (matches existing CMS tests).
- Qt-touching tests take the `qapp` fixture (pytest-qt); widget tests also take `qtbot` and call `qtbot.addWidget(widget)`.
- Source PVs (verbatim):
  - current `SR:C03-BI{DCCT:1}I:Real-I`
  - lifetime `SR:C03-BI{DCCT:1}Lifetime-I`
  - mode `SR-OPS{}Mode-Sts`
  - shutter `SR-OPS{}Shutter-Sts`
  - top-off `INJ{TOC}OpControl-Sel`
  - next injection `INJ{TOC-SM}Cnt:Next-I`
  - ops messages `OP{1}Message.VAL$`, `OP{2}Message.VAL$`
- Click target URL: `https://www.bnl.gov/nsls2/operating-status.php`
- Exact enum semantics for `SR-OPS{}Shutter-Sts` / `SR-OPS{}Mode-Sts` are confirmed against live PVs during implementation; `apply_pv_value` derives `beam_available` defensively (case-insensitive "open" token).

## File Structure

- Create `src/lightfall_endstation_cms/services/__init__.py` — new subpackage marker.
- Create `src/lightfall_endstation_cms/services/nsls2_beam_status.py` — PV constants, `NSLS2BeamData`, pure `apply_pv_value`, and `NSLS2BeamStatusService`.
- Create `src/lightfall_endstation_cms/statusbar/__init__.py` — new subpackage marker.
- Create `src/lightfall_endstation_cms/statusbar/nsls2_beam_status.py` — `NSLS2BeamStatusPlugin`.
- Modify `src/lightfall_endstation_cms/manifest.py` — add one statusbar `PluginEntry`.
- Create `tests/test_nsls2_beam_status_data.py` — pure mapping tests (Task 1).
- Create `tests/test_nsls2_beam_status_service.py` — service lifecycle tests (Task 2).
- Create `tests/test_nsls2_beam_status_plugin.py` — plugin display/behavior tests (Task 3).
- Create `tests/test_manifest_statusbar.py` — manifest registration test (Task 4).

---

### Task 1: PV constants, `NSLS2BeamData`, and pure `apply_pv_value`

**Files:**
- Create: `src/lightfall_endstation_cms/services/__init__.py`
- Create: `src/lightfall_endstation_cms/services/nsls2_beam_status.py` (data + constants + pure function only; the service class is added in Task 2)
- Test: `tests/test_nsls2_beam_status_data.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Module constants: `SR_CURRENT_PV`, `SR_LIFETIME_PV`, `SR_MODE_PV`, `SR_SHUTTER_PV`, `TOPOFF_PV`, `NEXT_INJ_PV`, `OPS_MSG1_PV`, `OPS_MSG2_PV` (all `str`); `ALL_PVS: tuple[str, ...]`; `STRING_PVS: frozenset[str]`.
  - `@dataclass NSLS2BeamData` with fields: `beam_current: float = 0.0`, `beam_available: bool = False`, `shutter_status: str = ""`, `mode: str = ""`, `lifetime: float = 0.0`, `topoff_state: str = ""`, `next_injection: str = ""`, `ops_message_1: str = ""`, `ops_message_2: str = ""`, `timestamp: datetime | None = None`; and a property `ops_message: str` joining the two messages with `"\n"`, dropping blanks.
  - `apply_pv_value(data: NSLS2BeamData, pv_name: str, value: object) -> None` — mutates `data` in place; sets `beam_available` when applying the shutter PV.
  - `shutter_means_available(value: object) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_nsls2_beam_status_data.py`:

```python
"""Tests for NSLS-II beam status PV mapping (pure, no Qt / no IOC)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.services import nsls2_beam_status as svc
from lightfall_endstation_cms.services.nsls2_beam_status import (
    NSLS2BeamData,
    apply_pv_value,
    shutter_means_available,
)


def test_all_pvs_tuple_has_eight_unique_entries():
    assert len(svc.ALL_PVS) == 8
    assert len(set(svc.ALL_PVS)) == 8


def test_string_pvs_are_the_enum_and_message_pvs():
    assert svc.STRING_PVS == frozenset(
        {svc.SR_MODE_PV, svc.SR_SHUTTER_PV, svc.TOPOFF_PV, svc.OPS_MSG1_PV, svc.OPS_MSG2_PV}
    )


def test_apply_numeric_pvs():
    data = NSLS2BeamData()
    apply_pv_value(data, svc.SR_CURRENT_PV, 401.27)
    apply_pv_value(data, svc.SR_LIFETIME_PV, 12.5)
    assert data.beam_current == 401.27
    assert data.lifetime == 12.5


def test_apply_string_pvs():
    data = NSLS2BeamData()
    apply_pv_value(data, svc.SR_MODE_PV, "Operations")
    apply_pv_value(data, svc.TOPOFF_PV, "On")
    apply_pv_value(data, svc.NEXT_INJ_PV, "120")
    apply_pv_value(data, svc.OPS_MSG1_PV, "Beam delivered to all beamlines")
    apply_pv_value(data, svc.OPS_MSG2_PV, "Next fill 14:00")
    assert data.mode == "Operations"
    assert data.topoff_state == "On"
    assert data.next_injection == "120"
    assert data.ops_message == "Beam delivered to all beamlines\nNext fill 14:00"


def test_shutter_pv_sets_availability():
    data = NSLS2BeamData()
    apply_pv_value(data, svc.SR_SHUTTER_PV, "Open")
    assert data.shutter_status == "Open"
    assert data.beam_available is True

    apply_pv_value(data, svc.SR_SHUTTER_PV, "Closed")
    assert data.shutter_status == "Closed"
    assert data.beam_available is False


def test_shutter_means_available_is_case_insensitive():
    assert shutter_means_available("OPEN") is True
    assert shutter_means_available("open") is True
    assert shutter_means_available("Closed") is False
    assert shutter_means_available("") is False
    assert shutter_means_available(0) is False


def test_ops_message_drops_blank_lines():
    data = NSLS2BeamData(ops_message_1="hello", ops_message_2="")
    assert data.ops_message == "hello"


def test_unknown_pv_is_ignored():
    data = NSLS2BeamData()
    apply_pv_value(data, "Some:Other-PV", 5.0)
    assert data == NSLS2BeamData()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest lightfall-endstation-cms/tests/test_nsls2_beam_status_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lightfall_endstation_cms.services'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/lightfall_endstation_cms/services/__init__.py`:

```python
"""Background services for the CMS endstation package."""
```

Create `src/lightfall_endstation_cms/services/nsls2_beam_status.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest lightfall-endstation-cms/tests/test_nsls2_beam_status_data.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
cd lightfall-endstation-cms
git add src/lightfall_endstation_cms/services/__init__.py \
        src/lightfall_endstation_cms/services/nsls2_beam_status.py \
        tests/test_nsls2_beam_status_data.py
git commit -m "feat(cms): NSLS-II beam status PV mapping and data model"
```

---

### Task 2: `NSLS2BeamStatusService` (caproto lifecycle + signals)

**Files:**
- Modify: `src/lightfall_endstation_cms/services/nsls2_beam_status.py` (append the service class)
- Test: `tests/test_nsls2_beam_status_service.py`

**Interfaces:**
- Consumes: `NSLS2BeamData`, `apply_pv_value`, `ALL_PVS`, `STRING_PVS`, the PV-name constants (Task 1); `caproto.threading.client.Context` (imported lazily inside `start()` so tests can monkeypatch it at `caproto.threading.client.Context`).
- Produces: `NSLS2BeamStatusService(QObject)` with:
  - Signals: `status_changed = Signal(object)` (carries `NSLS2BeamData`), `connection_changed = Signal(bool)`.
  - Classmethods: `get_instance() -> NSLS2BeamStatusService`, `reset() -> None`.
  - Properties: `current_data -> NSLS2BeamData | None`, `is_connected -> bool`, `is_running -> bool`, `last_error -> str | None`.
  - Methods: `start() -> None`, `stop() -> None`, `get_introspection_data() -> dict[str, Any]`.
  - Internal (tested directly): `_on_value(pv_name: str, value: object) -> None`, `_on_connection(pv, state) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_nsls2_beam_status_service.py`:

```python
"""Lifecycle tests for NSLS2BeamStatusService (caproto Context monkeypatched)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.services import nsls2_beam_status as svc
from lightfall_endstation_cms.services.nsls2_beam_status import NSLS2BeamStatusService


class _FakeSub:
    def __init__(self, pv):
        self.pv = pv
        self.callbacks = []

    def add_callback(self, cb):
        self.callbacks.append(cb)


class _FakePV:
    def __init__(self, name):
        self.name = name

    def subscribe(self, data_type=None):
        self.data_type = data_type
        return _FakeSub(self)


class _FakeContext:
    instances = []

    def __init__(self, *a, **k):
        self.connection_state_callback = None
        self.disconnected = False
        _FakeContext.instances.append(self)

    def get_pvs(self, *names, connection_state_callback=None):
        self.connection_state_callback = connection_state_callback
        return [_FakePV(n) for n in names]

    def disconnect(self):
        self.disconnected = True


@pytest.fixture(autouse=True)
def _patch_context(monkeypatch):
    _FakeContext.instances = []
    monkeypatch.setattr("caproto.threading.client.Context", _FakeContext)
    yield
    NSLS2BeamStatusService.reset()


def test_singleton_identity_and_reset():
    a = NSLS2BeamStatusService.get_instance()
    b = NSLS2BeamStatusService.get_instance()
    assert a is b
    NSLS2BeamStatusService.reset()
    c = NSLS2BeamStatusService.get_instance()
    assert c is not a


def test_start_is_idempotent_and_subscribes_all_pvs():
    s = NSLS2BeamStatusService.get_instance()
    s.start()
    s.start()  # second call must be a no-op
    assert s.is_running is True
    assert len(_FakeContext.instances) == 1
    ctx = _FakeContext.instances[0]
    # one PV per ALL_PVS, each with a monitor callback registered
    assert len(s._subs) == len(svc.ALL_PVS)


def test_string_pvs_subscribe_as_string():
    s = NSLS2BeamStatusService.get_instance()
    s.start()
    by_name = {sub.pv.name: sub.pv for sub in s._subs}
    assert by_name[svc.SR_MODE_PV].data_type == "string"
    assert by_name[svc.SR_CURRENT_PV].data_type is None


def test_on_value_updates_data_and_emits_status():
    s = NSLS2BeamStatusService.get_instance()
    received = []
    s.status_changed.connect(received.append)
    s._on_value(svc.SR_CURRENT_PV, 401.0)
    assert s.current_data is not None
    assert s.current_data.beam_current == 401.0
    assert received and received[-1].beam_current == 401.0


def test_connection_transitions_emit_once():
    s = NSLS2BeamStatusService.get_instance()
    seen = []
    s.connection_changed.connect(seen.append)
    pv = _FakePV(svc.SR_CURRENT_PV)
    s._on_connection(pv, "connected")
    s._on_connection(_FakePV(svc.SR_LIFETIME_PV), "connected")  # still connected, no new emit
    assert s.is_connected is True
    assert seen == [True]
    s._on_connection(pv, "disconnected")
    s._on_connection(_FakePV(svc.SR_LIFETIME_PV), "disconnected")
    assert s.is_connected is False
    assert seen == [True, False]


def test_stop_disconnects_context():
    s = NSLS2BeamStatusService.get_instance()
    s.start()
    ctx = _FakeContext.instances[0]
    s.stop()
    assert s.is_running is False
    assert ctx.disconnected is True


def test_introspection_reports_values():
    s = NSLS2BeamStatusService.get_instance()
    s._on_value(svc.SR_CURRENT_PV, 401.0)
    s._on_value(svc.SR_SHUTTER_PV, "Open")
    data = s.get_introspection_data()
    assert data["beam_current_mA"] == 401.0
    assert data["beam_available"] is True
    assert data["is_running"] == s.is_running
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest lightfall-endstation-cms/tests/test_nsls2_beam_status_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'NSLS2BeamStatusService'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/lightfall_endstation_cms/services/nsls2_beam_status.py`. First extend the import block at the top of the file:

```python
import threading
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QObject, Signal
```

Then append the service class at the end of the file:

```python
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
        return self._data

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
            self._subs = []
            for pv in self._pvs:
                data_type = "string" if pv.name in STRING_PVS else None
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
        """Decode a caproto monitor response into a Python scalar."""
        data = getattr(response, "data", response)
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
            self.connection_changed.emit(now)

    def get_introspection_data(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "is_connected": self.is_connected,
            "is_running": self._running,
        }
        if self._data is not None:
            result["beam_current_mA"] = self._data.beam_current
            result["beam_available"] = self._data.beam_available
            result["mode"] = self._data.mode
            result["lifetime_hours"] = self._data.lifetime
            result["topoff_state"] = self._data.topoff_state
            result["next_injection"] = self._data.next_injection
            result["ops_message"] = self._data.ops_message
            if self._data.timestamp:
                result["timestamp"] = self._data.timestamp.isoformat()
        if self._last_error:
            result["last_error"] = self._last_error
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest lightfall-endstation-cms/tests/test_nsls2_beam_status_service.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
cd lightfall-endstation-cms
git add src/lightfall_endstation_cms/services/nsls2_beam_status.py \
        tests/test_nsls2_beam_status_service.py
git commit -m "feat(cms): NSLS2BeamStatusService caproto lifecycle and signals"
```

---

### Task 3: `NSLS2BeamStatusPlugin` (status-bar widget)

**Files:**
- Create: `src/lightfall_endstation_cms/statusbar/__init__.py`
- Create: `src/lightfall_endstation_cms/statusbar/nsls2_beam_status.py`
- Test: `tests/test_nsls2_beam_status_plugin.py`

**Interfaces:**
- Consumes: `NSLS2BeamStatusService` and `NSLS2BeamData` (Task 1/2); `lightfall.plugins.statusbar_plugin.StatusBarPlugin` / `StatusBarPluginMetadata`; `lightfall.ui.theme.ThemeManager`; `lightfall.ui.toast.ToastManager`. The service is fetched via `NSLS2BeamStatusService.get_instance()` inside methods (lazy) so tests can monkeypatch it.
- Produces: `NSLS2BeamStatusPlugin(StatusBarPlugin)` with `metadata.id == "lightfall.statusbar.nsls2_beam"`, `name == "nsls2_beam_status"`, `BEAM_STATUS_URL == "https://www.bnl.gov/nsls2/operating-status.php"`, and the standard `update` / `connect_signals` / `disconnect_signals` / `on_clicked` / `get_introspection_data` overrides.

- [ ] **Step 1: Write the failing test**

Create `tests/test_nsls2_beam_status_plugin.py`:

```python
"""Display/behavior tests for NSLS2BeamStatusPlugin (fake service)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.services.nsls2_beam_status import NSLS2BeamData
from lightfall_endstation_cms.statusbar import nsls2_beam_status as mod
from lightfall_endstation_cms.statusbar.nsls2_beam_status import NSLS2BeamStatusPlugin


class _FakeService(QObject):
    status_changed = Signal(object)
    connection_changed = Signal(bool)

    def __init__(self, *, connected=True, data=None):
        super().__init__()
        self._connected = connected
        self._data = data
        self.started = False
        self.last_error = None

    @property
    def is_running(self):
        return self.started

    def start(self):
        self.started = True

    @property
    def is_connected(self):
        return self._connected

    @property
    def current_data(self):
        return self._data

    def get_introspection_data(self):
        return {"is_connected": self._connected}


def _install(monkeypatch, fake):
    monkeypatch.setattr(
        mod.NSLS2BeamStatusService, "get_instance", classmethod(lambda cls: fake)
    )


def _make(qtbot, fake):
    plugin = NSLS2BeamStatusPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    return plugin


def test_metadata_and_name():
    assert NSLS2BeamStatusPlugin.metadata.id == "lightfall.statusbar.nsls2_beam"
    assert NSLS2BeamStatusPlugin().name == "nsls2_beam_status"


def test_shows_current_and_lifetime_when_available(qapp, qtbot, monkeypatch):
    data = NSLS2BeamData(beam_current=401.0, lifetime=12.5, beam_available=True)
    fake = _FakeService(connected=True, data=data)
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)
    plugin.update()
    assert "401 mA" in plugin._button.text()
    assert "12.5h" in plugin._button.text()
    assert fake.started is True  # lazy-started


def test_offline_when_disconnected(qapp, qtbot, monkeypatch):
    fake = _FakeService(connected=False, data=None)
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)
    plugin.update()
    assert plugin._button.text() == "Offline"


def test_toast_on_availability_change(qapp, qtbot, monkeypatch):
    class _Toast:
        def __init__(self):
            self.calls = []

        def success(self, *a, **k):
            self.calls.append(("success", a))

        def warning(self, *a, **k):
            self.calls.append(("warning", a))

    toast = _Toast()
    monkeypatch.setattr(
        "lightfall.ui.toast.ToastManager.get_instance", classmethod(lambda cls: toast)
    )
    data_open = NSLS2BeamData(beam_current=401.0, lifetime=12.5, beam_available=True)
    fake = _FakeService(connected=True, data=data_open)
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)
    plugin.update()  # first paint: establishes baseline, no toast
    assert toast.calls == []
    fake._data = NSLS2BeamData(beam_current=0.0, lifetime=0.0, beam_available=False)
    plugin.update()  # transition available -> unavailable
    assert toast.calls and toast.calls[-1][0] == "warning"


def test_click_opens_status_page(qapp, qtbot, monkeypatch):
    opened = {}
    monkeypatch.setattr(
        "PySide6.QtGui.QDesktopServices.openUrl",
        lambda url: opened.setdefault("url", url.toString()) or True,
    )
    fake = _FakeService()
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)
    plugin.on_clicked()
    assert opened["url"] == "https://www.bnl.gov/nsls2/operating-status.php"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest lightfall-endstation-cms/tests/test_nsls2_beam_status_plugin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lightfall_endstation_cms.statusbar'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/lightfall_endstation_cms/statusbar/__init__.py`:

```python
"""Status-bar plugins for the CMS endstation package."""
```

Create `src/lightfall_endstation_cms/statusbar/nsls2_beam_status.py`:

```python
"""NSLS-II ring status indicator for the Lightfall status bar (CMS 11-BM).

Displays storage-ring current, lifetime, operating mode, and beam
availability, sourced from EPICS PVs via NSLS2BeamStatusService.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import qtawesome as qta
from PySide6.QtCore import QUrl, Slot
from PySide6.QtGui import QDesktopServices

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.ui.theme import ThemeManager
from lightfall.ui.toast import ToastManager
from lightfall.utils.logging import logger

from lightfall_endstation_cms.services.nsls2_beam_status import NSLS2BeamStatusService

if TYPE_CHECKING:
    from lightfall_endstation_cms.services.nsls2_beam_status import NSLS2BeamData


class NSLS2BeamStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing NSLS-II storage-ring status.

    Color coding: success when beam is available, error when not, and
    text_secondary when offline / disconnected. Clicking opens the NSLS-II
    operating-status page.
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.nsls2_beam",
        name="NSLS-II Beam Status",
        description="Shows NSLS-II storage-ring current and status",
        priority=45,
        position="permanent",
        tooltip="NSLS-II ring status - click for details",
    )

    BEAM_STATUS_URL = "https://www.bnl.gov/nsls2/operating-status.php"

    def __init__(self) -> None:
        super().__init__()
        self._service: NSLS2BeamStatusService | None = None
        self._last_beam_available: bool | None = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        return "nsls2_beam_status"

    def on_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl(self.BEAM_STATUS_URL))

    def update(self) -> None:
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        try:
            service = NSLS2BeamStatusService.get_instance()
            self._service = service
            if not service.is_running:
                service.start()
            if service.is_connected and service.current_data is not None:
                self._update_display_data(service.current_data)
            else:
                self._update_display_offline()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Could not get NSLS-II beam status: {}", e)
            self._update_display_offline()

    def connect_signals(self) -> None:
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        self._theme_manager.colors_changed.connect(self.update)
        try:
            service = NSLS2BeamStatusService.get_instance()
            self._service = service
            service.status_changed.connect(self._on_status_changed)
            service.connection_changed.connect(self._on_connection_changed)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Could not connect to NSLS2BeamStatusService: {}", e)

    def disconnect_signals(self) -> None:
        if self._service is not None:
            try:
                self._service.status_changed.disconnect(self._on_status_changed)
                self._service.connection_changed.disconnect(self._on_connection_changed)
            except RuntimeError:
                pass
        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    @Slot(object)
    def _on_status_changed(self, data: NSLS2BeamData) -> None:
        if self._service is not None and not self._service.is_connected:
            self._update_display_offline()
        else:
            self._update_display_data(data)

    @Slot(bool)
    def _on_connection_changed(self, connected: bool) -> None:
        if not connected:
            self._update_display_offline()
        else:
            self.update()

    def _update_display_data(self, data: NSLS2BeamData) -> None:
        if (
            self._last_beam_available is not None
            and data.beam_available != self._last_beam_available
        ):
            self._notify_status_change(data.beam_available)
        self._last_beam_available = data.beam_available

        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        colors = self._theme_manager.colors
        color = colors.success if data.beam_available else colors.error

        self.set_icon(qta.icon("ri.sun-line", color=color))
        self.set_text(f"{data.beam_current:.0f} mA | {data.lifetime:.1f}h")
        self.set_color(color)
        self.set_tooltip(self._build_tooltip(data))

    def _notify_status_change(self, beam_available: bool) -> None:
        toast = ToastManager.get_instance()
        link = f'<a href="{self.BEAM_STATUS_URL}">Operating Status</a>'
        if beam_available:
            toast.success(
                "NSLS-II Beam Available",
                f"Storage-ring beam is now available · {link}",
                duration=10000,
            )
        else:
            toast.warning(
                "NSLS-II Beam Unavailable",
                f"Storage-ring beam is no longer available · {link}",
                duration=10000,
            )

    def _update_display_offline(self) -> None:
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        secondary = self._theme_manager.colors.text_secondary
        self.set_icon(qta.icon("ri.sun-line", color=secondary))
        self.set_text("Offline")
        self.set_color(secondary)
        error_msg = ""
        if self._service and self._service.last_error:
            error_msg = f"\nError: {self._service.last_error}"
        self.set_tooltip(f"NSLS-II ring status unavailable{error_msg}")

    def _build_tooltip(self, data: NSLS2BeamData) -> str:
        lines = [
            "NSLS-II Storage Ring",
            "-" * 25,
            f"Current: {data.beam_current:.1f} mA",
            f"Lifetime: {data.lifetime:.1f} hours",
            f"Mode: {data.mode or 'unknown'}",
            f"Beam: {'Available' if data.beam_available else 'Unavailable'}"
            f" ({data.shutter_status or '?'})",
            f"Top-off: {data.topoff_state or 'unknown'}",
            f"Next injection: {data.next_injection or 'unknown'}",
        ]
        if data.ops_message:
            lines.extend(["", "Operations:", data.ops_message])
        if data.timestamp:
            lines.extend(["", f"Updated: {data.timestamp.strftime('%H:%M:%S')}"])
        return "\n".join(lines)

    def get_introspection_data(self) -> dict[str, Any]:
        data = super().get_introspection_data()
        try:
            service = NSLS2BeamStatusService.get_instance()
            data.update(service.get_introspection_data())
        except Exception:
            data["nsls2_beam_connected"] = False
        return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest lightfall-endstation-cms/tests/test_nsls2_beam_status_plugin.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd lightfall-endstation-cms
git add src/lightfall_endstation_cms/statusbar/__init__.py \
        src/lightfall_endstation_cms/statusbar/nsls2_beam_status.py \
        tests/test_nsls2_beam_status_plugin.py
git commit -m "feat(cms): NSLS-II ring status status-bar plugin"
```

---

### Task 4: Register the plugin in the manifest

**Files:**
- Modify: `src/lightfall_endstation_cms/manifest.py`
- Test: `tests/test_manifest_statusbar.py`

**Interfaces:**
- Consumes: `NSLS2BeamStatusPlugin` (Task 3); `lightfall.plugins.manifest.PluginEntry` / `PluginManifest`.
- Produces: a `manifest.plugins` entry with `type_name == "statusbar"`, `name == "nsls2_beam_status"`, `import_path == "lightfall_endstation_cms.statusbar.nsls2_beam_status:NSLS2BeamStatusPlugin"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_manifest_statusbar.py`:

```python
"""Verify the NSLS-II ring status plugin is registered and importable."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.manifest import manifest


def _find(name):
    return next((e for e in manifest.plugins if e.name == name), None)


def test_statusbar_entry_registered():
    entry = _find("nsls2_beam_status")
    assert entry is not None
    assert entry.type_name == "statusbar"
    assert entry.import_path == (
        "lightfall_endstation_cms.statusbar.nsls2_beam_status:NSLS2BeamStatusPlugin"
    )


def test_statusbar_entry_import_path_resolves():
    entry = _find("nsls2_beam_status")
    module_path, _, attr = entry.import_path.partition(":")
    module = importlib.import_module(module_path)
    assert hasattr(module, attr)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest lightfall-endstation-cms/tests/test_manifest_statusbar.py -v`
Expected: FAIL — `test_statusbar_entry_registered` asserts `entry is not None` and fails (no such entry yet).

- [ ] **Step 3: Write minimal implementation**

In `src/lightfall_endstation_cms/manifest.py`, add a new `PluginEntry` to the `plugins=[...]` list (after the existing panel entries, before the closing `]`):

```python
        PluginEntry(
            type_name="statusbar",
            name="nsls2_beam_status",
            import_path="lightfall_endstation_cms.statusbar.nsls2_beam_status:NSLS2BeamStatusPlugin",
            metadata={"beamline": "11-BM CMS"},
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest lightfall-endstation-cms/tests/test_manifest_statusbar.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full new-feature test set and commit**

Run: `.venv/Scripts/python -m pytest lightfall-endstation-cms/tests/test_nsls2_beam_status_data.py lightfall-endstation-cms/tests/test_nsls2_beam_status_service.py lightfall-endstation-cms/tests/test_nsls2_beam_status_plugin.py lightfall-endstation-cms/tests/test_manifest_statusbar.py -v`
Expected: PASS (all).

```bash
cd lightfall-endstation-cms
git add src/lightfall_endstation_cms/manifest.py tests/test_manifest_statusbar.py
git commit -m "feat(cms): register NSLS-II ring status status-bar plugin"
```

---

## Post-implementation (manual, on the NSLS-II network)

These are not automated steps; record results when next on-site at CMS:

- Confirm `SR-OPS{}Shutter-Sts` enum strings. If "beam available" is not signalled by an "open"-containing string, adjust `_SHUTTER_OPEN_TOKEN` / `shutter_means_available` and its unit test.
- Confirm `SR-OPS{}Mode-Sts` and `INJ{TOC}OpControl-Sel` render as readable strings (string subscribe). If they come back as enum indices, capture `enum_strings` from a control read in `_decode`.
- Verify the indicator appears, colors track real shutter state, and the click opens the status page.

## Self-Review

- **Spec coverage:** caproto transport (Task 2 `start`); all 8 PVs + mapping (Task 1, `ALL_PVS`/`apply_pv_value`); `NSLS2BeamData` fields (Task 1); service public surface incl. signals/singleton/introspection (Task 2); plugin display/toast/tooltip/click/introspection (Task 3); manifest entry (Task 4); defensive enum parsing + offline behavior (Task 1 `shutter_means_available`, Task 3 offline path); tests with no live IOC (all tasks). `force_refresh` from the spec's service surface is intentionally dropped — YAGNI, no consumer in the CMS package (noted here as a deliberate deviation). The spec's "hide text when nominal" idea is replaced with always-show current|lifetime (no facility-specific threshold) — deliberate simplification.
- **Placeholder scan:** none — every code/test step is complete.
- **Type consistency:** `apply_pv_value(data, pv_name, value)`, `NSLS2BeamData` fields, `NSLS2BeamStatusService` method/property/signal names, and `import_path` string are identical across Tasks 1–4.
