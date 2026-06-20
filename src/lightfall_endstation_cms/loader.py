"""Profile-collection path resolution for the CMS beamline.

Devices are no longer discovered by executing the profile-collection — they come
from the happi database shipped in :mod:`lightfall_endstation_cms.devices`.  The
:class:`~lightfall_endstation_cms.bootstrap.ProfileSessionBootstrapper` still
runs the profile's *infrastructure* scripts in the live kernel to adopt the
RunEngine and Tiled client, and uses :func:`_get_profile_path` to find them.

The profile-collection is expected as a git submodule at ``./profile-collection/``
or at a path given by the ``CMS_PROFILE_PATH`` environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path


def _get_profile_path() -> Path:
    """Resolve the profile-collection ``startup/`` directory.

    Honors ``CMS_PROFILE_PATH`` (pointing at the ``startup/`` dir); otherwise
    falls back to the ``profile-collection/startup`` git submodule shipped
    alongside this package.
    """
    env_path = os.environ.get("CMS_PROFILE_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_dir():
            return p
        raise FileNotFoundError(f"CMS_PROFILE_PATH={env_path} does not exist")

    # Default: submodule relative to this package.
    # __file__ is .../src/lightfall_endstation_cms/loader.py -> 3 parents to repo root
    pkg_dir = Path(__file__).parent.parent.parent
    submodule = pkg_dir / "profile-collection" / "startup"
    if submodule.is_dir():
        return submodule

    raise FileNotFoundError(
        "Could not find CMS profile-collection. Set CMS_PROFILE_PATH or "
        "ensure the git submodule is initialized."
    )
