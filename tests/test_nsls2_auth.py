from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.auth.nsls2_provider import (
    NSLS2AuthPlugin,
    NSLS2TiledAuthProvider,
)


def test_select_password_provider_picks_internal():
    providers = [
        SimpleNamespace(mode="external", provider="oidc"),
        SimpleNamespace(mode="internal", provider="auth"),
    ]
    spec = NSLS2TiledAuthProvider._select_password_provider(providers)
    assert spec.mode == "internal"


def test_select_password_provider_raises_on_empty_list():
    # Must not IndexError on an empty providers list.
    with pytest.raises(RuntimeError):
        NSLS2TiledAuthProvider._select_password_provider([])


def test_select_password_provider_raises_when_no_password_mode():
    # Must not silently fall back to a non-password (e.g. OAuth) provider.
    providers = [SimpleNamespace(mode="external", provider="oidc")]
    with pytest.raises(RuntimeError):
        NSLS2TiledAuthProvider._select_password_provider(providers)


def test_plugin_metadata():
    plugin = NSLS2AuthPlugin()
    assert plugin.name == "nsls2_tiled"
    assert plugin.display_name == "NSLS-II (CMS)"
    assert plugin.requires_username is True
    # Password is collected (masked) in the login dialog and exchanged for a
    # tiled token; it is never stored by Lightfall.
    assert plugin.requires_password is True
    assert isinstance(plugin.create_provider(), NSLS2TiledAuthProvider)


def test_authenticate_warms_login_and_returns_session():
    calls = {}

    class _TestProvider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username, password):
            calls["user"] = username
            calls["password"] = password
            return True  # pretend the password grant succeeded + token cached

    provider = _TestProvider()
    session = asyncio.run(provider.authenticate(username="rond", password="pw"))

    assert calls["user"] == "rond"
    assert calls["password"] == "pw"
    assert session is not None
    assert session.user.username == "rond"


def test_authenticate_requires_password():
    """Username alone is not enough — password is required (no token grant)."""
    called = {"v": False}

    class _Provider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username, password):
            called["v"] = True
            return True

    provider = _Provider()
    assert asyncio.run(provider.authenticate(username="rond")) is None
    assert called["v"] is False  # _tiled_login not reached without a password


def test_authenticate_returns_none_on_login_failure():
    class _FailProvider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username, password):
            return False

    provider = _FailProvider()
    assert asyncio.run(provider.authenticate(username="rond", password="pw")) is None


def test_authenticate_returns_none_when_tiled_login_raises():
    class _RaiseProvider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username, password):
            raise RuntimeError("password grant failed")

    provider = _RaiseProvider()
    assert asyncio.run(provider.authenticate(username="rond", password="pw")) is None
