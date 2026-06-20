"""DeviceBackendPlugin for the CMS (11-BM) endstation device catalog.

Devices come from a happi JSON database shipped with this package
(``devices/cms_happi.json``); the device classes it references live in
``lightfall_endstation_cms.devices``.  Lightfall's :class:`HappiBackend`
instantiates the ophyd objects in the background, so the device tree is
populated at startup without running the CMS profile-collection.

The profile-collection is still executed once, post-login, by the
:class:`ProfileSessionBootstrapper` — but only its infrastructure scripts
(``00``–``03``), purely to adopt the live ``RunEngine`` and the write-scoped
Tiled client.  Device-defining scripts are no longer run (see
``bootstrap.DEFAULT_PROFILE_KEEP``); happi is the single source of truth for
devices.
"""
from __future__ import annotations

from importlib.resources import as_file, files

from lightfall.devices.backends.happi import HappiBackend
from lightfall.devices.base import DeviceBackend
from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin

# happi entries tag every CMS device with this beamline; passing it through
# keeps DeviceInfo.beamline consistent and scopes the catalog.
_BEAMLINE = "CMS"


def _happi_db_path() -> str:
    """Filesystem path to the packaged CMS happi database.

    ``as_file`` materialises the resource to a real path; for a normal
    (unzipped) install it is the file in site-packages. JSONBackend needs a
    filesystem path, not a resource object.
    """
    resource = files("lightfall_endstation_cms.devices").joinpath("cms_happi.json")
    with as_file(resource) as path:
        return str(path)


class CMSProfileCollectionPlugin(DeviceBackendPlugin):
    """Contributes the CMS (11-BM) device backend (happi-backed)."""

    @property
    def name(self) -> str:
        # Registry name kept stable for backwards compatibility with any
        # persisted backend selection, even though the backend is now happi.
        return "cms_profile_collection"

    def create_backend(self) -> DeviceBackend:
        backend = HappiBackend(
            path=_happi_db_path(),
            beamline=_BEAMLINE,
            instantiate="background",
        )

        # Arm the one-shot RE + Tiled adoption that runs the profile's
        # infrastructure scripts in the live kernel after NSLS-II login.
        from lightfall_endstation_cms.session_trigger import CMSSessionTrigger

        trigger = CMSSessionTrigger()
        backend._session_trigger = trigger  # keep a reference alive
        trigger.arm()
        return backend
