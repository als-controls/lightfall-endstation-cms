"""DeviceBackendPlugin wrapper for the CMS profile-collection backend."""
from __future__ import annotations

from lightfall.devices.base import DeviceBackend
from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin

from lightfall_endstation_cms.backends.profile_collection import ProfileCollectionBackend


class CMSProfileCollectionPlugin(DeviceBackendPlugin):
    """Contributes the CMS (11-BM) profile-collection device backend."""

    @property
    def name(self) -> str:
        return "cms_profile_collection"

    def create_backend(self) -> DeviceBackend:
        backend = ProfileCollectionBackend()
        from lightfall_endstation_cms.session_trigger import CMSSessionTrigger

        trigger = CMSSessionTrigger(backend)
        backend._session_trigger = trigger
        trigger.arm()
        return backend
