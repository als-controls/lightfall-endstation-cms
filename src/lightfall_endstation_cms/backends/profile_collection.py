"""Lightfall DeviceBackend that loads devices from a CMS profile-collection.

This backend executes the NSLS-II CMS IPython startup scripts and extracts
ophyd device instances, making them available to Lightfall without needing a
separate happi JSON catalog.

The profile-collection is the single source of truth for device definitions.

Devices are populated by the ProfileSessionBootstrapper, which runs the FULL
profile in Lightfall's console kernel after login and then calls
``populate_from_namespace`` with the live namespace. ``connect()`` is a no-op
(see its docstring) — it must NOT do its own sandboxed load.

Usage:
    backend = ProfileCollectionBackend()
    backend.populate_from_namespace(shell.user_ns)  # devices from the live ns
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_DNS

from loguru import logger

from lightfall.devices.base import DeviceBackend
from lightfall.devices.model import (
    ConnectionType,
    DeviceCategory,
    DeviceConfiguration,
    DeviceInfo,
    DeviceStatus,
    MaintenanceRecord,
)


# Deterministic UUID namespace for profile-collection devices.
# uuid5(NAMESPACE, "cms-profile:smx") always produces the same UUID for "smx".
_CMS_UUID_NS = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _device_uuid(name: str) -> UUID:
    """Generate a deterministic UUID for a device name."""
    return uuid5(_CMS_UUID_NS, f"cms-profile:{name}")


def _categorize_device(obj: Any) -> DeviceCategory:
    """Categorize an ophyd device instance by inspecting its class hierarchy."""
    cls = type(obj)
    mro_names = {c.__name__ for c in cls.__mro__}

    if "EpicsMotor" in mro_names or "PositionerBase" in mro_names:
        return DeviceCategory.MOTOR
    if "DetectorBase" in mro_names or "EpicsMCA" in mro_names:
        return DeviceCategory.DETECTOR
    if "Signal" in mro_names or "EpicsSignal" in mro_names or "EpicsSignalRO" in mro_names:
        return DeviceCategory.DETECTOR

    # Check for motor-like names in the class hierarchy
    for name in mro_names:
        lower = name.lower()
        if "motor" in lower or "positioner" in lower or "slit" in lower:
            return DeviceCategory.MOTOR
        if "detector" in lower or "camera" in lower or "pilatus" in lower:
            return DeviceCategory.DETECTOR

    return DeviceCategory.CONTROLLER


def _get_prefix(obj: Any) -> str:
    """Extract the EPICS PV prefix from an ophyd device."""
    prefix = getattr(obj, "prefix", "") or ""
    if hasattr(prefix, "pvname"):  # ophyd PVName
        return str(prefix)
    return str(prefix)


def _get_device_class_path(obj: Any) -> str:
    """Get the fully-qualified class name of a device."""
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


class ProfileCollectionBackend(DeviceBackend):
    """Lightfall DeviceBackend backed by the CMS profile-collection scripts.

    Loads the NSLS-II CMS IPython startup scripts, extracts ophyd devices,
    and exposes them as DeviceInfo objects for Lightfall.

    This is a read-only backend -- devices are defined by the profile scripts.
    """

    def __init__(
        self,
        profile_path: str | Path | None = None,
        blacklist: set[str] | None = None,
        beamline: str = "11-BM CMS",
    ):
        self._profile_path = profile_path
        self._blacklist = blacklist
        self._beamline = beamline
        self._connected = False
        self._devices: dict[UUID, DeviceInfo] = {}
        self._name_index: dict[str, UUID] = {}
        self._prefix_index: dict[str, UUID] = {}
        self._ophyd_instances: dict[UUID, Any] = {}
        self._namespace: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "cms-profile-collection"

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_editable(self) -> bool:
        return False  # Profile collection is read-only

    def connect(self) -> bool:
        """No-op connect; devices come from the bootstrapper, not a sandboxed load.

        Returns True (the backend is "ready" to receive devices) WITHOUT running
        the sandboxed ``load_profile()``. That sandboxed load would instantiate
        ophyd ``EpicsSignalBase`` devices at plugin-load time (before login),
        and the bootstrapper's later full profile run would then fail at
        ``00-startup``'s ``EpicsSignalBase.set_defaults(...)`` — which "may only
        be called before the first instance is created". The
        ProfileSessionBootstrapper runs the full profile in the console kernel
        and calls :meth:`populate_from_namespace` to fill the catalog.
        """
        self._connected = True
        return True

    def populate_from_namespace(self, namespace: dict[str, Any]) -> int:
        """Build the device catalog from an already-populated namespace.

        Used when the CMS profile-collection has been executed in Lightfall's
        live IPython kernel (by the ProfileSessionBootstrapper) rather than in
        a sandboxed namespace. The full profile is the source of truth.

        Args:
            namespace: A namespace dict (e.g. the kernel's ``shell.user_ns``).

        Returns:
            Number of ophyd devices extracted and cataloged.
        """
        from lightfall_endstation_cms.loader import extract_ophyd_devices

        self._devices.clear()
        self._name_index.clear()
        self._prefix_index.clear()
        self._ophyd_instances.clear()
        self._namespace = namespace
        ophyd_devices = extract_ophyd_devices(namespace)
        self._build_device_catalog(ophyd_devices)
        self._connected = True
        logger.info(
            "CMS backend populated from live namespace: {} devices",
            len(self._devices),
        )
        return len(self._devices)

    def disconnect(self) -> None:
        self._devices.clear()
        self._name_index.clear()
        self._prefix_index.clear()
        self._ophyd_instances.clear()
        self._namespace.clear()
        self._connected = False

    def _build_device_catalog(self, ophyd_devices: dict[str, Any]) -> None:
        """Convert extracted ophyd devices into DeviceInfo objects."""
        for var_name, obj in ophyd_devices.items():
            device_id = _device_uuid(var_name)
            prefix = _get_prefix(obj)
            device_class = _get_device_class_path(obj)
            category = _categorize_device(obj)

            # Use ophyd's .name if available, fall back to variable name
            ophyd_name = getattr(obj, "name", var_name) or var_name

            info = DeviceInfo(
                id=device_id,
                name=var_name,
                display_name=ophyd_name,
                description=f"CMS profile-collection device: {var_name}",
                category=category,
                device_class=device_class,
                connection_type=ConnectionType.EPICS if prefix else ConnectionType.OTHER,
                prefix=prefix,
                beamline=self._beamline,
                active=True,
                tags=["cms", "profile-collection"],
                metadata={
                    "source": "profile-collection",
                    "ophyd_name": ophyd_name,
                    "var_name": var_name,
                },
            )
            # Attach the pre-instantiated ophyd device
            info._ophyd_device = obj

            self._devices[device_id] = info
            self._name_index[var_name] = device_id
            if prefix:
                self._prefix_index[prefix] = device_id
            self._ophyd_instances[device_id] = obj

    def get_ophyd_device(self, device_id: UUID) -> Any | None:
        """Get the pre-instantiated ophyd device for a given device ID.

        This is the key advantage of the profile-collection backend:
        devices are already instantiated by the profile scripts, so
        Lightfall can use them directly without happi's load_device() step.
        """
        return self._ophyd_instances.get(device_id)

    # === Device CRUD (read-only) ===

    def get_device(self, device_id: UUID) -> DeviceInfo | None:
        return self._devices.get(device_id)

    def get_device_by_name(self, name: str) -> DeviceInfo | None:
        uid = self._name_index.get(name)
        return self._devices.get(uid) if uid else None

    def get_device_by_prefix(self, prefix: str) -> DeviceInfo | None:
        uid = self._prefix_index.get(prefix)
        return self._devices.get(uid) if uid else None

    def list_devices(
        self,
        category: DeviceCategory | None = None,
        beamline: str | None = None,
        active_only: bool = True,
    ) -> list[DeviceInfo]:
        results = []
        for info in self._devices.values():
            if active_only and not info.active:
                continue
            if category and info.category != category:
                continue
            if beamline and info.beamline != beamline:
                continue
            results.append(info)
        return results

    def search_devices(self, query: str) -> list[DeviceInfo]:
        q = query.lower()
        return [
            info for info in self._devices.values()
            if q in info.name.lower()
            or q in info.display_name.lower()
            or q in info.prefix.lower()
            or q in info.device_class.lower()
            or q in info.description.lower()
        ]

    def add_device(self, device: DeviceInfo) -> bool:
        logger.warning("ProfileCollectionBackend is read-only")
        return False

    def update_device(self, device: DeviceInfo) -> bool:
        logger.warning("ProfileCollectionBackend is read-only")
        return False

    def remove_device(self, device_id: UUID) -> bool:
        logger.warning("ProfileCollectionBackend is read-only")
        return False

    # === Configuration (not supported for profile-collection) ===

    def get_device_configurations(self, device_id: UUID) -> list[DeviceConfiguration]:
        return []

    def get_configuration(self, device_id: UUID, config_name: str) -> DeviceConfiguration | None:
        return None

    def save_configuration(self, config: DeviceConfiguration) -> bool:
        return False

    def delete_configuration(self, config_id: UUID) -> bool:
        return False

    # === Maintenance (not supported) ===

    def get_maintenance_history(self, device_id: UUID, limit: int = 100) -> list[MaintenanceRecord]:
        return []

    def add_maintenance_record(self, record: MaintenanceRecord) -> bool:
        return False
