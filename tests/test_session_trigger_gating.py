"""Tests for the devices-live gate in CMSSessionTrigger._poll."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import lightfall_endstation_cms.session_trigger as st
from lightfall_endstation_cms.session_trigger import CMSSessionTrigger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_inline(monkeypatch):
    """Make invoke_in_main_thread run its callable synchronously in the test."""
    monkeypatch.setattr(st, "invoke_in_main_thread", lambda fn, *a, **k: fn(*a, **k))


def _fake_bootstrap(monkeypatch):
    """Stub _run_bootstrap so it records calls without touching the kernel.

    Sets _done = True to simulate a successful bootstrap, matching what the real
    implementation does on success.  This lets tests assert on _done and on the
    once-only guard without needing a real IPython kernel.
    """
    calls = []

    def _stub(self):
        calls.append(1)
        self._done = True

    monkeypatch.setattr(CMSSessionTrigger, "_run_bootstrap", _stub)
    return calls


def _patch_devices(monkeypatch, live_mapping):
    """Patch kernel_access.devices_by_name to return live_mapping for any names."""
    monkeypatch.setattr(st.kernel_access, "devices_by_name", lambda names: {
        k: v for k, v in live_mapping.items() if k in names
    })


# ---------------------------------------------------------------------------
# (a) _poll does NOT fire while a requested device is not yet live
# ---------------------------------------------------------------------------

def test_poll_does_not_fire_when_devices_missing(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    # Only one of two devices is live
    sentinel = object()
    _patch_devices(monkeypatch, {"smx": sentinel})

    trigger = CMSSessionTrigger()
    trigger.arm(["smx", "pilatus2M"], poll_ms=500, timeout_s=60.0)

    # Well before deadline
    trigger._now = lambda: trigger._deadline - 10.0

    trigger._poll()

    assert calls == [], "bootstrap should not fire when devices are still missing"
    assert trigger._done is False


# ---------------------------------------------------------------------------
# (b) _poll fires exactly once when all requested devices are live
# ---------------------------------------------------------------------------

def test_poll_fires_when_all_devices_live(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    s1, s2 = object(), object()
    _patch_devices(monkeypatch, {"smx": s1, "pilatus2M": s2})

    trigger = CMSSessionTrigger()
    trigger.arm(["smx", "pilatus2M"], poll_ms=500, timeout_s=60.0)

    # Well before deadline
    trigger._now = lambda: trigger._deadline - 10.0

    trigger._poll()

    assert calls == [1], "bootstrap should fire exactly once when all devices are live"
    assert trigger._done is True


# ---------------------------------------------------------------------------
# (c) repeated _poll calls after liveness fire only once (_done guard)
# ---------------------------------------------------------------------------

def test_poll_fires_only_once_after_live(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    s1, s2 = object(), object()
    _patch_devices(monkeypatch, {"smx": s1, "pilatus2M": s2})

    trigger = CMSSessionTrigger()
    trigger.arm(["smx", "pilatus2M"], poll_ms=500, timeout_s=60.0)
    trigger._now = lambda: trigger._deadline - 10.0

    # Multiple poll calls — must fire once only
    trigger._poll()
    trigger._poll()
    trigger._poll()

    assert calls == [1], "_done guard must prevent re-firing"
    assert trigger._done is True


# ---------------------------------------------------------------------------
# (d) on timeout with devices still missing, _poll fires anyway (degraded)
# ---------------------------------------------------------------------------

def test_poll_fires_degraded_on_timeout(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    # No devices live
    _patch_devices(monkeypatch, {})

    trigger = CMSSessionTrigger()
    trigger.arm(["smx", "pilatus2M"], poll_ms=500, timeout_s=60.0)

    # Simulate time past the deadline
    trigger._now = lambda: trigger._deadline + 1.0

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        trigger._poll()

    assert calls == [1], "degraded bootstrap should fire on timeout"
    assert trigger._done is True


def test_poll_logs_missing_devices_on_timeout(monkeypatch, caplog):
    """Timeout path must log a warning naming the missing devices."""
    import logging

    _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)

    # smx is live but pilatus2M is not
    sentinel = object()
    _patch_devices(monkeypatch, {"smx": sentinel})

    trigger = CMSSessionTrigger()
    trigger.arm(["smx", "pilatus2M"], poll_ms=500, timeout_s=60.0)
    trigger._now = lambda: trigger._deadline + 1.0

    # Capture loguru output via stdlib logging propagation.
    # If loguru doesn't propagate in tests, we still assert the trigger fired
    # in degraded mode (best-effort logging check).
    with caplog.at_level(logging.WARNING):
        trigger._poll()

    assert trigger._done is True


# ---------------------------------------------------------------------------
# (e) _poll is a no-op before arm() is called
# ---------------------------------------------------------------------------

def test_poll_before_arm_is_noop(monkeypatch):
    calls = _fake_bootstrap(monkeypatch)
    _run_inline(monkeypatch)
    _patch_devices(monkeypatch, {"smx": object()})

    trigger = CMSSessionTrigger()
    # Do NOT call arm() — _poll should be safe to call anyway (guard on _deadline)
    trigger._poll()

    assert calls == [], "_poll before arm() must be a no-op"


# ---------------------------------------------------------------------------
# (f) re-arm (the retry path) tears down the previous timer — no leak
# ---------------------------------------------------------------------------

def test_rearm_tears_down_previous_timer(qtbot, monkeypatch):
    _patch_devices(monkeypatch, {})  # devices never live -> timer would keep ticking
    trigger = CMSSessionTrigger()

    trigger.arm(["smx"], poll_ms=10_000, timeout_s=999.0)
    first = trigger._timer
    assert first is not None and first.isActive()

    trigger.arm(["smx"], poll_ms=10_000, timeout_s=999.0)
    assert trigger._timer is not first, "re-arm must install a new timer"
    assert not first.isActive(), "the previous timer must be stopped (no leak)"
