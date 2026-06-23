"""Resolve and wire the per-proposal *assets* directory for CMS detectors.

Background
----------
The area detectors (``devices/area_detectors.py``) and the Xspress3
(``devices/xspress3.py``) write their files under a per-proposal *assets*
directory. Each module exposes a module-level ``assets_path`` hook that MUST be
a callable returning that directory before any such detector stages; an unset
hook raises ``RuntimeError`` at stage time (the ``area_detectors.assets_path is
not set`` crash).

In the old profile-collection world ``00-startup.py`` defined ``assets_path()``
-- a closure over the redis-backed ``RE.md`` cycle/data_session -- and
``ProfileSessionBootstrapper._wire_assets_path`` lifted it onto the device
modules. Under post-login plugin loading that bootstrapper is no longer armed
(see ``plugin.py``), so the wiring was lost. This module re-expresses it in a
self-contained way: the resolver reads the proposal context from Lightfall's
shared RunEngine (``get_engine().RE.md``), and :func:`wire_assets_path` is
called from the CMS device-backend plugin at backend-creation time (post-login).
"""
from __future__ import annotations

import os

from loguru import logger

# Explicit override for sessions whose ``RE.md`` has no proposal context yet
# (e.g. before a proposal is selected, or when staging detectors outside a
# proposal session). Mirrors the device modules' documented "set assets_path
# explicitly outside a profile session" escape hatch.
_ASSETS_PATH_ENV = "CMS_ASSETS_PATH"
_PROPOSALS_ROOT = "/nsls2/data/cms/proposals"


def assets_path() -> str:
    """Return the per-proposal assets directory for CMS detectors.

    Resolution order:

    1. ``$CMS_ASSETS_PATH`` if set (explicit override; trailing ``/`` enforced).
    2. ``/nsls2/data/cms/proposals/{cycle}/{data_session}/assets/`` derived from
       the shared RunEngine's ``RE.md``.

    Raises:
        RuntimeError: if neither source resolves. We deliberately refuse to
            return a path containing ``None`` segments -- a silent
            ``.../proposals/None/None/assets/`` would write data to the wrong
            place. Staging fails loudly with an actionable message instead.
    """
    override = os.environ.get(_ASSETS_PATH_ENV)
    if override:
        return override if override.endswith("/") else override + "/"

    # Imported lazily so this module stays importable without lightfall's
    # engine present (e.g. in unit tests that monkeypatch the resolver).
    from lightfall.acquire import get_engine

    run_engine = getattr(get_engine(), "RE", None)
    md = getattr(run_engine, "md", None) or {}
    cycle = md.get("cycle")
    data_session = md.get("data_session")
    if cycle and data_session:
        return f"{_PROPOSALS_ROOT}/{cycle}/{data_session}/assets/"

    raise RuntimeError(
        "CMS assets_path is unresolved: RE.md has no 'cycle'/'data_session' "
        f"(select a proposal first) and ${_ASSETS_PATH_ENV} is not set. "
        "Detectors cannot stage until the proposal context is available."
    )


def wire_assets_path() -> None:
    """Point the CMS detector modules' ``assets_path`` hook at :func:`assets_path`.

    Idempotent and best-effort: importing the device modules requires
    ``nslsii``; if that is unavailable we log and return so backend creation
    (and the rest of startup) is not aborted. Replaces the wiring that
    ``ProfileSessionBootstrapper._wire_assets_path`` used to perform.
    """
    try:
        from lightfall_endstation_cms.devices import area_detectors, xspress3
    except Exception:
        logger.exception(
            "Could not import CMS detector modules to wire assets_path "
            "(nslsii missing?); detector staging will raise until it is set"
        )
        return

    area_detectors.assets_path = assets_path
    xspress3.assets_path = assets_path
    logger.info("Wired assets_path onto area_detectors and xspress3 modules")
