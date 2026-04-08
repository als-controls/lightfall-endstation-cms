"""Profile collection loader for CMS beamline.

Executes the CMS profile-collection IPython startup scripts in order,
extracting device instances and class definitions from the resulting namespace.

The profile-collection is expected as a git submodule at ./profile-collection/
or at a path specified by the CMS_PROFILE_PATH environment variable.

Configuration is done via environment variables:

    CMS_PROFILE_PATH        Path to the profile-collection startup/ directory
    CMS_BEAMLINE_STAGE      One of: default, open_MAXS, BigHuber (default: default)
    CMS_CAMERA_ON           Enable Prosilica cameras (default: 1)
    CMS_PILATUS300_ON       Enable Pilatus 300K (default: 0)
    CMS_PILATUS800_ON       Enable Pilatus 800K (default: 1)
    CMS_PILATUS800_2_ON     Enable Pilatus 800K #2 (default: 0)
    CMS_PILATUS2M_ON        Enable Pilatus 2M (default: 1)
    CMS_PROFILE_BLACKLIST   Comma-separated file prefixes to skip (e.g. "00,02,90")
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

# Default blacklist: skip infrastructure, bluesky framework, and user files
# that have side effects (Tiled, Kafka, Redis, RunEngine, etc.)
DEFAULT_BLACKLIST = {
    "00",  # startup: Tiled, Kafka, Redis, nslsii.configure_base
    "01",  # AD v33 compatibility shim (imports from 00 namespace)
    "02",  # tiled-writer
    "03",  # async setup
    "55",  # archiver
    "85",  # suitcase-specfile
    "86",  # live-spec
    "90",  # bluesky RunEngine, detselect, config
    "91",  # fit_scan (needs RunEngine)
    "92",  # IPython magics
    "93",  # supplemental-data (needs RunEngine)
    "94",  # sample system (huge, needs beamline object)
    "95",  # sample-custom (needs 94)
    "96",  # automation (needs sample system)
    "97",  # user (needs everything)
    "98",  # databroker-browser
    "99",  # caproto-test
}

# Files that define devices -- these are the ones we want
DEVICE_FILES = {
    "10",  # motors
    "15",  # optics utilities (no devices, but defines helpers used by others)
    "19",  # experiment shutter
    "20",  # area detectors
    "25",  # scalers
    "26",  # ion chamber
    "27",  # Xspress3
    "30",  # beam monitors (if it exists)
    "41",  # endstation serial devices
    "42",  # diodebox
    "43",  # endstation ioLogik
    "44",  # laser PTA
    "50",  # bluesky devices
    "51",  # linkam stages
    "52",  # ocean optics
}


def _env_bool(key: str, default: bool = False) -> bool:
    """Read a boolean from an environment variable."""
    val = os.environ.get(key, "")
    if not val:
        return default
    return val.lower() in ("1", "true", "yes", "on")


def _get_profile_path() -> Path:
    """Resolve the profile-collection startup directory."""
    env_path = os.environ.get("CMS_PROFILE_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_dir():
            return p
        raise FileNotFoundError(f"CMS_PROFILE_PATH={env_path} does not exist")

    # Default: submodule relative to this package
    pkg_dir = Path(__file__).parent.parent.parent.parent  # up to repo root
    submodule = pkg_dir / "profile-collection" / "startup"
    if submodule.is_dir():
        return submodule

    raise FileNotFoundError(
        "Could not find CMS profile-collection. Set CMS_PROFILE_PATH or "
        "ensure the git submodule is initialized."
    )


def _get_blacklist() -> set[str]:
    """Get the set of file prefixes to skip."""
    env_list = os.environ.get("CMS_PROFILE_BLACKLIST", "")
    if env_list:
        return {s.strip() for s in env_list.split(",") if s.strip()}
    return DEFAULT_BLACKLIST.copy()


def _build_seed_namespace() -> dict[str, Any]:
    """Build the initial namespace that profile scripts expect.

    The profile scripts assume they're running inside IPython with
    nslsii already configured. We provide the minimum viable namespace
    so the device-defining files can execute without errors.
    """
    import numpy as np

    ns: dict[str, Any] = {
        "__builtins__": __builtins__,
        "np": np,
    }

    # Inject the beamline_stage variable that 10-motors.py reads
    ns["beamline_stage"] = os.environ.get("CMS_BEAMLINE_STAGE", "default")

    # Inject detector enable flags that 20-area-detectors.py reads
    ns["Camera_on"] = _env_bool("CMS_CAMERA_ON", default=True)
    ns["Pilatus300_on"] = _env_bool("CMS_PILATUS300_ON", default=False)
    ns["Pilatus800_on"] = _env_bool("CMS_PILATUS800_ON", default=True)
    ns["Pilatus800_2_on"] = _env_bool("CMS_PILATUS800_2_ON", default=False)
    ns["Pilatus2M_on"] = _env_bool("CMS_PILATUS2M_ON", default=True)

    return ns


def load_profile(
    profile_path: Path | str | None = None,
    blacklist: set[str] | None = None,
    whitelist: set[str] | None = None,
) -> dict[str, Any]:
    """Execute CMS profile-collection scripts and return the namespace.

    Args:
        profile_path: Path to the startup/ directory. If None, auto-detected.
        blacklist: File prefixes to skip. If None, uses DEFAULT_BLACKLIST.
        whitelist: If set, ONLY load files matching these prefixes (overrides blacklist).

    Returns:
        The namespace dict containing all defined names (devices, classes, etc.)
    """
    startup_dir = Path(profile_path) if profile_path else _get_profile_path()
    skip = blacklist if blacklist is not None else _get_blacklist()

    # Find all numbered startup scripts, sorted
    scripts = sorted(
        p for p in startup_dir.glob("[0-9]*.py")
        if not p.name.endswith(".pybak") and not p.name.endswith(".bak")
    )

    ns = _build_seed_namespace()
    loaded = []

    for script in scripts:
        prefix = script.name.split("-")[0]

        if whitelist is not None:
            if prefix not in whitelist:
                continue
        elif prefix in skip:
            logger.debug("Skipping {} (blacklisted)", script.name)
            continue

        logger.info("Loading {}", script.name)
        try:
            code = script.read_text(encoding="utf-8")
            exec(compile(code, str(script), "exec"), ns)
            loaded.append(script.name)
        except Exception:
            logger.exception("Failed to load {}", script.name)

    logger.info("Loaded {} profile scripts: {}", len(loaded), loaded)
    return ns


def extract_ophyd_devices(ns: dict[str, Any]) -> dict[str, Any]:
    """Extract ophyd Device and Signal instances from a namespace.

    Args:
        ns: Namespace dict from load_profile().

    Returns:
        Dict mapping variable name -> ophyd device instance.
    """
    try:
        from ophyd import Device, Signal
    except ImportError:
        logger.error("ophyd not installed, cannot extract devices")
        return {}

    devices = {}
    for name, obj in ns.items():
        if name.startswith("_"):
            continue
        if isinstance(obj, (Device, Signal)):
            devices[name] = obj

    logger.info("Extracted {} ophyd devices from profile namespace", len(devices))
    return devices


def extract_device_classes(ns: dict[str, Any]) -> dict[str, type]:
    """Extract ophyd Device subclasses defined in the profile.

    Args:
        ns: Namespace dict from load_profile().

    Returns:
        Dict mapping class name -> class object.
    """
    try:
        from ophyd import Device
    except ImportError:
        return {}

    classes = {}
    for name, obj in ns.items():
        if isinstance(obj, type) and issubclass(obj, Device) and obj is not Device:
            classes[name] = obj

    logger.info("Extracted {} device classes from profile namespace", len(classes))
    return classes
