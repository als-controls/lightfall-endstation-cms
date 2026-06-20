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


def test_happi_db_loads_every_entry_through_backend():
    """Regression: the packaged DB must actually load through HappiBackend.

    happi rejects any document lacking ``type`` or whose name is not a valid
    Python identifier, and a non-dict top-level key (e.g. a `_NOTES` list) fails
    the whole client. Loading metadata does not import device_class, so this
    runs without ophyd/nslsii.
    """
    pytest.importorskip("happi")
    from lightfall.devices.backends.happi import HappiBackend

    backend = HappiBackend(path=str(_HAPPI_JSON), beamline="CMS", instantiate="none")
    assert backend.connect() is True

    loaded = {d.name for d in backend.list_devices(active_only=False)}
    expected = set(_happi_items())  # JSON keys == profile var names
    # Every entry loads (none skipped as malformed) ...
    assert loaded == expected, f"missing: {sorted(expected - loaded)}"
    # ... and the happi item name is the profile var name, so kernel injection
    # (ns[name] = ophyd_instance) binds devices under the names the profile uses.
    assert "pilatus2M" in loaded and "fs1" in loaded


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


def test_slit_config_resolves_against_profile_startup(tmp_path, monkeypatch):
    """A slit's relative ``config_file`` resolves against the profile-collection
    startup dir (so Lightfall shares the beamline's saved positions), and an
    existing preset file is read."""
    pytest.importorskip("ophyd")
    from lightfall_endstation_cms.devices import motors

    monkeypatch.setattr(motors, "preset_base", None)
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: tmp_path
    )
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "s2_config.cfg").write_text(json.dumps({"open": [{"xc": 1.0}]}))

    s2 = motors.MotorCenterAndGap(
        "XF:11BMB-OP{Slt:2", name="s2", config_file="cfg/s2_config.cfg"
    )

    assert s2._resolved_config_path() == tmp_path / "cfg" / "s2_config.cfg"
    # The preset file was actually read at construction.
    assert s2._positions == {"open": [{"xc": 1.0}]}


def test_slit_config_absolute_path_is_untouched(tmp_path, monkeypatch):
    pytest.importorskip("ophyd")
    from lightfall_endstation_cms.devices import motors

    monkeypatch.setattr(motors, "preset_base", None)
    abs_cfg = tmp_path / "abs_s1.cfg"
    s1 = motors.MotorCenterAndGap(
        "XF:11BMB-OP{Slt:1", name="s1", config_file=str(abs_cfg)
    )
    assert s1._resolved_config_path() == abs_cfg


def test_preset_base_override(tmp_path, monkeypatch):
    pytest.importorskip("ophyd")
    from lightfall_endstation_cms.devices import motors

    monkeypatch.setattr(motors, "preset_base", tmp_path)
    s3 = motors.MotorCenterAndGap(
        "XF:11BMB-OP{Slt:3", name="s3", config_file="cfg/s3_config.cfg"
    )
    assert s3._resolved_config_path() == tmp_path / "cfg" / "s3_config.cfg"


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
