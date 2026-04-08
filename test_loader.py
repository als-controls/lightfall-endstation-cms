#!/usr/bin/env python
"""Smoke test for the profile-collection loader.

Run from the repo root:
    python test_loader.py

This does NOT connect to EPICS -- it just verifies that the profile
scripts can be parsed and device instances extracted.
"""

import sys
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lucid_endstation_cms.loader import (
    extract_device_classes,
    extract_ophyd_devices,
    load_profile,
)


def main():
    print("=" * 60)
    print("CMS Profile Collection Loader - Smoke Test")
    print("=" * 60)

    # Load only the device files (whitelist mode)
    device_prefixes = {"10", "19", "20", "25", "26", "27", "42", "43", "50", "51", "52"}

    print(f"\nLoading profile scripts (whitelist: {sorted(device_prefixes)})...")
    try:
        ns = load_profile(whitelist=device_prefixes)
    except Exception as e:
        print(f"\nFailed to load profile: {e}")
        print("\nThis is expected if ophyd/epics are not installed.")
        print("The loader is designed to run at the beamline or with")
        print("a caproxy connection to CMS EPICS IOCs.")
        return 1

    # Extract devices
    devices = extract_ophyd_devices(ns)
    classes = extract_device_classes(ns)

    print(f"\n{'Devices extracted:':<30} {len(devices)}")
    print(f"{'Classes extracted:':<30} {len(classes)}")

    # Show devices by type
    print("\n--- Devices ---")
    by_type: dict[str, list[str]] = {}
    for name, obj in sorted(devices.items()):
        cls_name = type(obj).__name__
        by_type.setdefault(cls_name, []).append(name)

    for cls_name, names in sorted(by_type.items()):
        print(f"\n  {cls_name} ({len(names)}):")
        for n in names:
            prefix = getattr(devices[n], "prefix", "")
            print(f"    {n:<25} {prefix}")

    # Show classes
    print("\n--- Device Classes ---")
    for name, cls in sorted(classes.items()):
        bases = ", ".join(b.__name__ for b in cls.__bases__)
        print(f"  {name:<35} ({bases})")

    print(f"\n{'=' * 60}")
    print("Smoke test complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
