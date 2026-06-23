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
