"""DeviceBackendPlugin for the CMS (11-BM) endstation device catalog.

Devices come from a happi JSON database shipped with this package
(``devices/cms_happi.json``); the device classes it references live in
``lightfall_endstation_cms.devices``.  Lightfall's :class:`HappiBackend`
instantiates the ophyd objects in the background, so the device tree is
populated at startup without running the CMS profile-collection.

Post-login, the CMS profile is hosted by the
:class:`~lightfall_endstation_cms.bootstrap.ProfileSessionBootstrapper`, armed on
the devices-live gate
(:class:`~lightfall_endstation_cms.session_trigger.CMSSessionTrigger`). Its INFRA
-- configure_base's redis ``RE.md``, kafka publisher, ``SupplementalData``, the
``assets_path`` hook, and the tiled writer -- is re-expressed onto Lightfall's
own ``RunEngine`` instead of running ``00``-``03``; then, once the happi devices
are injected into the kernel, the high-level SAM framework
(``81``/``94``/``95``/``96``/``97``/``991``) the console relies on is run. The
device-DEFINING scripts are never run; happi is the single source of truth for
devices (see ``bootstrap.DEFAULT_SAM_KEEP``).
"""
from __future__ import annotations

import os
from importlib.resources import as_file, files

from loguru import logger

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


def _bootstrap_timeout_s() -> float:
    """Seconds before the SAM bootstrap fires in degraded mode (env-tunable)."""
    try:
        return float(os.environ.get("CMS_BOOTSTRAP_TIMEOUT_S", "60"))
    except (TypeError, ValueError):
        return 60.0


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
        # SAM hosting (the profile/console framework the CMS panels veneer over)
        # is re-expressed as a devices-live post-login action: build the happi
        # backend, then arm the gate that runs ProfileSessionBootstrapper once
        # the catalog's ophyd objects are live. Background instantiation is
        # async, so the bootstrap cannot run synchronously here.
        backend = HappiBackend(
            path=_happi_db_path(),
            beamline=_BEAMLINE,
            instantiate="background",
        )
        self._arm_session_trigger(backend)
        return backend

    def _arm_session_trigger(self, backend: DeviceBackend) -> None:
        """Arm the devices-loaded gate that hosts the CMS profile (SAM phase).

        Background device instantiation is async, so the bootstrap -- which
        injects the live ophyd objects and runs the SAM framework -- cannot run
        at backend-creation time. CMSSessionTrigger waits for the
        DeviceConnectionManager's ``all_connections_complete`` signal (the
        "devices loaded" event), then fires ProfileSessionBootstrapper on the GUI
        thread (which ensures the kernel and injects). The trigger is held on the
        plugin instance so its deadline QTimer is not garbage-collected.
        Best-effort: a failure to arm must not fail backend creation (re-login
        retries).
        """
        try:
            from lightfall_endstation_cms.session_trigger import CMSSessionTrigger

            trigger = CMSSessionTrigger(backend)
            self._session_trigger = trigger
            trigger.arm(timeout_s=_bootstrap_timeout_s())
        except Exception:
            logger.exception(
                "Could not arm the CMS session trigger; SAM hosting will not "
                "start automatically (re-login to retry)"
            )
