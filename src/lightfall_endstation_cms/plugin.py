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
        # instantiate="none": load device METADATA only at startup, do NOT
        # construct ophyd objects yet. Background instantiation would create
        # EpicsSignalBase instances before login, which makes 00-startup's
        # ``EpicsSignalBase.set_defaults(timeout=120)`` raise ("called too late")
        # and abort the rest of 00 (assets_path, beamline_stage, …). The ophyd
        # objects are instead constructed by the bootstrap's device-injection
        # step, which runs AFTER 00-startup's set_defaults (see bootstrap.py).
        backend = HappiBackend(
            path=_happi_db_path(),
            beamline=_BEAMLINE,
            instantiate="none",
        )

        # Arm the one-shot post-login bootstrap: it runs the profile infra
        # (RE + Tiled), injects this backend's happi devices into the kernel,
        # and runs the SAM framework. The backend is handed over so its ophyd
        # instances can be injected under their profile variable names.
        from lightfall_endstation_cms.session_trigger import CMSSessionTrigger

        trigger = CMSSessionTrigger(backend)
        backend._session_trigger = trigger  # keep a reference alive
        trigger.arm()
        return backend
