"""Re-express 00-startup's SupplementalData onto Lightfall's RunEngine.

``configure_base`` creates a ``SupplementalData`` and appends it to
``RE.preprocessors`` (exposing ``sd`` in the namespace). The CMS profile does not
populate ``sd.baseline``, so this is an empty SD that scans / the SAM framework
can add to. ``BestEffortCallback`` is intentionally NOT re-expressed -- the GUI
session needs no console live-table/plot.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

# Module-level handle so a re-login / second backend creation reuses the same
# SupplementalData instead of appending a duplicate preprocessor.
_sd: Any = None


def get_supplemental_data() -> Any:
    """Return the shared SupplementalData instance, or None if not yet wired."""
    return _sd


def wire_supplemental_data() -> Any:
    """Create a SupplementalData and append it to the shared RunEngine.

    Mirrors ``configure_base``'s ``sd`` (added to ``RE.preprocessors``). The
    instance is returned so callers can seed it into the SAM namespace as ``sd``.

    Best-effort and idempotent: if bluesky is missing, the shared RunEngine is
    unavailable, or an SD is already wired, we log and return the existing
    instance (or None) without raising.
    """
    global _sd
    if _sd is not None:
        logger.info("SupplementalData already wired; leaving it as-is")
        return _sd

    try:
        from bluesky.preprocessors import SupplementalData
    except Exception:
        logger.exception(
            "Cannot wire SupplementalData (bluesky missing?); baseline/monitor "
            "preprocessing will be unavailable"
        )
        return None

    try:
        from lightfall.acquire import get_engine

        run_engine = getattr(get_engine(), "RE", None)
        if run_engine is None:
            logger.warning(
                "Shared RunEngine not available; skipping SupplementalData wiring"
            )
            return None

        sd = SupplementalData()
        run_engine.preprocessors.append(sd)
        _sd = sd
        logger.info(
            "Wired SupplementalData onto the shared RunEngine (empty baseline)"
        )
        return sd
    except Exception:
        logger.exception("Failed to wire SupplementalData")
        return None
