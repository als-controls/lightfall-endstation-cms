"""DeviceBackendPlugin for the CMS (11-BM) endstation device catalog.

Devices come from a happi JSON database shipped with this package
(``devices/cms_happi.json``); the device classes it references live in
``lightfall_endstation_cms.devices``.  Lightfall's :class:`HappiBackend`
instantiates the ophyd objects in the background, so the device tree is
populated at startup without running the CMS profile-collection.

The profile-collection is still executed post-login by the
:class:`ProfileSessionBootstrapper`, in two phases: the infrastructure scripts
(``00``–``03``) to adopt the live ``RunEngine`` + Tiled client, then — after the
happi devices are injected into the kernel — the high-level SAM framework
(``81``/``94``/``95``/``96``/``97``/``991``) the console relies on. The
device-DEFINING scripts are never run; happi is the single source of truth for
devices (see ``bootstrap.DEFAULT_INFRA_KEEP`` / ``DEFAULT_SAM_KEEP``).
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
        # Ordinary post-login happi backend: instantiate="background" lets the
        # DeviceConnectionManager construct the ophyd objects and report live
        # status, so devices auto-initialize instead of staying UNKNOWN. Under
        # lightfall's post-login plugin loading this whole backend loads after
        # authentication, so there is no pre-login EpicsSignalBase creation to
        # sequence around — the old instantiate="none" + kernel-injection
        # workaround (and 00-startup's set_defaults) is gone.
        #
        # NOTE: SAM hosting (the profile/console framework the CMS panels veneer
        # over) is being re-expressed as a catalog-driven post-login action;
        # the old AUTHENTICATED-armed CMSSessionTrigger is intentionally NOT
        # armed here (it armed too late under post-login loading and would fire
        # the bootstrap on a re-login against already-instantiated devices).
        return HappiBackend(
            path=_happi_db_path(),
            beamline=_BEAMLINE,
            instantiate="background",
        )
