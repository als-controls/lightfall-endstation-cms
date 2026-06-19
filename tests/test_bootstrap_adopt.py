# tests/test_bootstrap_adopt.py
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ophyd.sim import SynAxis

from lightfall_endstation_cms.backends.profile_collection import ProfileCollectionBackend
from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


def _patch_engine_and_tiled(monkeypatch, fake_re):
    fake_engine = MagicMock(name="engine")
    fake_engine.RE = fake_re
    fake_tiled = MagicMock(name="TiledService")

    import lightfall_endstation_cms.bootstrap as boot

    monkeypatch.setattr(boot, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(
        boot.TiledService, "get_instance", classmethod(lambda cls: fake_tiled)
    )
    return fake_engine, fake_tiled


def test_adopt_wires_engine_devices_and_writing_client(monkeypatch):
    # Stub namespace as if the profile had run.
    fake_re = MagicMock(name="RE")
    fake_mig = MagicMock(name="mig")
    fake_writer = MagicMock(name="tiled_writing_client")
    ns = {
        "RE": fake_re,
        "mig": fake_mig,
        "tiled_writing_client": fake_writer,
        "smx": SynAxis(name="smx"),
    }

    fake_engine, fake_tiled = _patch_engine_and_tiled(monkeypatch, fake_re)

    backend = ProfileCollectionBackend()
    bootstrapper = ProfileSessionBootstrapper(backend)

    assert bootstrapper.adopt(ns) is True

    # Engine adopted the profile's RE.
    fake_engine.adopt.assert_called_once_with(fake_re)
    # RE name rebound to a ConsoleREProxy (callable; delegates to engine.RE).
    from lightfall.acquire.engine.console_proxy import ConsoleREProxy

    assert isinstance(ns["RE"], ConsoleREProxy)
    # Devices populated from the live namespace.
    assert backend.is_connected is True
    assert backend.get_device_by_name("smx") is not None
    # The write-scoped client is adopted (not the anonymous/Duo-gated mig).
    fake_tiled.adopt_client.assert_called_once()
    args, kwargs = fake_tiled.adopt_client.call_args
    assert args[0] is fake_writer


def test_adopt_falls_back_to_mig_without_writing_client(monkeypatch):
    """When the profile predates `tiled_writing_client`, adopt the legacy `mig`."""
    fake_re = MagicMock(name="RE")
    fake_mig = MagicMock(name="mig")
    ns = {"RE": fake_re, "mig": fake_mig}

    _, fake_tiled = _patch_engine_and_tiled(monkeypatch, fake_re)

    bootstrapper = ProfileSessionBootstrapper(ProfileCollectionBackend())
    assert bootstrapper.adopt(ns) is True

    fake_tiled.adopt_client.assert_called_once()
    args, _ = fake_tiled.adopt_client.call_args
    assert args[0] is fake_mig


def test_adopt_skips_gracefully_when_no_RE(monkeypatch):
    """If the profile failed before creating RE (e.g. Redis unreachable), adopt
    must log and return without raising — not KeyError on namespace['RE']."""
    fake_engine, fake_tiled = _patch_engine_and_tiled(monkeypatch, MagicMock())

    bootstrapper = ProfileSessionBootstrapper(ProfileCollectionBackend())

    assert bootstrapper.adopt({}) is False  # no "RE" — must not raise

    fake_engine.adopt.assert_not_called()
    fake_tiled.adopt_client.assert_not_called()
