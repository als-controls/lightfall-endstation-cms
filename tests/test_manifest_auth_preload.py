from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.manifest import manifest


def _entry(type_name: str, name: str):
    return next(
        e for e in manifest.plugins if e.type_name == type_name and e.name == name
    )


def test_nsls2_auth_provider_is_preload():
    """The NSLS-II auth provider must preload so it is registered before the
    startup login dialog renders its provider buttons (otherwise the
    'NSLS-II (CMS)' Duo login button is missing)."""
    entry = _entry("auth_provider", "nsls2_tiled")
    assert entry.preload is True
    assert entry.import_path == (
        "lightfall_endstation_cms.auth.nsls2_provider:NSLS2AuthPlugin"
    )


def test_device_backend_entry_present():
    # Sanity: the device_backend entry still exists and stays background-loaded
    # (its CMSSessionTrigger only needs to arm before the user finishes login;
    # preloading it would run the sandboxed connect() synchronously at startup).
    entry = _entry("device_backend", "cms_profile_collection")
    assert entry.import_path == (
        "lightfall_endstation_cms.plugin:CMSProfileCollectionPlugin"
    )
    assert not entry.preload
