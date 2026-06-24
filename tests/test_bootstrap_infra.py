"""Tests for the re-expressed-infra path of ProfileSessionBootstrapper (Arch B).

Covers ``adopt_reexpressed_infra`` (wire the 5 helpers + seed the SAM namespace)
and ``_seed_tiled_namespace`` (cat/db/mig from the service key). All external
deps are mocked; the live data path is box-validated separately.
"""

from __future__ import annotations

import sys
import types

import lightfall_endstation_cms.bootstrap as boot
from lightfall_endstation_cms import (
    assets,
    kafka_publisher,
    run_engine_md,
    supplemental_data,
    tiled_writer,
)


def _patch_helpers(monkeypatch):
    calls = []
    sd_obj = object()
    monkeypatch.setattr(run_engine_md, "wire_redis_metadata", lambda: calls.append("redis"))
    monkeypatch.setattr(kafka_publisher, "wire_kafka_publisher", lambda: calls.append("kafka"))
    monkeypatch.setattr(
        supplemental_data,
        "wire_supplemental_data",
        lambda: (calls.append("sd"), sd_obj)[1],
    )
    monkeypatch.setattr(assets, "wire_assets_path", lambda: calls.append("assets"))
    monkeypatch.setattr(tiled_writer, "wire_tiled_writer", lambda: calls.append("tiled"))
    return calls, sd_obj


def test_adopt_reexpressed_infra_wires_and_seeds(monkeypatch):
    calls, sd_obj = _patch_helpers(monkeypatch)
    engine = types.SimpleNamespace(RE=object())
    monkeypatch.setattr(boot, "get_engine", lambda: engine)
    monkeypatch.setattr(boot, "ConsoleREProxy", lambda e: ("proxy", e))
    # No service key -> _seed_tiled_namespace returns early (no tiled deps needed).
    monkeypatch.delenv("TILED_BLUESKY_WRITING_API_KEY_CMS", raising=False)

    ns: dict = {}
    ok = boot.ProfileSessionBootstrapper().adopt_reexpressed_infra(ns)

    assert ok is True
    assert set(calls) == {"redis", "kafka", "sd", "assets", "tiled"}
    assert ns["RE"] == ("proxy", engine)
    assert ns["assets_path"] is assets.assets_path
    assert ns["proposal_path"] is assets.proposal_path
    assert ns["sd"] is sd_obj


def test_adopt_reexpressed_infra_false_when_no_run_engine(monkeypatch):
    _patch_helpers(monkeypatch)
    monkeypatch.setattr(boot, "get_engine", lambda: types.SimpleNamespace(RE=None))
    assert boot.ProfileSessionBootstrapper().adopt_reexpressed_infra({}) is False


def test_seed_tiled_namespace_warns_without_key(monkeypatch):
    monkeypatch.delenv("TILED_BLUESKY_WRITING_API_KEY_CMS", raising=False)
    ns: dict = {}
    boot.ProfileSessionBootstrapper._seed_tiled_namespace(ns)
    assert "cat" not in ns and "db" not in ns


def test_seed_tiled_namespace_seeds_clients(monkeypatch):
    monkeypatch.setenv("TILED_BLUESKY_WRITING_API_KEY_CMS", "fake-key")

    class _Sub:
        def __getitem__(self, k):
            return f"raw-node-{k}"

    class _Client:
        def __getitem__(self, k):
            if k == "cms":
                return _Sub()
            if k == "cms/migration":
                return "mig-node"
            raise KeyError(k)

    fake_tiled = types.ModuleType("tiled.client")
    fake_tiled.from_uri = lambda uri, api_key: _Client()
    monkeypatch.setitem(sys.modules, "tiled.client", fake_tiled)

    fake_db = types.ModuleType("databroker")
    fake_db.Broker = lambda cat: f"broker({cat})"
    monkeypatch.setitem(sys.modules, "databroker", fake_db)

    ns: dict = {}
    boot.ProfileSessionBootstrapper._seed_tiled_namespace(ns)

    assert ns["cat"] == "raw-node-raw"
    assert ns["tiled_reading_client"] == "raw-node-raw"
    assert ns["tiled_writing_client"] == "raw-node-raw"
    assert ns["mig"] == "mig-node"
    assert ns["db"] == "broker(raw-node-raw)"


def test_seed_profile_imports_seeds_os_and_stdlib():
    import os as _os

    ns: dict = {}
    boot.ProfileSessionBootstrapper._seed_profile_imports(ns)
    # 90-bluesky's os.path.join relies on 00-startup's leaked ``import os``.
    assert ns["os"] is _os
    for mod in ("asyncio", "queue", "threading", "contextlib"):
        assert mod in ns


def test_seed_profile_imports_does_not_overwrite():
    sentinel = object()
    ns: dict = {"os": sentinel}
    boot.ProfileSessionBootstrapper._seed_profile_imports(ns)
    assert ns["os"] is sentinel


def test_redirect_config_paths_redirects_when_unreadable(monkeypatch, tmp_path):
    readable = tmp_path / ".cms_config"
    readable.write_text("x")
    monkeypatch.setenv("CMS_CONFIG_FILENAME_FALLBACK", str(readable))
    ns = {"CMS_CONFIG_FILENAME": "/nonexistent/xf11bm/.cms_config"}
    script = types.SimpleNamespace(name="90-bluesky.py")
    boot.ProfileSessionBootstrapper._redirect_config_paths(script, ns)
    assert ns["CMS_CONFIG_FILENAME"] == str(readable)


def test_redirect_config_paths_noop_when_readable(tmp_path):
    readable = tmp_path / ".cms_config"
    readable.write_text("x")
    ns = {"CMS_CONFIG_FILENAME": str(readable)}  # readable -> xf11bm case, untouched
    script = types.SimpleNamespace(name="90-bluesky.py")
    boot.ProfileSessionBootstrapper._redirect_config_paths(script, ns)
    assert ns["CMS_CONFIG_FILENAME"] == str(readable)


def test_redirect_config_paths_only_after_90():
    ns = {"CMS_CONFIG_FILENAME": "/nonexistent/x"}
    script = types.SimpleNamespace(name="94-sample.py")
    boot.ProfileSessionBootstrapper._redirect_config_paths(script, ns)
    assert ns["CMS_CONFIG_FILENAME"] == "/nonexistent/x"


def test_seed_device_classes_seeds_shutter_and_camera_classes():
    from lightfall_endstation_cms.devices import area_detectors, shutters

    ns: dict = {}
    boot.ProfileSessionBootstrapper._seed_device_classes(ns)
    assert ns["TriState"] is shutters.TriState
    assert ns["TwoButtonShutterNC"] is shutters.TwoButtonShutterNC
    assert ns["StandardProsilica"] is area_detectors.StandardProsilica


def test_seed_device_classes_does_not_overwrite():
    sentinel = object()
    ns = {"TriState": sentinel}
    boot.ProfileSessionBootstrapper._seed_device_classes(ns)
    assert ns["TriState"] is sentinel


def test_seed_ophyd_names_seeds_classes():
    import ophyd

    ns: dict = {}
    boot.ProfileSessionBootstrapper._seed_ophyd_names(ns)
    assert ns["EpicsSignal"] is ophyd.EpicsSignal
    assert ns["EpicsSignalRO"] is ophyd.EpicsSignalRO
    assert ns["EpicsMotor"] is ophyd.EpicsMotor
    assert ns["Device"] is ophyd.Device
    assert ns["Component"] is ophyd.Component
    assert ns["Cpt"] is ophyd.Component  # alias


def test_seed_ophyd_names_does_not_overwrite():
    sentinel = object()
    ns = {"EpicsSignal": sentinel}
    boot.ProfileSessionBootstrapper._seed_ophyd_names(ns)
    assert ns["EpicsSignal"] is sentinel


def test_seed_profile_imports_seeds_time_and_np_alias():
    import numpy
    import time as _time

    ns: dict = {}
    boot.ProfileSessionBootstrapper._seed_profile_imports(ns)
    # 81-beam uses time.* and np.* un-imported (it has zero top-level imports).
    assert ns["time"] is _time
    assert ns["np"] is numpy
