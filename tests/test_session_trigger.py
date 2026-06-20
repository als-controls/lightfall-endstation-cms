from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.session_trigger import CMSSessionTrigger


def test_trigger_runs_bootstrap_once_on_success(monkeypatch):
    fake_shell = MagicMock(name="shell")
    fake_bootstrapper = MagicMock(name="bootstrapper")
    fake_bootstrapper.bootstrap.return_value = True  # success

    import lightfall_endstation_cms.session_trigger as st
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: fake_shell)
    monkeypatch.setattr(st, "ProfileSessionBootstrapper", lambda: fake_bootstrapper)

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
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: fake_shell)
    monkeypatch.setattr(st, "ProfileSessionBootstrapper", lambda: fake_bootstrapper)
    monkeypatch.setattr(CMSSessionTrigger, "_notify_failure", staticmethod(lambda: None))

    trigger = CMSSessionTrigger()
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)  # fails
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)  # retries

    assert fake_bootstrapper.bootstrap.call_count == 2
    assert trigger._done is False
