"""Tests for the CMS assets_path resolver and detector-module wiring.

These cover the gap left when 00-startup's assets_path() wiring was dropped from
the post-login plugin flow (the ``area_detectors.assets_path is not set`` crash).
"""
from __future__ import annotations

import sys
import types

import pytest

from lightfall_endstation_cms import assets


class _FakeRE:
    def __init__(self, md):
        self.md = md


def _install_fake_engine(monkeypatch, md):
    """Make ``lightfall.acquire.get_engine().RE.md`` return *md*."""
    fake_engine = types.SimpleNamespace(RE=_FakeRE(md))
    fake_acquire = types.ModuleType("lightfall.acquire")
    fake_acquire.get_engine = lambda: fake_engine
    monkeypatch.setitem(sys.modules, "lightfall.acquire", fake_acquire)


def test_env_override_wins_and_normalizes_trailing_slash(monkeypatch):
    monkeypatch.setenv(assets._ASSETS_PATH_ENV, "/tmp/cms_assets")
    # Override is used even when RE.md has no proposal context.
    _install_fake_engine(monkeypatch, {})
    assert assets.assets_path() == "/tmp/cms_assets/"


def test_env_override_keeps_existing_trailing_slash(monkeypatch):
    monkeypatch.setenv(assets._ASSETS_PATH_ENV, "/tmp/cms_assets/")
    assert assets.assets_path() == "/tmp/cms_assets/"


def test_resolves_from_re_md_proposal_context(monkeypatch):
    monkeypatch.delenv(assets._ASSETS_PATH_ENV, raising=False)
    _install_fake_engine(monkeypatch, {"cycle": "2025-1", "data_session": "PAS-123456"})
    assert assets.assets_path() == (
        "/nsls2/data/cms/proposals/2025-1/PAS-123456/assets/"
    )


def test_raises_when_proposal_context_missing(monkeypatch):
    monkeypatch.delenv(assets._ASSETS_PATH_ENV, raising=False)
    _install_fake_engine(monkeypatch, {"versions": {}})  # no cycle/data_session
    with pytest.raises(RuntimeError, match="assets_path is unresolved"):
        assets.assets_path()


def test_never_emits_none_segments(monkeypatch):
    """A partial RE.md must not yield .../proposals/None/None/assets/."""
    monkeypatch.delenv(assets._ASSETS_PATH_ENV, raising=False)
    _install_fake_engine(monkeypatch, {"cycle": "2025-1"})  # data_session missing
    with pytest.raises(RuntimeError):
        assets.assets_path()


def test_wire_assets_path_sets_hook_on_both_modules(monkeypatch):
    fake_ad = types.ModuleType("area_detectors")
    fake_ad.assets_path = None
    fake_xs = types.ModuleType("xspress3")
    fake_xs.assets_path = None

    fake_devices = types.ModuleType("lightfall_endstation_cms.devices")
    fake_devices.area_detectors = fake_ad
    fake_devices.xspress3 = fake_xs
    monkeypatch.setitem(
        sys.modules, "lightfall_endstation_cms.devices", fake_devices
    )

    assets.wire_assets_path()

    assert fake_ad.assets_path is assets.assets_path
    assert fake_xs.assets_path is assets.assets_path


def test_wire_assets_path_best_effort_when_import_fails(monkeypatch):
    """A missing nslsii (detector import failure) must not raise."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "lightfall_endstation_cms.devices" or name.endswith(
            "lightfall_endstation_cms.devices"
        ):
            raise ImportError("nslsii missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(
        sys.modules, "lightfall_endstation_cms.devices", raising=False
    )
    monkeypatch.setattr(builtins, "__import__", _boom)
    # Should swallow the import error and simply return.
    assets.wire_assets_path()
