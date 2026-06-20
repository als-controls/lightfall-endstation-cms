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
    fake_shell = MagicMock(name="shell")
    fake_bootstrapper = MagicMock(name="bootstrapper")
    fake_bootstrapper.bootstrap.return_value = True  # success

    import lightfall_endstation_cms.session_trigger as st
    _run_inline(monkeypatch)
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: fake_shell)
    monkeypatch.setattr(st, "ProfileSessionBootstrapper", lambda backend=None: fake_bootstrapper)

    trigger = CMSSessionTrigger()
    # Two AUTHENTICATED transitions; a successful bootstrap must run only once.
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)

    fake_bootstrapper.bootstrap.assert_called_once_with(fake_shell)


def test_trigger_retries_when_bootstrap_fails(monkeypatch):
    """A failed bootstrap (e.g. Redis down -> no RE) must NOT consume the
    one-shot: a later AUTHENTICATED retries."""
    fake_shell = MagicMock(name="shell")
    fake_bootstrapper = MagicMock(name="bootstrapper")
    fake_bootstrapper.bootstrap.return_value = False  # failed load

    import lightfall_endstation_cms.session_trigger as st
    _run_inline(monkeypatch)
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: fake_shell)
    monkeypatch.setattr(st, "ProfileSessionBootstrapper", lambda backend=None: fake_bootstrapper)
    monkeypatch.setattr(CMSSessionTrigger, "_notify_failure", staticmethod(lambda: None))

    trigger = CMSSessionTrigger()
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)  # fails
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)  # retries

    assert fake_bootstrapper.bootstrap.call_count == 2
    assert trigger._done is False


def test_bootstrap_is_marshaled_to_main_thread(monkeypatch):
    """Regression: the bootstrap must be dispatched via invoke_in_main_thread,
    NOT run inline on the (background) thread that emits state_changed.

    state_changed is emitted from the login worker thread; running the bootstrap
    there creates QWidgets / imports qtconsole off the GUI thread, which
    deadlocks on the import lock against the main-thread proactive panel init.
    """
    import lightfall_endstation_cms.session_trigger as st

    dispatched: list = []
    monkeypatch.setattr(st, "invoke_in_main_thread", lambda fn, *a, **k: dispatched.append(fn))
    # If the trigger (wrongly) ran inline, this would execute:
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: (_ for _ in ()).throw(AssertionError("ran inline")))

    trigger = CMSSessionTrigger()
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)

    # The work was handed to the main-thread invoker, not executed inline.
    assert dispatched == [trigger._run_bootstrap]
