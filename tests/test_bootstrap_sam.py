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


class _SpyCatalog:
    """Records mark_device_live calls."""

    def __init__(self):
        self.marked: list = []

    def mark_device_live(self, device_id, obj, **kw):
        self.marked.append((device_id, obj))
        return True


def _dev(name, obj):
    return SimpleNamespace(name=name, id=uuid4(), _ophyd_device=obj)


def test_inject_devices_binds_instances_under_profile_names(monkeypatch):
    d1 = _dev("smx", object())
    d2 = _dev("pilatus2M", object())
    bs = ProfileSessionBootstrapper(_FakeBackend([d1, d2]))
    monkeypatch.setattr(bs, "_device_catalog", lambda: None)

    ns: dict = {}
    assert bs._inject_devices(ns) == 2
    assert ns["smx"] is d1._ophyd_device
    assert ns["pilatus2M"] is d2._ophyd_device


def test_inject_devices_notifies_catalog(monkeypatch):
    d1 = _dev("smx", object())
    spy = _SpyCatalog()
    bs = ProfileSessionBootstrapper(_FakeBackend([d1]))
    monkeypatch.setattr(bs, "_device_catalog", lambda: spy)

    bs._inject_devices({})
    # The injected device is pushed into the catalog so the UI leaves UNKNOWN.
    assert spy.marked == [(d1.id, d1._ophyd_device)]


def test_inject_devices_falls_back_to_happi_client(monkeypatch):
    obj = object()

    class _Result:
        def get(self):
            return obj

    class _Client:
        def search(self, name=None):
            return [_Result()]

    dev = _dev("bsx", None)
    bs = ProfileSessionBootstrapper(_FakeBackend([dev], client=_Client()))
    monkeypatch.setattr(bs, "_device_catalog", lambda: None)

    ns: dict = {}
    bs._inject_devices(ns)
    assert ns["bsx"] is obj
    # The freshly built instance is shared back onto the catalog entry.
    assert dev._ophyd_device is obj


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
        lambda shell, scripts, label="": calls.append(("run", label)),
    )
    monkeypatch.setattr(bs, "adopt", lambda ns: calls.append(("adopt",)) or True)
    monkeypatch.setattr(bs, "_inject_devices", lambda ns: calls.append(("inject",)) or 0)

    shell = SimpleNamespace(user_ns={})
    assert bs.bootstrap(shell) is True
    assert calls == [("run", "infra"), ("adopt",), ("inject",), ("run", "sam")]


def test_bootstrap_aborts_when_adopt_fails(monkeypatch):
    bs = ProfileSessionBootstrapper(_FakeBackend([]))
    calls: list = []

    monkeypatch.setattr(bs, "_profile_scripts", lambda keep: sorted(keep))
    monkeypatch.setattr(
        bs, "run_profile",
        lambda shell, scripts, label="": calls.append(("run", label)),
    )
    monkeypatch.setattr(bs, "adopt", lambda ns: False)  # no RE -> abort
    monkeypatch.setattr(bs, "_inject_devices", lambda ns: calls.append(("inject",)) or 0)

    shell = SimpleNamespace(user_ns={})
    assert bs.bootstrap(shell) is False
    # Infra ran, then adopt failed -> no injection, no SAM phase.
    assert calls == [("run", "infra")]
