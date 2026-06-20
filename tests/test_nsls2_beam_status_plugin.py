"""Display/behavior tests for NSLS2BeamStatusPlugin (fake service)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.services.nsls2_beam_status import NSLS2BeamData
from lightfall_endstation_cms.statusbar import nsls2_beam_status as mod
from lightfall_endstation_cms.statusbar.nsls2_beam_status import NSLS2BeamStatusPlugin


class _FakeService(QObject):
    status_changed = Signal(object)
    connection_changed = Signal(bool)

    def __init__(self, *, connected=True, data=None):
        super().__init__()
        self._connected = connected
        self._data = data
        self.started = False
        self.last_error = None

    @property
    def is_running(self):
        return self.started

    def start(self):
        self.started = True

    @property
    def is_connected(self):
        return self._connected

    @property
    def current_data(self):
        return self._data

    def get_introspection_data(self):
        return {"is_connected": self._connected}


def _install(monkeypatch, fake):
    monkeypatch.setattr(
        mod.NSLS2BeamStatusService, "get_instance", classmethod(lambda cls: fake)
    )


def _make(qtbot, fake):
    plugin = NSLS2BeamStatusPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    return plugin


def test_metadata_and_name():
    assert NSLS2BeamStatusPlugin.metadata.id == "lightfall.statusbar.nsls2_beam"
    assert NSLS2BeamStatusPlugin().name == "nsls2_beam_status"


def test_shows_current_and_lifetime_when_available(qapp, qtbot, monkeypatch):
    data = NSLS2BeamData(beam_current=401.0, lifetime=12.5, beam_available=True)
    fake = _FakeService(connected=True, data=data)
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)
    plugin.update()
    assert "401 mA" in plugin._button.text()
    assert "12.5h" in plugin._button.text()
    assert fake.started is True  # lazy-started


def test_offline_when_disconnected(qapp, qtbot, monkeypatch):
    fake = _FakeService(connected=False, data=None)
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)
    plugin.update()
    assert plugin._button.text() == "Offline"


def test_toast_on_availability_change(qapp, qtbot, monkeypatch):
    class _Toast:
        def __init__(self):
            self.calls = []

        def success(self, *a, **k):
            self.calls.append(("success", a))

        def warning(self, *a, **k):
            self.calls.append(("warning", a))

    toast = _Toast()
    monkeypatch.setattr(
        "lightfall.ui.toast.ToastManager.get_instance", classmethod(lambda cls: toast)
    )
    data_open = NSLS2BeamData(beam_current=401.0, lifetime=12.5, beam_available=True)
    fake = _FakeService(connected=True, data=data_open)
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)
    plugin.update()  # first paint: establishes baseline, no toast
    assert toast.calls == []
    fake._data = NSLS2BeamData(beam_current=0.0, lifetime=0.0, beam_available=False)
    plugin.update()  # transition available -> unavailable
    assert toast.calls and toast.calls[-1][0] == "warning"


def test_click_opens_status_page(qapp, qtbot, monkeypatch):
    opened = {}
    monkeypatch.setattr(
        "PySide6.QtGui.QDesktopServices.openUrl",
        lambda url: opened.setdefault("url", url.toString()) or True,
    )
    fake = _FakeService()
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)
    plugin.on_clicked()
    assert opened["url"] == "https://www.bnl.gov/nsls2/operating-status.php"


def test_toast_on_beam_restored(qapp, qtbot, monkeypatch):
    """Reverse transition: unavailable -> available fires a success toast."""

    class _Toast:
        def __init__(self):
            self.calls = []

        def success(self, *a, **k):
            self.calls.append(("success", a))

        def warning(self, *a, **k):
            self.calls.append(("warning", a))

    toast = _Toast()
    monkeypatch.setattr(
        "lightfall.ui.toast.ToastManager.get_instance", classmethod(lambda cls: toast)
    )
    data_unavailable = NSLS2BeamData(beam_current=0.0, lifetime=0.0, beam_available=False)
    fake = _FakeService(connected=True, data=data_unavailable)
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)
    plugin.update()  # first paint: establishes baseline (unavailable), no toast
    assert toast.calls == []
    fake._data = NSLS2BeamData(beam_current=401.0, lifetime=12.5, beam_available=True)
    plugin.update()  # transition unavailable -> available
    assert toast.calls and toast.calls[-1][0] == "success"


def test_connect_then_disconnect_signals(qapp, qtbot, monkeypatch):
    """connect_signals then disconnect_signals does not raise; slots are severed."""
    from unittest.mock import MagicMock
    from PySide6.QtCore import QObject as _QObject
    from PySide6.QtCore import Signal as _Signal

    class _FakeThemeManager(_QObject):
        colors_changed = _Signal()

        def __init__(self):
            super().__init__()
            self.colors = MagicMock()

    fake_theme = _FakeThemeManager()
    monkeypatch.setattr(
        "lightfall.ui.theme.ThemeManager.get_instance",
        classmethod(lambda cls: fake_theme),
    )

    data = NSLS2BeamData(beam_current=350.0, lifetime=8.0, beam_available=True)
    fake = _FakeService(connected=True, data=data)
    _install(monkeypatch, fake)
    plugin = _make(qtbot, fake)

    # Establish known button text via update before touching signals
    plugin.update()
    text_after_update = plugin._button.text()

    # connect then disconnect must not raise
    plugin.connect_signals()
    plugin.disconnect_signals()

    # Capture text right after disconnect
    text_before_emit = plugin._button.text()

    # Emit status_changed with data that would produce a *different* text
    different_data = NSLS2BeamData(beam_current=999.0, lifetime=99.9, beam_available=True)
    fake.status_changed.emit(different_data)

    # Slot was disconnected, so button text must be unchanged
    assert plugin._button.text() == text_before_emit
