"""Tests for subscribing a Tiled document writer to the shared RunEngine.

Covers the third 00-startup migration gap: the document-writing piece
(configure_base's tiled_inserter) that persists runs to cms/raw. Without it the
native RunEngine dispatches documents to the GUI but nothing reaches Tiled.
"""
from __future__ import annotations

import sys
import types

import pytest

from lightfall_endstation_cms import tiled_writer


@pytest.fixture(autouse=True)
def _reset_module_state():
    # _writer/_token are module-level (the idempotency guard); reset around each
    # test so ordering does not leak a "already subscribed" state.
    tiled_writer._writer = None
    tiled_writer._token = None
    yield
    tiled_writer._writer = None
    tiled_writer._token = None


class _FakeEngine:
    def __init__(self):
        self.subscribed = []
        self._next = 41

    def subscribe(self, cb):
        self._next += 1
        self.subscribed.append(cb)
        return self._next  # non-None token (may differ from 0)


def _install_fakes(monkeypatch, *, engine, from_uri=None):
    """Install fake tiled.client / lightfall.acquire / writer modules."""
    calls = {"from_uri": [], "ThreadedTiledWriter": []}

    def _default_from_uri(url, api_key=None, **kwargs):
        rec = {"url": url, "api_key": api_key}
        calls["from_uri"].append(rec)

        class _Catalog:
            def __getitem__(self, key):
                rec["node"] = key
                return f"client:{key}"

        return _Catalog()

    fake_tiled = types.ModuleType("tiled.client")
    fake_tiled.from_uri = from_uri or _default_from_uri
    monkeypatch.setitem(sys.modules, "tiled.client", fake_tiled)

    fake_acquire = types.ModuleType("lightfall.acquire")
    fake_acquire.get_engine = lambda: engine
    monkeypatch.setitem(sys.modules, "lightfall.acquire", fake_acquire)

    def _ThreadedTiledWriter(raw, error_callback=None):
        calls["ThreadedTiledWriter"].append({"raw": raw, "error_callback": error_callback})
        return f"threaded({raw})"

    fake_threaded = types.ModuleType("lightfall.services.threaded_tiled_writer")
    fake_threaded.ThreadedTiledWriter = _ThreadedTiledWriter
    monkeypatch.setitem(
        sys.modules, "lightfall.services.threaded_tiled_writer", fake_threaded
    )

    return calls


def test_subscribes_writer_to_engine(monkeypatch):
    monkeypatch.setenv(tiled_writer._WRITE_API_KEY_ENV, "SVC-KEY")
    engine = _FakeEngine()
    calls = _install_fakes(monkeypatch, engine=engine)

    tiled_writer.wire_tiled_writer()

    # Built a write client with the service key, navigated to cms/raw.
    assert calls["from_uri"][0]["url"] == tiled_writer._TILED_URI
    assert calls["from_uri"][0]["api_key"] == "SVC-KEY"
    assert calls["from_uri"][0]["node"] == tiled_writer._WRITE_NODE
    # Wrapped a post_document writer (over the cms/raw client) in ThreadedTiledWriter.
    raw = calls["ThreadedTiledWriter"][0]["raw"]
    assert isinstance(raw, tiled_writer._PostDocumentWriter)
    assert raw._client == f"client:{tiled_writer._WRITE_NODE}"
    assert calls["ThreadedTiledWriter"][0]["error_callback"] is tiled_writer._on_writer_error
    # Subscribed the threaded writer to the engine and recorded the token.
    assert len(engine.subscribed) == 1
    assert tiled_writer._token is not None
    assert tiled_writer._writer == engine.subscribed[0]


def test_post_document_writer_posts_each_document():
    posted = []

    class _Client:
        def post_document(self, name, doc):
            posted.append((name, doc))

    writer = tiled_writer._PostDocumentWriter(_Client())
    writer("start", {"uid": "abc"})
    writer("stop", {"run_start": "abc"})

    assert posted == [("start", {"uid": "abc"}), ("stop", {"run_start": "abc"})]


def test_idempotent_when_already_subscribed(monkeypatch):
    monkeypatch.setenv(tiled_writer._WRITE_API_KEY_ENV, "SVC-KEY")
    engine = _FakeEngine()
    _install_fakes(monkeypatch, engine=engine)

    tiled_writer.wire_tiled_writer()
    tiled_writer.wire_tiled_writer()  # second call must NOT subscribe a duplicate

    assert len(engine.subscribed) == 1


def test_skips_without_api_key(monkeypatch):
    monkeypatch.delenv(tiled_writer._WRITE_API_KEY_ENV, raising=False)
    engine = _FakeEngine()
    _install_fakes(monkeypatch, engine=engine)

    tiled_writer.wire_tiled_writer()

    assert engine.subscribed == []
    assert tiled_writer._token is None


def test_best_effort_when_engine_missing(monkeypatch):
    monkeypatch.setenv(tiled_writer._WRITE_API_KEY_ENV, "SVC-KEY")
    _install_fakes(monkeypatch, engine=None)

    # No shared engine yet: must not raise, must not subscribe.
    tiled_writer.wire_tiled_writer()
    assert tiled_writer._token is None


def test_best_effort_and_rolls_back_when_from_uri_raises(monkeypatch):
    monkeypatch.setenv(tiled_writer._WRITE_API_KEY_ENV, "SVC-KEY")
    engine = _FakeEngine()

    def _boom(*a, **k):
        raise OSError("tiled unreachable")

    _install_fakes(monkeypatch, engine=engine, from_uri=_boom)

    # Connection failure must not abort startup; partial state rolled back.
    tiled_writer.wire_tiled_writer()
    assert engine.subscribed == []
    assert tiled_writer._writer is None
    assert tiled_writer._token is None
