"""Re-express 00-startup's Kafka document publisher onto Lightfall's RunEngine.

00-startup runs ``nslsii.configure_base(..., publish_documents_with_kafka=True)``,
which resolves to ``nslsii.configure_kafka_publisher(RE, beamline_name="cms")``
(the name comes from ``TiledInserter.name == "cms"``). Rather than run the
profile and adopt its RunEngine, we attach the same publisher to Lightfall's own
RunEngine.
"""

from __future__ import annotations

import os

from loguru import logger

# CMS kafka beamline name -> topic ``cms.bluesky.runengine.documents``. Matches
# 00-startup's ``TiledInserter.name``. Overridable for deployments that differ.
_BEAMLINE_NAME = os.environ.get("CMS_KAFKA_BEAMLINE_NAME", "cms")

# Module-level guard so a re-login / second backend creation does not subscribe
# a duplicate publisher (which would double-publish every document to kafka).
_subscribed = False


def wire_kafka_publisher() -> None:
    """Subscribe nslsii's Kafka document publisher to the shared RunEngine.

    Mirrors ``configure_base(..., publish_documents_with_kafka=True)``:
    ``configure_kafka_publisher`` reads the bluesky kafka config
    (``$BLUESKY_KAFKA_CONFIG_PATH`` or ``/etc/bluesky/kafka.yml``) and subscribes
    a Publisher to the RunEngine so documents reach the NSLS-II kafka broker.

    Best-effort and idempotent: if nslsii is missing, the shared RunEngine is
    unavailable, the kafka config cannot be read, or a publisher is already
    subscribed, we log and return without raising so backend creation (and the
    rest of startup) is never aborted.
    """
    global _subscribed
    if _subscribed:
        logger.info("Kafka publisher already subscribed; leaving it as-is")
        return

    try:
        from nslsii import configure_kafka_publisher
    except Exception:
        logger.exception(
            "Cannot subscribe the Kafka publisher (nslsii missing?); documents "
            "will not be published to the NSLS-II kafka broker"
        )
        return

    try:
        from lightfall.acquire import get_engine

        run_engine = getattr(get_engine(), "RE", None)
        if run_engine is None:
            logger.warning(
                "Shared RunEngine not available; skipping Kafka publisher "
                "subscription (documents will not be published to kafka)"
            )
            return

        # Reads $BLUESKY_KAFKA_CONFIG_PATH or /etc/bluesky/kafka.yml and
        # subscribes a Publisher to run_engine.
        configure_kafka_publisher(run_engine, beamline_name=_BEAMLINE_NAME)
        _subscribed = True
        logger.info(
            "Subscribed Kafka document publisher to the shared RunEngine "
            "(beamline_name={})",
            _BEAMLINE_NAME,
        )
    except Exception:
        logger.exception(
            "Failed to subscribe the Kafka publisher; documents will not be "
            "published to the NSLS-II kafka broker"
        )
