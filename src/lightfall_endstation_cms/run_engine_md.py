"""Restore the redis-backed RunEngine metadata store for CMS.

Background
----------
``00-startup.py`` made ``RE.md`` a redis-backed :class:`RedisJSONDict` via
``nslsii.configure_base(..., redis_url="info.cms.nsls2.bnl.gov")`` so the live
proposal context (``cycle``, ``data_session``, ``username`` …) is shared with the
beamline's redis -- updated by ``sync_experiment`` / beamline staff as proposals
change. That call was dropped when the profile stopped running under post-login
plugin loading, so Lightfall's shared engine carried a plain ``dict`` holding
only ``{'versions': …}``; ``assets.assets_path`` (which reads cycle/data_session)
could not resolve and detectors crashed at stage time.

This module re-expresses *only* the metadata-store piece of ``configure_base``
(not its kafka / tiled-subscription / SupplementalData wiring, which the new
plugin architecture handles elsewhere): build the same ``RedisJSONDict`` -- via
``nslsii.open_redis_client`` so the canonical NSLS-II connection logic and the
``$REDIS_HOST`` / ``$REDIS_PORT`` overrides are honored -- and assign it onto
Lightfall's shared RunEngine. :func:`wire_redis_metadata` is called from the CMS
device-backend plugin at backend-creation time (post-login).
"""
from __future__ import annotations

from loguru import logger

# Beamline redis that carries the CMS proposal context. Matches 00-startup's
# nslsii.configure_base(redis_url=...). nslsii.open_redis_client honors the
# $REDIS_HOST / $REDIS_PORT env overrides, so this is just the default.
_REDIS_URL = "info.cms.nsls2.bnl.gov"


def wire_redis_metadata() -> None:
    """Point the shared RunEngine's ``RE.md`` at the CMS beamline redis.

    Best-effort and idempotent: if nslsii/redis_json_dict are missing, the
    shared RunEngine is not available yet, or ``RE.md`` is already redis-backed,
    we log and return without raising so backend creation (and the rest of
    startup) is not aborted. On any redis/connection error we likewise log and
    leave ``RE.md`` untouched -- ``assets_path`` then raises its own actionable
    "select a proposal" error rather than this aborting startup.
    """
    try:
        from nslsii import open_redis_client
        from redis_json_dict import RedisJSONDict
    except Exception:
        logger.exception(
            "Cannot restore redis-backed RE.md (nslsii/redis_json_dict missing?); "
            "proposal context (cycle/data_session) will be unavailable"
        )
        return

    try:
        from lightfall.acquire import get_engine

        run_engine = getattr(get_engine(), "RE", None)
        if run_engine is None:
            logger.warning(
                "Shared RunEngine not available; skipping redis-backed RE.md "
                "setup (proposal context will be unavailable)"
            )
            return

        if isinstance(getattr(run_engine, "md", None), RedisJSONDict):
            logger.info("RE.md is already redis-backed; leaving it as-is")
            return

        # Mirror nslsii.configure_base(redis_url=...): default port/db, no SSL,
        # empty prefix. open_redis_client builds the Redis connection (lazily
        # connected, so this does not block even if redis is unreachable).
        redis_client = open_redis_client(redis_url=_REDIS_URL)
        run_engine.md = RedisJSONDict(redis_client=redis_client, prefix="")
        logger.info(
            "Wired redis-backed RE.md from {} (CMS proposal context)", _REDIS_URL
        )
    except Exception:
        logger.exception(
            "Failed to wire redis-backed RE.md from {}; proposal context "
            "(cycle/data_session) will be unavailable",
            _REDIS_URL,
        )
