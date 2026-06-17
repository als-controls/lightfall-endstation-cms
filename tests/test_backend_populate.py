from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ophyd.sim import SynAxis

from lightfall_endstation_cms.backends.profile_collection import ProfileCollectionBackend


def test_populate_from_namespace_builds_catalog():
    backend = ProfileCollectionBackend()
    ns = {"smx": SynAxis(name="smx"), "_hidden": 123, "note": "not a device"}

    count = backend.populate_from_namespace(ns)

    assert count == 1
    assert backend.is_connected is True
    dev = backend.get_device_by_name("smx")
    assert dev is not None
    assert dev.display_name == "smx"


def test_populate_from_empty_namespace_keeps_existing_catalog():
    """A namespace with no devices (e.g. profile devices failed on EPICS CA)
    must NOT wipe an existing catalog (e.g. the one connect() built)."""
    backend = ProfileCollectionBackend()
    backend.populate_from_namespace({"smx": SynAxis(name="smx")})
    assert backend.get_device_by_name("smx") is not None

    # Full run produced nothing -> keep the existing device.
    count = backend.populate_from_namespace({"note": "no devices here"})

    assert count == 1
    assert backend.get_device_by_name("smx") is not None
