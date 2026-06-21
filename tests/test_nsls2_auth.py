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
    assert plugin.accent_color == "#2e7d32"  # green login button
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


def test_authenticate_adopts_browser_client_on_success():
    """On a successful login the warm-token data-browser client is adopted."""
    calls = {"adopt": 0}

    class _Provider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username, password):
            return True

        def _adopt_browser_client(self):
            calls["adopt"] += 1

    session = asyncio.run(_Provider().authenticate(username="rond", password="pw"))
    assert session is not None
    assert calls["adopt"] == 1


def test_authenticate_skips_browser_adopt_on_login_failure():
    """No data-browser adoption when the tiled login fails."""
    calls = {"adopt": 0}

    class _Provider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username, password):
            return False

        def _adopt_browser_client(self):
            calls["adopt"] += 1

    assert asyncio.run(_Provider().authenticate(username="rond", password="pw")) is None
    assert calls["adopt"] == 0


def test_adopt_browser_client_adopts_warm_reading_node(monkeypatch):
    """_adopt_browser_client opens from_profile(nsls2)[cms][raw] (reusing the
    duo-warmed token) and hands it to TiledService.adopt_client."""
    import tiled.client as tiled_client

    import lightfall_endstation_cms.auth.nsls2_provider as mod

    class _Node:
        def __init__(self, path=()):
            self.path = path

        def __getitem__(self, key):
            return _Node((*self.path, key))

    monkeypatch.setattr(tiled_client, "from_profile", lambda name: _Node((name,)))
    monkeypatch.setattr(mod, "invoke_in_main_thread", lambda fn, *a, **k: fn(*a, **k))

    adopted = {}

    class _FakeService:
        def adopt_client(self, client, url=""):
            adopted["client"] = client
            adopted["url"] = url

    import lightfall.services.tiled_service as svc

    monkeypatch.setattr(svc.TiledService, "get_instance", classmethod(lambda cls: _FakeService()))

    mod.NSLS2TiledAuthProvider()._adopt_browser_client()

    assert adopted["client"].path == (mod.TILED_PROFILE, *mod._BROWSE_PATH)
    assert adopted["url"] == mod.TILED_URI


def test_adopt_browser_client_is_best_effort(monkeypatch):
    """A browser that can't connect must never raise into the login flow."""
    import tiled.client as tiled_client

    import lightfall_endstation_cms.auth.nsls2_provider as mod

    def _boom(name):
        raise RuntimeError("no tiled profile")

    monkeypatch.setattr(tiled_client, "from_profile", _boom)
    # Must not raise.
    mod.NSLS2TiledAuthProvider()._adopt_browser_client()
