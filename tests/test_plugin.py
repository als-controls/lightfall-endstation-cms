"""Tests for the CMS device-backend plugin wrapper."""
from __future__ import annotations

import json
from pathlib import Path

from lightfall.devices.backends.happi import HappiBackend
from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin

from lightfall_endstation_cms.plugin import CMSProfileCollectionPlugin, _happi_db_path


def test_is_device_backend_plugin():
    assert issubclass(CMSProfileCollectionPlugin, DeviceBackendPlugin)
    assert CMSProfileCollectionPlugin.type_name == "device_backend"


class _FakeTrigger:
    """No-op stand-in for CMSSessionTrigger (avoids a real QTimer in tests)."""

    def __init__(self, backend=None):
        self.backend = backend
        self.armed_with = None

    def arm(self, device_names, **kwargs):
        self.armed_with = (list(device_names), kwargs)


def test_create_backend_returns_happi_backend(monkeypatch):
    import lightfall_endstation_cms.session_trigger as st_mod

    monkeypatch.setattr(st_mod, "CMSSessionTrigger", _FakeTrigger)
    plugin = CMSProfileCollectionPlugin()
    assert plugin.name == "cms_profile_collection"
    backend = plugin.create_backend()
    assert isinstance(backend, HappiBackend)
    # Ordinary post-login happi backend: background instantiation lets the
    # DeviceConnectionManager construct the ophyd objects and set live status
    # (devices auto-initialize instead of staying UNKNOWN). No kernel injection,
    # and no pre-login set_defaults to sequence around (that workaround is gone).
    assert backend.path == _happi_db_path()
    assert backend._beamline == "CMS"
    assert backend._instantiate_mode == "background"
    # The pre-login-armed SAM bootstrap trigger is no longer attached here — it
    # armed too late under post-login loading (and would fire the bootstrap on a
    # re-login against already-instantiated devices). SAM hosting is re-expressed
    # as a catalog-driven post-login action instead.
    assert getattr(backend, "_session_trigger", None) is None
    # The trigger is held on the PLUGIN (not the backend) so its timer survives.
    assert isinstance(plugin._session_trigger, _FakeTrigger)


def test_create_backend_arms_devices_live_gate(monkeypatch):
    import lightfall_endstation_cms.session_trigger as st_mod

    monkeypatch.setattr(st_mod, "CMSSessionTrigger", _FakeTrigger)
    monkeypatch.setenv("CMS_BOOTSTRAP_WAIT_DEVICES", "smx, pilatus2M")

    plugin = CMSProfileCollectionPlugin()
    plugin.create_backend()

    trig = plugin._session_trigger
    assert isinstance(trig, _FakeTrigger)
    names, kwargs = trig.armed_with
    assert names == ["smx", "pilatus2M"]
    assert "timeout_s" in kwargs


def test_packaged_happi_db_is_valid_json_with_cms_devices():
    path = Path(_happi_db_path())
    assert path.is_file()
    db = json.loads(path.read_text())
    assert db, "happi DB is empty"
    # Every entry is a happi item tagged for CMS with an importable device_class.
    for key, item in db.items():
        if key.startswith("_"):  # _NOTES etc.
            continue
        assert item["name"], key
        assert item["device_class"], key
        assert item["beamline"] == "CMS", key


def test_manifest_entry_points_at_wrapper():
    from lightfall_endstation_cms.manifest import manifest
    entry = next(p for p in manifest.plugins if p.name == "cms_profile_collection")
    assert entry.type_name == "device_backend"
    assert entry.import_path.endswith("plugin:CMSProfileCollectionPlugin")
