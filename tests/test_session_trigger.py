from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.session_trigger import CMSSessionTrigger


def test_trigger_runs_bootstrap_once_on_authenticated(monkeypatch):
    fake_shell = MagicMock(name="shell")
    fake_bootstrapper = MagicMock(name="bootstrapper")

    import lightfall_endstation_cms.session_trigger as st
    # Stub shell acquisition and bootstrapper construction.
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: fake_shell)
    monkeypatch.setattr(st, "ProfileSessionBootstrapper", lambda backend: fake_bootstrapper)

    trigger = CMSSessionTrigger(backend=MagicMock())
    # Simulate two AUTHENTICATED transitions; bootstrap must run only once.
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)

    fake_bootstrapper.bootstrap.assert_called_once_with(fake_shell)
