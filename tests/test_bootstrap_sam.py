"""Device injection + two-phase (infra -> adopt -> inject -> SAM) bootstrap."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


class _FakeBackend:
    def __init__(self, devices, client=None):
        self._devices = devices
        self._client = client

    def list_devices(self, active_only=True):
        return self._devices


def test_inject_devices_binds_instances_under_profile_names():
    d1 = SimpleNamespace(name="smx", _ophyd_device=object())
    d2 = SimpleNamespace(name="pilatus2M", _ophyd_device=object())
    bs = ProfileSessionBootstrapper(_FakeBackend([d1, d2]))

    ns: dict = {}
    assert bs._inject_devices(ns) == 2
    assert ns["smx"] is d1._ophyd_device
    assert ns["pilatus2M"] is d2._ophyd_device


def test_inject_devices_falls_back_to_happi_client():
    obj = object()

    class _Result:
        def get(self):
            return obj

    class _Client:
        def search(self, name=None):
            return [_Result()]

    dev = SimpleNamespace(name="bsx", _ophyd_device=None)
    bs = ProfileSessionBootstrapper(_FakeBackend([dev], client=_Client()))

    ns: dict = {}
    bs._inject_devices(ns)
    assert ns["bsx"] is obj
    # The freshly built instance is shared back onto the catalog entry.
    assert dev._ophyd_device is obj


def test_inject_devices_noop_without_backend():
    assert ProfileSessionBootstrapper()._inject_devices({}) == 0


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
