# tests/test_bootstrap_adopt.py
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ophyd.sim import SynAxis

from lightfall_endstation_cms.backends.profile_collection import ProfileCollectionBackend
from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


def test_adopt_wires_engine_devices_and_tiled(monkeypatch):
    # Stub namespace as if the profile had run.
    fake_re = MagicMock(name="RE")
    fake_mig = MagicMock(name="mig")
    ns = {"RE": fake_re, "mig": fake_mig, "smx": SynAxis(name="smx")}

    fake_engine = MagicMock(name="engine")
    fake_engine.RE = fake_re
    fake_tiled = MagicMock(name="TiledService")

    import lightfall_endstation_cms.bootstrap as boot
    monkeypatch.setattr(boot, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(boot.TiledService, "get_instance", classmethod(lambda cls: fake_tiled))

    backend = ProfileCollectionBackend()
    bootstrapper = ProfileSessionBootstrapper(backend)

    bootstrapper.adopt(ns)

    # Engine adopted the profile's RE.
    fake_engine.adopt.assert_called_once_with(fake_re)
    # RE name rebound to a ConsoleREProxy (callable; delegates to engine.RE).
    from lightfall.acquire.engine.console_proxy import ConsoleREProxy
    assert isinstance(ns["RE"], ConsoleREProxy)
    # Devices populated from the live namespace.
    assert backend.is_connected is True
    assert backend.get_device_by_name("smx") is not None
    # mig adopted as the Tiled reading client.
    fake_tiled.adopt_client.assert_called_once()
    args, kwargs = fake_tiled.adopt_client.call_args
    assert args[0] is fake_mig


def test_adopt_skips_gracefully_when_no_RE(monkeypatch):
    """If the profile failed before creating RE (e.g. Redis unreachable), adopt
    must log and return without raising — not KeyError on namespace['RE']."""
    fake_engine = MagicMock(name="engine")
    fake_tiled = MagicMock(name="TiledService")

    import lightfall_endstation_cms.bootstrap as boot
    monkeypatch.setattr(boot, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(boot.TiledService, "get_instance", classmethod(lambda cls: fake_tiled))

    bootstrapper = ProfileSessionBootstrapper(ProfileCollectionBackend())

    bootstrapper.adopt({})  # no "RE" — must not raise

    fake_engine.adopt.assert_not_called()
    fake_tiled.adopt_client.assert_not_called()
