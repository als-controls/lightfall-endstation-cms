"""Tests for CMSSessionTrigger bootstrap once-only and thread-marshaling behaviour.

These tests verify properties of _run_bootstrap and invoke_in_main_thread that
are independent of the devices-live gating mechanism.  The gating behaviour
itself is covered by test_session_trigger_gating.py.

The AUTHENTICATED-based _on_state_changed gate has been replaced by the
devices-live poll gate (arm() + _poll()).  Tests that were written against
_on_state_changed have been updated to drive _fire()/_run_bootstrap() directly,
since those properties (once-only, retry-on-failure, GUI-thread marshaling)
belong to _fire/_run_bootstrap, not to the gate that calls them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.session_trigger import CMSSessionTrigger


def _run_inline(monkeypatch):
    """Make invoke_in_main_thread run its callable synchronously in the test."""
    import lightfall_endstation_cms.session_trigger as st

    monkeypatch.setattr(st, "invoke_in_main_thread", lambda fn, *a, **k: fn(*a, **k))


def test_trigger_runs_bootstrap_once_on_success(monkeypatch):
    """A successful bootstrap must fire at most once even if _fire() is called
    multiple times (e.g., multiple poll ticks, or arm() called again)."""
    fake_shell = MagicMock(name="shell")
    fake_bootstrapper = MagicMock(name="bootstrapper")
    fake_bootstrapper.bootstrap.return_value = True  # success

    import lightfall_endstation_cms.session_trigger as st
    _run_inline(monkeypatch)
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: fake_shell)
    monkeypatch.setattr(st, "ProfileSessionBootstrapper", lambda backend=None: fake_bootstrapper)

    trigger = CMSSessionTrigger()
    # Two _fire() calls (simulate two poll ticks that both see devices live);
    # a successful bootstrap must run only once.
    trigger._fire()
    trigger._fire()

    fake_bootstrapper.bootstrap.assert_called_once_with(fake_shell)


def test_trigger_retries_when_bootstrap_fails(monkeypatch):
    """A failed bootstrap (e.g. Redis down -> no RE) must NOT consume the
    one-shot: a later _fire() retries."""
    fake_shell = MagicMock(name="shell")
    fake_bootstrapper = MagicMock(name="bootstrapper")
    fake_bootstrapper.bootstrap.return_value = False  # failed load

    import lightfall_endstation_cms.session_trigger as st
    _run_inline(monkeypatch)
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: fake_shell)
    monkeypatch.setattr(st, "ProfileSessionBootstrapper", lambda backend=None: fake_bootstrapper)
    monkeypatch.setattr(CMSSessionTrigger, "_notify_failure", staticmethod(lambda: None))

    trigger = CMSSessionTrigger()
    trigger._fire()   # fails
    trigger._fire()   # retries

    assert fake_bootstrapper.bootstrap.call_count == 2
    assert trigger._done is False


def test_bootstrap_is_marshaled_to_main_thread(monkeypatch):
    """Regression: the bootstrap must be dispatched via invoke_in_main_thread,
    NOT run inline on whatever thread calls _fire().

    _poll() / _fire() may be called from a QTimer on the GUI thread, but the
    original constraint (no cross-thread QWidget creation, no import-lock
    deadlock) is preserved by keeping the invoke_in_main_thread wrapper.
    """
    import lightfall_endstation_cms.session_trigger as st

    dispatched: list = []
    monkeypatch.setattr(st, "invoke_in_main_thread", lambda fn, *a, **k: dispatched.append(fn))
    # If the trigger (wrongly) ran inline, this would execute:
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: (_ for _ in ()).throw(AssertionError("ran inline")))

    trigger = CMSSessionTrigger()
    trigger._fire()

    # The work was handed to the main-thread invoker, not executed inline.
    assert dispatched == [trigger._run_bootstrap]
