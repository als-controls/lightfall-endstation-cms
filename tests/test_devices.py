"""Tests for the shipped CMS device package and happi database."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_DEVICES_DIR = Path(__file__).parent.parent / "src" / "lightfall_endstation_cms" / "devices"
_HAPPI_JSON = _DEVICES_DIR / "cms_happi.json"


def _happi_items() -> dict:
    db = json.loads(_HAPPI_JSON.read_text())
    return {k: v for k, v in db.items() if not k.startswith("_")}


def test_custom_device_class_modules_exist():
    """Every ``lightfall_endstation_cms.devices.<mod>.<Cls>`` referenced by the
    happi DB must point at a sub-module file that ships in the package. (Static
    check — does not import, so it runs without ophyd/nslsii.)"""
    prefix = "lightfall_endstation_cms.devices."
    for key, item in _happi_items().items():
        dotted = item["device_class"]
        if not dotted.startswith(prefix):
            continue  # stock ophyd class (ophyd.EpicsMotor etc.)
        module_path, _, _cls = dotted.rpartition(".")
        submodule = module_path[len(prefix):]
        assert (_DEVICES_DIR / f"{submodule}.py").is_file(), (
            f"{key}: device_class {dotted!r} -> missing module {submodule}.py"
        )


def test_lazy_init_does_not_eagerly_import_submodules():
    """Importing the package must NOT import the nslsii-dependent sub-modules;
    accessing a pure-ophyd name imports only that sub-module (PEP 562)."""
    pytest.importorskip("ophyd")  # motors needs ophyd

    # Drop any pre-imported device modules so the assertion is meaningful.
    for name in list(sys.modules):
        if name.startswith("lightfall_endstation_cms.devices"):
            del sys.modules[name]

    import lightfall_endstation_cms.devices as devices

    assert "lightfall_endstation_cms.devices.area_detectors" not in sys.modules
    assert "lightfall_endstation_cms.devices.xspress3" not in sys.modules

    # Accessing a pure-ophyd class works and imports only its sub-module.
    assert devices.Blades is not None
    assert "lightfall_endstation_cms.devices.motors" in sys.modules
    # Still no eager pull-in of the nslsii-dependent ones.
    assert "lightfall_endstation_cms.devices.area_detectors" not in sys.modules

    with pytest.raises(AttributeError):
        _ = devices.NoSuchDevice


def test_all_happi_device_classes_import_and_resolve():
    """Each device_class resolves to a real class. Requires ophyd + nslsii
    (the [beamline] extra), so skips where they are absent (e.g. CI/dev)."""
    pytest.importorskip("ophyd")
    pytest.importorskip("nslsii")

    import importlib

    for key, item in _happi_items().items():
        dotted = item["device_class"]
        module_path, _, cls_name = dotted.rpartition(".")
        module = importlib.import_module(module_path)
        assert isinstance(getattr(module, cls_name), type), f"{key}: {dotted} not a class"
