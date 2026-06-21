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
            return object()  # a (fake) authenticated client

        def _adopt_browser_client(self, client):
            calls["adopt"] += 1

    session = asyncio.run(_Provider().authenticate(username="rond", password="pw"))
    assert session is not None
    assert calls["adopt"] == 1


def test_authenticate_skips_browser_adopt_on_login_failure():
    """No data-browser adoption when the tiled login fails."""
    calls = {"adopt": 0}

    class _Provider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username, password):
            return None

        def _adopt_browser_client(self, client):
            calls["adopt"] += 1

    assert asyncio.run(_Provider().authenticate(username="rond", password="pw")) is None
    assert calls["adopt"] == 0


def test_authenticate_threads_login_client_to_adopt():
    """The authenticated client from _tiled_login must be handed to
    _adopt_browser_client — NOT discarded. This is the seam that makes the data
    browser reuse the duo-warmed session instead of an anonymous client."""
    seen = {}
    sentinel = object()

    class _Provider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username, password):
            return sentinel

        def _adopt_browser_client(self, client):
            seen["client"] = client

    session = asyncio.run(_Provider().authenticate(username="rond", password="pw"))
    assert session is not None
    assert seen["client"] is sentinel


def test_adopt_browser_client_navigates_the_login_client(monkeypatch):
    """_adopt_browser_client navigates the AUTHENTICATED client passed in
    (login's duo-warmed client) down [cms][raw] and hands that node to
    TiledService.adopt_client. It must NOT build a fresh from_profile() client
    (which would be anonymous and list zero runs)."""
    import tiled.client as tiled_client

    import lightfall_endstation_cms.auth.nsls2_provider as mod

    # A fresh from_profile() here is the bug: fail loudly if anyone calls it.
    def _forbidden(*a, **k):
        raise AssertionError("must reuse the login client, not call from_profile()")

    monkeypatch.setattr(tiled_client, "from_profile", _forbidden)
    monkeypatch.setattr(mod, "invoke_in_main_thread", lambda fn, *a, **k: fn(*a, **k))

    class _Node:
        def __init__(self, path=()):
            self.path = path

        def __getitem__(self, key):
            return _Node((*self.path, key))

    authed = _Node(("AUTHED_ROOT",))

    adopted = {}

    class _FakeService:
        def adopt_client(self, client, url=""):
            adopted["client"] = client
            adopted["url"] = url

    import lightfall.services.tiled_service as svc

    monkeypatch.setattr(svc.TiledService, "get_instance", classmethod(lambda cls: _FakeService()))

    mod.NSLS2TiledAuthProvider()._adopt_browser_client(authed)

    # Navigated from the AUTHENTICATED client, through the browse path.
    assert adopted["client"].path == ("AUTHED_ROOT", *mod._BROWSE_PATH)
    assert adopted["url"] == mod.TILED_URI


def test_adopt_browser_client_is_best_effort():
    """A browser node that can't be opened must never raise into the login flow."""
    import lightfall_endstation_cms.auth.nsls2_provider as mod

    class _Boom:
        def __getitem__(self, key):
            raise RuntimeError("node unavailable")

    # Must not raise even though navigating the client blows up.
    mod.NSLS2TiledAuthProvider()._adopt_browser_client(_Boom())
