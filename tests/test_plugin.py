"""Tests for the CMS device-backend plugin wrapper."""
from __future__ import annotations

from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin

from lightfall_endstation_cms.backends.profile_collection import ProfileCollectionBackend
from lightfall_endstation_cms.plugin import CMSProfileCollectionPlugin


def test_is_device_backend_plugin():
    assert issubclass(CMSProfileCollectionPlugin, DeviceBackendPlugin)
    assert CMSProfileCollectionPlugin.type_name == "device_backend"


def test_create_backend_returns_profile_collection_backend():
    plugin = CMSProfileCollectionPlugin()
    assert plugin.name == "cms_profile_collection"
    assert isinstance(plugin.create_backend(), ProfileCollectionBackend)


def test_manifest_entry_points_at_wrapper():
    from lightfall_endstation_cms.manifest import manifest
    entry = next(p for p in manifest.plugins if p.name == "cms_profile_collection")
    assert entry.type_name == "device_backend"
    assert entry.import_path.endswith("plugin:CMSProfileCollectionPlugin")
