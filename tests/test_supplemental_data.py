"""Tests for re-expressing SupplementalData (00-startup migration)."""

from __future__ import annotations

import sys
import types

from lightfall_endstation_cms import supplemental_data


class _FakeSD:
    """Stand-in for bluesky.preprocessors.SupplementalData."""


def _install_fakes(monkeypatch, *, run_engine, sd_cls=_FakeSD):
    fake_preproc = types.ModuleType("bluesky.preprocessors")
    fake_preproc.SupplementalData = sd_cls
    monkeypatch.setitem(sys.modules, "bluesky.preprocessors", fake_preproc)

    fake_acquire = types.ModuleType("lightfall.acquire")
    fake_acquire.get_engine = lambda: types.SimpleNamespace(RE=run_engine)
    monkeypatch.setitem(sys.modules, "lightfall.acquire", fake_acquire)

    monkeypatch.setattr(supplemental_data, "_sd", None)


def test_appends_supplemental_data_to_preprocessors(monkeypatch):
    re = types.SimpleNamespace(preprocessors=[])
    _install_fakes(monkeypatch, run_engine=re)

    sd = supplemental_data.wire_supplemental_data()

    assert len(re.preprocessors) == 1
    assert re.preprocessors[0] is sd
    assert supplemental_data.get_supplemental_data() is sd


def test_idempotent_does_not_append_twice(monkeypatch):
    re = types.SimpleNamespace(preprocessors=[])
    _install_fakes(monkeypatch, run_engine=re)

    sd1 = supplemental_data.wire_supplemental_data()
    sd2 = supplemental_data.wire_supplemental_data()

    assert sd1 is sd2
    assert len(re.preprocessors) == 1


def test_best_effort_when_run_engine_missing(monkeypatch):
    _install_fakes(monkeypatch, run_engine=None)
    assert supplemental_data.wire_supplemental_data() is None


def test_best_effort_when_bluesky_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_bluesky(name, *args, **kwargs):
        if name == "bluesky.preprocessors":
            raise ImportError("bluesky missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "bluesky.preprocessors", raising=False)
    monkeypatch.setattr(builtins, "__import__", _no_bluesky)
    monkeypatch.setattr(supplemental_data, "_sd", None)
    assert supplemental_data.wire_supplemental_data() is None
