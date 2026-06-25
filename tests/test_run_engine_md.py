"""Tests for restoring the redis-backed RunEngine metadata store.

Covers the second half of the 00-startup migration gap: the redis-backed
``RE.md`` that ``assets_path()`` reads (cycle/data_session).
"""
from __future__ import annotations

import sys
import types

from lightfall_endstation_cms import run_engine_md


class _FakeRedisJSONDict(dict):
    """Stand-in for redis_json_dict.RedisJSONDict (a dict subclass)."""

    def __init__(self, redis_client=None, prefix=""):
        super().__init__()
        self.redis_client = redis_client
        self.prefix = prefix


def _install_fakes(monkeypatch, *, run_engine, redis_dict_cls=_FakeRedisJSONDict, open_client=None):
    """Install fake nslsii / redis_json_dict / lightfall.acquire modules."""
    calls = {"open_redis_client": []}

    def _default_open(*args, **kwargs):
        calls["open_redis_client"].append(kwargs or args)
        return "fake-redis-client"

    fake_nslsii = types.ModuleType("nslsii")
    fake_nslsii.open_redis_client = open_client or _default_open
    monkeypatch.setitem(sys.modules, "nslsii", fake_nslsii)

    fake_rjd = types.ModuleType("redis_json_dict")
    fake_rjd.RedisJSONDict = redis_dict_cls
    monkeypatch.setitem(sys.modules, "redis_json_dict", fake_rjd)

    fake_acquire = types.ModuleType("lightfall.acquire")
    fake_acquire.get_engine = lambda: types.SimpleNamespace(RE=run_engine)
    monkeypatch.setitem(sys.modules, "lightfall.acquire", fake_acquire)

    return calls


def test_assigns_redis_backed_md_to_run_engine(monkeypatch):
    re = types.SimpleNamespace(md={"versions": {}})  # plain dict initially
    calls = _install_fakes(monkeypatch, run_engine=re)

    run_engine_md.wire_redis_metadata()

    assert isinstance(re.md, _FakeRedisJSONDict)
    assert re.md.prefix == ""
    assert re.md.redis_client == "fake-redis-client"
    # open_redis_client was called with the CMS redis url.
    assert calls["open_redis_client"]
    assert any(
        run_engine_md._REDIS_URL in str(c) for c in calls["open_redis_client"]
    )


def test_idempotent_when_already_redis_backed(monkeypatch):
    existing = _FakeRedisJSONDict()
    existing["cycle"] = "2025-1"
    re = types.SimpleNamespace(md=existing)
    calls = _install_fakes(monkeypatch, run_engine=re)

    run_engine_md.wire_redis_metadata()

    # Left as-is; not rebuilt.
    assert re.md is existing
    assert calls["open_redis_client"] == []


def test_best_effort_when_run_engine_missing(monkeypatch):
    _install_fakes(monkeypatch, run_engine=None)
    # Must not raise even though there is no RE to configure.
    run_engine_md.wire_redis_metadata()


def test_best_effort_when_open_redis_client_raises(monkeypatch):
    re = types.SimpleNamespace(md={})

    def _boom(*args, **kwargs):
        raise OSError("redis unreachable")

    _install_fakes(monkeypatch, run_engine=re, open_client=_boom)
    # Connection failure must not abort startup; md is left untouched.
    run_engine_md.wire_redis_metadata()
    assert re.md == {}


def test_best_effort_when_imports_fail(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_nslsii(name, *args, **kwargs):
        if name == "nslsii" or name == "redis_json_dict":
            raise ImportError(f"{name} missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "nslsii", raising=False)
    monkeypatch.delitem(sys.modules, "redis_json_dict", raising=False)
    monkeypatch.setattr(builtins, "__import__", _no_nslsii)
    # Should swallow the import error and return.
    run_engine_md.wire_redis_metadata()
