"""Tests for the CMS device-backend plugin wrapper."""
from __future__ import annotations

import json
from pathlib import Path

from lightfall.devices.backends.happi import HappiBackend
from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin

from lightfall_endstation_cms.plugin import _happi_db_path, CMSProfileCollectionPlugin


def test_is_device_backend_plugin():
    assert issubclass(CMSProfileCollectionPlugin, DeviceBackendPlugin)
    assert CMSProfileCollectionPlugin.type_name == "device_backend"


def test_create_backend_returns_happi_backend():
    plugin = CMSProfileCollectionPlugin()
    assert plugin.name == "cms_profile_collection"
    backend = plugin.create_backend()
    assert isinstance(backend, HappiBackend)
    # Backend is pointed at the packaged CMS happi DB, scoped to CMS, and
    # instantiates devices in the background (no blocking EPICS on connect()).
    assert backend.path == _happi_db_path()
    assert backend._beamline == "CMS"
    assert backend._instantiate_mode == "background"


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
