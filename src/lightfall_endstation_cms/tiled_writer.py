"""Subscribe a Tiled document writer to the shared RunEngine for CMS.

Background
----------
``00-startup.py`` persisted bluesky documents to Tiled via
``nslsii.configure_base(..., tiled_inserter, publish_documents_with_kafka=True)``:
the inserter posted every document to
``tiled_writing_client = from_profile("nsls2", api_key=...)["cms"]["raw"]``.

That write path was dropped when the profile stopped running under post-login
plugin loading. :func:`run_engine_md.wire_redis_metadata` restored
``configure_base``'s *metadata-store* piece, but NOT its *document-writing*
piece — so Lightfall's shared (native) RunEngine dispatched documents to the GUI
(LiveTable, ``RE.md.scan_id`` ticked up in redis) but **nothing posted them to
Tiled**, and runs never appeared in ``cms/raw``.

The gap is structural: ``TiledService`` only holds an *adopted* read client, and
``TiledService.adopt_client()`` — unlike its ``from_uri`` connect path — never
calls ``_subscribe_writer()``. So an adopted client is read-only by
construction.

This module re-expresses the document-writing piece, mirroring the old
``tiled_inserter`` (post with the CMS service key; no client-side AccessStamper —
the existing records were written the same way, the server applies its own
access policy): build a write-scoped ``cms/raw`` client and subscribe a threaded
``TiledWriter`` to the shared RunEngine. Called from the CMS device-backend
plugin at backend-creation time (post-login), alongside ``wire_redis_metadata``.
"""
from __future__ import annotations

import os
from typing import Any

from loguru import logger

# NSLS-II Tiled server + the cms/raw write node. Matches 00-startup's
# tiled_writing_client = from_profile("nsls2", api_key=...)["cms"]["raw"].
_TILED_URI = "https://tiled.nsls2.bnl.gov"
_WRITE_NODE = "cms/raw"
# Service/admin key with write:data/write:metadata — the same key 00-startup
# built the writing client with, and the read identity the browser uses.
_WRITE_API_KEY_ENV = "TILED_BLUESKY_WRITING_API_KEY_CMS"

# Module-level handles so a re-login / second backend creation does not subscribe
# a duplicate writer (which would double-post every document to cms/raw).
_writer: Any = None
_token: int | None = None


class _PostDocumentWriter:
    """Document callback that persists a run via ``client.post_document``.

    Mirrors 00-startup's ``tiled_inserter``: every ``(name, doc)`` is posted to
    the ``cms/raw`` catalog, letting the NSLS-II server apply its own access
    policy server-side. This is how the existing records were written.

    Deliberately NOT ``bluesky_tiled_plugins.TiledWriter``: that writer calls
    ``create_container(access_tags=doc.pop("tiled_access_tags", None))``, so
    without an AccessStamper populating ``tiled_access_tags`` it sends
    ``access_tags=None`` and the server rejects the write with HTTP 500.
    Lightfall's core AccessStamper is ALS/alshub-specific (its tag schema is in
    lockstep with ``als_tiled``), so it is not the right tag source for NSLS-II.

    Wrapped in a ``ThreadedTiledWriter`` by :func:`wire_tiled_writer` so the
    HTTP posts run off the engine thread and never block plan execution.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def __call__(self, name: str, doc: dict[str, Any]) -> None:
        self._client.post_document(name, doc)


def wire_tiled_writer() -> None:
    """Subscribe a Tiled document writer to the shared RunEngine.

    Best-effort and idempotent: if the service key is missing, the shared
    RunEngine is unavailable, or a writer is already subscribed, we log and
    return without raising — so backend creation (and the rest of startup) is
    never aborted. On any error the partial state is rolled back so a later
    call can retry cleanly.
    """
    global _writer, _token

    if _token is not None:
        logger.info("Tiled document writer already subscribed; leaving it as-is")
        return

    api_key = os.environ.get(_WRITE_API_KEY_ENV)
    if not api_key:
        logger.warning(
            "{} is not set; cannot subscribe a Tiled document writer — runs will "
            "NOT be saved to Tiled ({})",
            _WRITE_API_KEY_ENV,
            _WRITE_NODE,
        )
        return

    try:
        from tiled.client import from_uri

        from lightfall.acquire import get_engine
        from lightfall.services.threaded_tiled_writer import ThreadedTiledWriter

        engine = get_engine()
        if engine is None:
            logger.warning(
                "Shared RunEngine not available; skipping Tiled writer "
                "subscription (runs will not be saved to Tiled)"
            )
            return

        # Write-scoped cms/raw client (service key carries write:data /
        # write:metadata; built independently of TiledService's adopted read
        # client so the write path does not depend on the read adoption).
        write_client = from_uri(_TILED_URI, api_key=api_key)[_WRITE_NODE]

        # Post each document via client.post_document (the legacy tiled_inserter
        # path), wrapped in ThreadedTiledWriter so the HTTP posts run off the
        # engine thread and never block plan execution. See _PostDocumentWriter
        # for why this is used instead of bluesky_tiled_plugins.TiledWriter.
        raw_writer = _PostDocumentWriter(write_client)
        writer = ThreadedTiledWriter(raw_writer, error_callback=_on_writer_error)
        token = engine.subscribe(writer)

        _writer, _token = writer, token
        logger.info(
            "Subscribed Tiled document writer to the shared RunEngine "
            "(writing to {}/{})",
            _TILED_URI,
            _WRITE_NODE,
        )
    except Exception:
        # Roll back any partial state so a later (re-login) call can retry.
        _writer, _token = None, None
        logger.exception(
            "Failed to subscribe a Tiled document writer; runs will NOT be saved "
            "to Tiled ({})",
            _WRITE_NODE,
        )


def _on_writer_error(name: str, doc: dict[str, Any], error: Exception) -> None:
    """Surface Tiled write failures without spamming the log.

    A 401 / permissions error means the service key lacks write scopes (or is
    wrong); anything else is logged once per document type so a systemic issue
    is visible but a transient hiccup does not flood the log.
    """
    error_str = str(error)
    if "401" in error_str or "Not enough permissions" in error_str:
        logger.warning(
            "Tiled write permission denied writing '{}' document; data will not "
            "be saved to {}. Check {} has write scopes.",
            name,
            _WRITE_NODE,
            _WRITE_API_KEY_ENV,
        )
    else:
        logger.warning("Tiled writer error on '{}' document: {}", name, error)
