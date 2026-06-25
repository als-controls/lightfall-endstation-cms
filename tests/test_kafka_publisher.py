"""Tests for re-expressing the Kafka document publisher (00-startup migration)."""

from __future__ import annotations

import sys
import types

from lightfall_endstation_cms import kafka_publisher


def _install_fakes(monkeypatch, *, run_engine, configure=None):
    calls = {"configure_kafka_publisher": []}

    def _default_configure(RE, beamline_name, **kwargs):
        calls["configure_kafka_publisher"].append((RE, beamline_name))

    fake_nslsii = types.ModuleType("nslsii")
    fake_nslsii.configure_kafka_publisher = configure or _default_configure
    monkeypatch.setitem(sys.modules, "nslsii", fake_nslsii)

    fake_acquire = types.ModuleType("lightfall.acquire")
    fake_acquire.get_engine = lambda: types.SimpleNamespace(RE=run_engine)
    monkeypatch.setitem(sys.modules, "lightfall.acquire", fake_acquire)

    monkeypatch.setattr(kafka_publisher, "_subscribed", False)
    return calls


def test_subscribes_kafka_publisher_with_cms_beamline(monkeypatch):
    re = object()
    calls = _install_fakes(monkeypatch, run_engine=re)

    kafka_publisher.wire_kafka_publisher()

    assert calls["configure_kafka_publisher"] == [(re, "cms")]


def test_idempotent_does_not_resubscribe(monkeypatch):
    re = object()
    calls = _install_fakes(monkeypatch, run_engine=re)

    kafka_publisher.wire_kafka_publisher()
    kafka_publisher.wire_kafka_publisher()

    assert len(calls["configure_kafka_publisher"]) == 1


def test_best_effort_when_run_engine_missing(monkeypatch):
    _install_fakes(monkeypatch, run_engine=None)
    # No RE -> must not raise and must not mark itself subscribed.
    kafka_publisher.wire_kafka_publisher()
    assert kafka_publisher._subscribed is False


def test_best_effort_when_configure_raises(monkeypatch):
    def _boom(RE, beamline_name, **kwargs):
        raise RuntimeError("no kafka broker reachable")

    _install_fakes(monkeypatch, run_engine=object(), configure=_boom)
    # Broker failure must not abort startup.
    kafka_publisher.wire_kafka_publisher()
    assert kafka_publisher._subscribed is False


def test_default_beamline_name_is_cms():
    # Matches 00-startup's TiledInserter.name; the kafka topic derives from it.
    assert kafka_publisher._BEAMLINE_NAME == "cms"
