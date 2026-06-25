"""Device injection + two-phase (infra -> adopt -> inject -> SAM) bootstrap."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


class _FakeBackend:
    def __init__(self, devices, client=None):
        self._devices = devices
        self._client = client

    def list_devices(self, active_only=True):
        return self._devices


def _dev(name, obj):
    return SimpleNamespace(name=name, id=uuid4(), _ophyd_device=obj)


def test_inject_devices_binds_live_instances_under_profile_names():
    d1 = _dev("smx", object())
    d2 = _dev("pilatus2M", object())
    bs = ProfileSessionBootstrapper(_FakeBackend([d1, d2]))

    ns: dict = {}
    assert bs._inject_devices(ns) == 2
    assert ns["smx"] is d1._ophyd_device
    assert ns["pilatus2M"] is d2._ophyd_device


def test_inject_devices_skips_devices_without_live_instance():
    """Injection binds only already-live ophyd objects. A device whose
    ``_ophyd_device`` is None (still connecting / offline IOC) is skipped, NOT
    rebuilt: the happi backend + DeviceConnectionManager are the sole
    instantiator and state-owner, so there is no second device-build path here.
    """
    live = _dev("smx", object())
    not_live = _dev("bsx", None)

    # A client is present but must NOT be consulted: injection never
    # re-instantiates. The spy records any lookup so we can assert it stayed
    # untouched (the old fire-once path called search() here to rebuild).
    class _SpyClient:
        def __init__(self):
            self.searched: list = []

        def search(self, name=None):
            self.searched.append(name)
            return []

    client = _SpyClient()
    bs = ProfileSessionBootstrapper(_FakeBackend([live, not_live], client=client))

    ns: dict = {}
    assert bs._inject_devices(ns) == 1
    assert ns["smx"] is live._ophyd_device
    assert "bsx" not in ns
    assert not_live._ophyd_device is None  # left untouched, not rebuilt
    assert client.searched == [], "injection must not re-instantiate via the happi client"


def test_inject_devices_noop_without_backend():
    assert ProfileSessionBootstrapper()._inject_devices({}) == 0


def test_seed_namespace_sets_config_globals(monkeypatch):
    monkeypatch.delenv("CMS_BEAMLINE_STAGE", raising=False)
    ns: dict = {}
    ProfileSessionBootstrapper._seed_namespace(ns)
    assert ns["beamline_stage"] == "default"  # 10-motors default, needed by 81/94
    assert ns["Pilatus2M_on"] is True


def test_seed_namespace_does_not_overwrite_existing():
    ns = {"beamline_stage": "open_MAXS"}
    ProfileSessionBootstrapper._seed_namespace(ns)
    assert ns["beamline_stage"] == "open_MAXS"


def test_bootstrap_runs_phases_in_order(monkeypatch):
    bs = ProfileSessionBootstrapper(_FakeBackend([]))
    calls: list = []

    monkeypatch.setattr(bs, "_profile_scripts", lambda keep: sorted(keep))
    monkeypatch.setattr(
        bs, "run_profile",
        lambda shell, scripts, label="", after_each=None: calls.append(("run", label)),
    )
    monkeypatch.setattr(
        bs, "adopt_reexpressed_infra", lambda ns: calls.append(("infra",)) or True
    )
    monkeypatch.setattr(bs, "_inject_devices", lambda ns: calls.append(("inject",)) or 0)

    shell = SimpleNamespace(user_ns={})
    assert bs.bootstrap(shell) is True
    # Re-express infra -> inject devices -> run only the SAM scripts.
    assert calls == [("infra",), ("inject",), ("run", "sam")]


def test_bootstrap_aborts_when_adopt_fails(monkeypatch):
    bs = ProfileSessionBootstrapper(_FakeBackend([]))
    calls: list = []

    monkeypatch.setattr(bs, "_profile_scripts", lambda keep: sorted(keep))
    monkeypatch.setattr(
        bs, "run_profile",
        lambda shell, scripts, label="", after_each=None: calls.append(("run", label)),
    )
    monkeypatch.setattr(
        bs, "adopt_reexpressed_infra", lambda ns: False
    )  # no RE -> abort
    monkeypatch.setattr(bs, "_inject_devices", lambda ns: calls.append(("inject",)) or 0)

    shell = SimpleNamespace(user_ns={})
    assert bs.bootstrap(shell) is False
    # Infra adoption failed -> no injection, no SAM phase.
    assert calls == []
