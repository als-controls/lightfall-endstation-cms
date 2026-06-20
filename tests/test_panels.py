"""Tests for kernel access helpers and the CMS sample panel."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms import kernel_access


# --- kernel_access ------------------------------------------------------

class _Sample_Generic:  # noqa: N801 - mirrors the kernel class name for MRO match
    pass


class _MySample(_Sample_Generic):
    pass


def test_find_kernel_objects_matches_by_base_class(monkeypatch):
    ns = {
        "sam": _MySample(),
        "other_sample": _MySample(),
        "cms": object(),          # not a sample
        "_hidden": _MySample(),   # underscore -> skipped
    }
    monkeypatch.setattr(kernel_access, "get_kernel_namespace", lambda: ns)
    found = kernel_access.find_kernel_objects("_Sample_Generic")
    assert set(found) == {"sam", "other_sample"}


def test_sam_is_loaded_and_get_object(monkeypatch):
    monkeypatch.setattr(kernel_access, "get_kernel_namespace", lambda: {"cms": object()})
    assert kernel_access.sam_is_loaded() is True
    assert kernel_access.get_kernel_object("cms") is not None
    assert kernel_access.get_kernel_object("missing") is None

    monkeypatch.setattr(kernel_access, "get_kernel_namespace", lambda: {})
    assert kernel_access.sam_is_loaded() is False


def test_execute_in_console_runs_cell(monkeypatch):
    ran = []
    shell = SimpleNamespace(run_cell=lambda code, store_history=False: ran.append(code))
    monkeypatch.setattr(kernel_access, "get_kernel_shell", lambda: shell)
    assert kernel_access.execute_in_console("sam.snap()") is True
    assert ran == ["sam.snap()"]


def test_execute_in_console_no_kernel(monkeypatch):
    monkeypatch.setattr(kernel_access, "get_kernel_shell", lambda: None)
    assert kernel_access.execute_in_console("sam.snap()") is False


def test_accessors_degrade_without_app(monkeypatch):
    # No LFApplication instance -> everything None/empty, no raise.
    import lightfall.core as core

    monkeypatch.setattr(core.LFApplication, "get_instance", staticmethod(lambda: None))
    assert kernel_access.get_ipython_panel() is None
    assert kernel_access.get_kernel_shell() is None
    assert kernel_access.get_kernel_namespace() == {}


# --- plugin -------------------------------------------------------------

def test_panel_plugin_and_manifest():
    from lightfall.plugins.panel_plugin import PanelPlugin

    from lightfall_endstation_cms.panels import CMSSamplePanelPlugin

    plugin = CMSSamplePanelPlugin()
    assert isinstance(plugin, PanelPlugin)
    assert plugin.name == "cms_sample"
    assert plugin.type_name == "panel"

    from lightfall_endstation_cms.manifest import manifest

    entry = next(p for p in manifest.plugins if p.name == "cms_sample")
    assert entry.type_name == "panel"
    assert entry.import_path.endswith("panels:CMSSamplePanelPlugin")
    assert entry.preload is True


# --- panel (needs a QApplication) ---------------------------------------

def test_panels_do_not_clobber_basepanel_status(qapp, monkeypatch):
    """Regression: a panel's status QLabel must not shadow BasePanel.status.

    BasePanel sets self._status = PanelStatus and the docking manager reads
    panel.status; a panel that reassigned self._status to a QLabel caused a
    KeyError in the sidebar status coloring. Every CMS panel's .status must
    remain a PanelStatus.
    """
    from lightfall.ui.panels.base import PanelStatus

    monkeypatch.setattr(kernel_access, "sam_is_loaded", lambda: False)
    monkeypatch.setattr(kernel_access, "find_kernel_objects", lambda *a: {})
    monkeypatch.setattr(kernel_access, "get_kernel_object", lambda name: None)

    from lightfall_endstation_cms.panels.beamline_panel import CMSBeamlinePanel
    from lightfall_endstation_cms.panels.holder_panel import CMSHolderPanel
    from lightfall_endstation_cms.panels.sample_panel import CMSSamplePanel

    for cls in (CMSSamplePanel, CMSHolderPanel, CMSBeamlinePanel):
        panel = cls()
        assert isinstance(panel.status, PanelStatus), f"{cls.__name__}.status clobbered"


def test_panel_not_loaded_state(qapp, monkeypatch):
    monkeypatch.setattr(kernel_access, "sam_is_loaded", lambda: False)
    monkeypatch.setattr(kernel_access, "find_kernel_objects", lambda *a: {})

    from lightfall_endstation_cms.panels.sample_panel import CMSSamplePanel

    panel = CMSSamplePanel()
    assert "not loaded" in panel._status_label.text().lower()
    assert panel._samples.count() == 0
    assert panel._snap_btn.isEnabled() is False


def test_panel_lists_samples_and_enables_actions(qapp, monkeypatch):
    monkeypatch.setattr(kernel_access, "sam_is_loaded", lambda: True)
    monkeypatch.setattr(
        kernel_access, "find_kernel_objects",
        lambda *a: {"sam": _MySample(), "sam2": _MySample()},
    )
    ran = []
    monkeypatch.setattr(kernel_access, "execute_in_console", lambda code: ran.append(code) or True)

    from lightfall_endstation_cms.panels.sample_panel import CMSSamplePanel

    panel = CMSSamplePanel()
    assert panel._samples.count() == 2
    assert "2 sample" in panel._status_label.text()

    panel._samples.setCurrentRow(0)  # select "sam"
    assert panel._snap_btn.isEnabled() is True
    panel._exposure.setValue(2.5)
    panel._on_measure()
    assert ran == ["sam.measure(2.5)"]
    panel._on_snap()
    assert ran[-1] == "sam.snap()"


def test_action_buttons_disabled_while_command_in_flight(qapp, monkeypatch):
    monkeypatch.setattr(kernel_access, "sam_is_loaded", lambda: True)
    monkeypatch.setattr(kernel_access, "find_kernel_objects", lambda *a: {"sam": _MySample()})

    from lightfall_endstation_cms.panels.sample_panel import CMSSamplePanel

    panel = CMSSamplePanel()
    panel._samples.setCurrentRow(0)
    enabled_during = {}

    def fake_exec(code):
        # The guard must have disabled the action buttons before dispatch.
        enabled_during["snap"] = panel._snap_btn.isEnabled()
        enabled_during["measure"] = panel._measure_btn.isEnabled()
        return True

    monkeypatch.setattr(kernel_access, "execute_in_console", fake_exec)
    panel._on_snap()

    assert enabled_during == {"snap": False, "measure": False}
    # After the command returns, refresh() restores the correct enabled state.
    assert panel._snap_btn.isEnabled() is True


# --- holder panel -------------------------------------------------------

class _Holder:
    pass


class _CapillaryHolder(_Holder):  # MRO includes "_Holder"
    def __init__(self, samples):
        self._samples = samples


def test_holder_plugin_and_manifest():
    from lightfall_endstation_cms.panels import CMSHolderPanelPlugin

    assert CMSHolderPanelPlugin().name == "cms_holder"
    from lightfall_endstation_cms.manifest import manifest

    entry = next(p for p in manifest.plugins if p.name == "cms_holder")
    assert entry.type_name == "panel"
    assert entry.import_path.endswith("panels:CMSHolderPanelPlugin")


def test_holder_panel_maps_slots_and_navigates(qapp, monkeypatch):
    holder = _CapillaryHolder({1: SimpleNamespace(name="s1"), 3: SimpleNamespace(name="s3")})
    monkeypatch.setattr(kernel_access, "sam_is_loaded", lambda: True)
    # Match the holder panel's base-class filter.
    monkeypatch.setattr(
        kernel_access, "find_kernel_objects",
        lambda *bases: {"hol": holder} if "_Holder" in bases else {},
    )
    ran = []
    monkeypatch.setattr(kernel_access, "execute_in_console", lambda code: ran.append(code) or True)

    from lightfall_endstation_cms.panels import holder_panel
    monkeypatch.setattr(holder_panel, "_HOLDER_BASES", ("_Holder",))

    panel = holder_panel.CMSHolderPanel()
    assert panel._holder_combo.currentText() == "hol"
    assert [panel._slots.item(i).text() for i in range(panel._slots.count())] == ["1: s1", "3: s3"]

    panel._slots.setCurrentRow(1)  # slot 3
    assert panel._goto_btn.isEnabled() is True
    panel._on_goto()
    assert ran == ["hol.gotoSample(3)"]


# --- beamline panel -----------------------------------------------------

def test_beamline_plugin_and_manifest():
    from lightfall_endstation_cms.panels import CMSBeamlinePanelPlugin

    assert CMSBeamlinePanelPlugin().name == "cms_beamline"
    from lightfall_endstation_cms.manifest import manifest

    entry = next(p for p in manifest.plugins if p.name == "cms_beamline")
    assert entry.type_name == "panel"
    assert entry.import_path.endswith("panels:CMSBeamlinePanelPlugin")


def test_beamline_panel_not_loaded(qapp, monkeypatch):
    monkeypatch.setattr(kernel_access, "sam_is_loaded", lambda: False)

    from lightfall_endstation_cms.panels.beamline_panel import CMSBeamlinePanel

    panel = CMSBeamlinePanel()
    assert "not loaded" in panel._status_label.text().lower()
    assert panel._open_btn.isEnabled() is False
    assert panel._beam_value.text() == "—"


def test_beamline_panel_reads_state(qapp, monkeypatch):
    objs = {
        "beam": SimpleNamespace(is_on=lambda verbosity=0: 1),
        "cms": SimpleNamespace(current_mode="measurement"),
    }
    monkeypatch.setattr(kernel_access, "sam_is_loaded", lambda: True)
    monkeypatch.setattr(kernel_access, "get_kernel_object", lambda name: objs.get(name))

    from lightfall_endstation_cms.panels.beamline_panel import CMSBeamlinePanel

    panel = CMSBeamlinePanel()
    assert panel._beam_value.text() == "OPEN"
    assert panel._mode_value.text() == "measurement"
    assert panel._open_btn.isEnabled() is True


def test_beamline_actions_require_confirmation(qapp, monkeypatch):
    objs = {
        "beam": SimpleNamespace(is_on=lambda verbosity=0: 0),
        "cms": SimpleNamespace(current_mode="undefined"),
    }
    monkeypatch.setattr(kernel_access, "sam_is_loaded", lambda: True)
    monkeypatch.setattr(kernel_access, "get_kernel_object", lambda name: objs.get(name))
    ran = []
    monkeypatch.setattr(kernel_access, "execute_in_console", lambda code: ran.append(code) or True)

    from lightfall_endstation_cms.panels.beamline_panel import CMSBeamlinePanel

    panel = CMSBeamlinePanel()

    # Declining the confirmation must NOT actuate.
    monkeypatch.setattr(panel, "_confirm", lambda action: False)
    panel._on_open()
    panel._on_alignment()
    assert ran == []

    # Confirming opens the shutter / switches mode.
    monkeypatch.setattr(panel, "_confirm", lambda action: True)
    panel._on_open()
    panel._on_alignment()
    assert ran == ["beam.on()", "cms.modeAlignment()"]

    # Closing the beam is the safe direction — no confirmation needed.
    panel._on_close()
    assert ran[-1] == "beam.off()"
