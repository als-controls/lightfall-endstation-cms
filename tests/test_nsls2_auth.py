from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.auth.nsls2_provider import (
    NSLS2AuthPlugin,
    NSLS2TiledAuthProvider,
)


def test_plugin_metadata():
    plugin = NSLS2AuthPlugin()
    assert plugin.name == "nsls2_tiled"
    assert plugin.display_name == "NSLS-II (CMS)"
    assert plugin.requires_username is True
    assert plugin.requires_password is False
    assert isinstance(plugin.create_provider(), NSLS2TiledAuthProvider)


def test_authenticate_warms_login_and_returns_session():
    calls = {}

    class _TestProvider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username):
            calls["user"] = username
            return True  # pretend Duo succeeded + token cached

    provider = _TestProvider()
    session = asyncio.run(provider.authenticate(username="rond"))

    assert calls["user"] == "rond"
    assert session is not None
    assert session.user.username == "rond"


def test_authenticate_returns_none_on_login_failure():
    class _FailProvider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username):
            return False

    provider = _FailProvider()
    assert asyncio.run(provider.authenticate(username="rond")) is None
