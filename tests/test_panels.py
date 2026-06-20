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

def test_panel_not_loaded_state(qapp, monkeypatch):
    monkeypatch.setattr(kernel_access, "sam_is_loaded", lambda: False)
    monkeypatch.setattr(kernel_access, "find_kernel_objects", lambda *a: {})

    from lightfall_endstation_cms.panels.sample_panel import CMSSamplePanel

    panel = CMSSamplePanel()
    assert "not loaded" in panel._status.text().lower()
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
    assert "2 sample" in panel._status.text()

    panel._samples.setCurrentRow(0)  # select "sam"
    assert panel._snap_btn.isEnabled() is True
    panel._exposure.setValue(2.5)
    panel._on_measure()
    assert ran == ["sam.measure(2.5)"]
    panel._on_snap()
    assert ran[-1] == "sam.snap()"
