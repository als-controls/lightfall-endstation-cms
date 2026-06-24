"""Tests for the devices-loaded gate in CMSSessionTrigger.

The gate fires the profile bootstrap when the DeviceConnectionManager emits
``all_connections_complete`` (the "devices are loaded" event), or when a
degraded-mode deadline elapses first.  These drive a QApplication directly
(rather than pytest-qt's qtbot) so they run in a runtime venv without the test
extras; QApplication.instance() reuses pytest-qt's qapp under the full CI suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import lightfall_endstation_cms.session_trigger as st
from lightfall_endstation_cms.session_trigger import CMSSessionTrigger

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _qapp():
    """A QApplication so arm()'s deadline QTimer can be constructed/started."""
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _reset_manager():
    """Fresh DeviceConnectionManager singleton per test so subscriptions and
    emitted signals don't bleed across tests."""
    from lightfall.devices.connection_manager import DeviceConnectionManager

    DeviceConnectionManager.reset_instance()
    yield
    DeviceConnectionManager.reset_instance()


def _run_inline(monkeypatch):
    """Make invoke_in_main_thread run its callable synchronously in the test."""
    monkeypatch.setattr(st, "invoke_in_main_thread", lambda fn, *a, **k: fn(*a, **k))


def _fake_bootstrap(monkeypatch):
    """Stub _run_bootstrap so it records calls without touching the kernel.

    Sets _done = True to mimic a successful bootstrap, so tests can assert on
    the once-only guard without a real IPython kernel.
    """
    calls: list = []

    def _stub(self):
        calls.append(1)
        self._done = True

    monkeypatch.setattr(CMSSessionTrigger, "_run_bootstrap", _stub)
    return calls


def _emit_complete():
    from lightfall.devices.connection_manager import DeviceConnectionManager

    DeviceConnectionManager.get_instance().all_connections_complete.emit()


# ---------------------------------------------------------------------------
# (a) arm() wires the gate to the connection manager's completion signal
# ---------------------------------------------------------------------------

def test_arm_subscribes_to_devices_loaded_signal(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    trigger = CMSSessionTrigger()
    trigger.arm(timeout_s=60.0)

    _emit_complete()  # the real "devices are loaded" event

    assert calls == [1], "bootstrap should fire when all connections complete"
    assert trigger._done is True


# ---------------------------------------------------------------------------
# (b) does not fire before the completion signal arrives
# ---------------------------------------------------------------------------

def test_does_not_fire_before_devices_loaded(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    trigger = CMSSessionTrigger()
    trigger.arm(timeout_s=60.0)

    assert calls == [], "must not fire until devices are loaded"
    assert trigger._done is False


# ---------------------------------------------------------------------------
# (c) repeated completion signals fire the bootstrap only once
# ---------------------------------------------------------------------------

def test_fires_only_once_on_repeated_signal(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    trigger = CMSSessionTrigger()
    trigger.arm(timeout_s=60.0)

    _emit_complete()
    _emit_complete()
    _emit_complete()

    assert calls == [1], "_done guard must prevent re-firing"


# ---------------------------------------------------------------------------
# (d) the deadline fires the bootstrap in degraded mode
# ---------------------------------------------------------------------------

def test_fires_degraded_on_deadline(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    trigger = CMSSessionTrigger()
    trigger.arm(timeout_s=60.0)

    trigger._on_deadline()  # simulate the deadline timer firing

    assert calls == [1], "degraded bootstrap should fire on the deadline"
    assert trigger._done is True


def test_deadline_is_noop_after_devices_loaded(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    trigger = CMSSessionTrigger()
    trigger.arm(timeout_s=60.0)

    _emit_complete()        # fires (nominal)
    trigger._on_deadline()  # must be a no-op now

    assert calls == [1]


# ---------------------------------------------------------------------------
# (e) re-arm (the retry path) still fires exactly once (no duplicate subscribe)
# ---------------------------------------------------------------------------

def test_rearm_still_fires_once(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    trigger = CMSSessionTrigger()
    trigger.arm(timeout_s=60.0)
    trigger.arm(timeout_s=60.0)  # re-arm must not leave a duplicate subscription

    _emit_complete()

    assert calls == [1], "re-arm must not cause a double fire"
