"""Run the CMS profile in Lightfall's console kernel and adopt its objects."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger

from lightfall.acquire.engine import get_engine
from lightfall.acquire.engine.console_proxy import ConsoleREProxy
from lightfall.services.tiled_service import TiledService

TILED_URI = "https://tiled.nsls2.bnl.gov"

# Profile scripts skipped by default because their dependencies are unavailable
# on the Lightfall runtime (Python 3.13): 24-area-detector-utilities imports
# telnetlib (removed in Python 3.12+), and 55-archiver imports arvpyf (an
# NSLS-II-internal package not published on PyPI). Matched by numeric filename
# prefix. Override with the CMS_PROFILE_BLACKLIST env var (a comma-separated
# list of prefixes, which REPLACES this default).
DEFAULT_PROFILE_BLACKLIST = frozenset({"24", "55"})


class ProfileSessionBootstrapper:
    """Loads the CMS profile into the live kernel and adopts RE/devices/mig."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def _blacklist(self) -> set[str]:
        """Numeric prefixes of profile scripts to skip.

        Honors the ``CMS_PROFILE_BLACKLIST`` env var (comma-separated prefixes),
        which fully REPLACES :data:`DEFAULT_PROFILE_BLACKLIST` when set. Setting
        it empty (``CMS_PROFILE_BLACKLIST=``) clears the blacklist entirely, so
        every profile script runs.
        """
        env = os.environ.get("CMS_PROFILE_BLACKLIST")
        if env is not None:
            return {s.strip() for s in env.split(",") if s.strip()}
        return set(DEFAULT_PROFILE_BLACKLIST)

    def _profile_scripts(self) -> list[Path]:
        """Ordered profile scripts to run. BEAMLINE SEAM (overridden in tests).

        Skips backup/experimental variants and any script whose numeric prefix
        is blacklisted (see :meth:`_blacklist`) — e.g. modules needing deps that
        are unavailable on the Lightfall runtime.
        """
        from lightfall_endstation_cms.loader import _get_profile_path

        startup = _get_profile_path()
        skip = self._blacklist()
        scripts: list[Path] = []
        for p in sorted(startup.glob("[0-9]*.py")):
            if p.name.endswith((".pybak", ".bak")) or "_new." in p.name:
                continue
            prefix = p.name.split("-")[0]
            if prefix in skip:
                logger.info("Skipping blacklisted profile script: {}", p.name)
                continue
            scripts.append(p)
        return scripts

    def run_profile(self, shell: Any) -> None:
        """Execute the profile scripts into the kernel shell (beamline)."""
        for script in self._profile_scripts():
            logger.info("Running profile script into console: {}", script.name)
            try:
                source = script.read_text(encoding="utf-8")
            except Exception:
                logger.exception("Failed to read profile script {}", script.name)
                continue
            try:
                shell.user_ns["__file__"] = str(script)
                result = shell.run_cell(source, store_history=False)
                if getattr(result, "error_in_exec", None) is not None:
                    logger.error("Profile script {} raised: {}", script.name, result.error_in_exec)
            except Exception:
                logger.exception("Unexpected error running profile script {}", script.name)

    def adopt(self, namespace: dict[str, Any]) -> None:
        """Adopt RE, devices, and the mig reading client from the namespace.

        Must run AFTER the full profile load so that load-time
        ``RE.subscribe(...)`` / ``RE.md[...]`` wiring binds to the raw RE.
        """
        engine = get_engine()

        # 1) Engine adopts the profile's RE (subscriptions/preprocessors intact),
        #    then rebind the console name `RE` to a GUI-safe proxy.
        engine.adopt(namespace["RE"])
        namespace["RE"] = ConsoleREProxy(engine)
        logger.info("Adopted profile RunEngine; console RE is now a ConsoleREProxy")

        # 2) Devices come from the live namespace.
        n = self._backend.populate_from_namespace(namespace)
        logger.info("Adopted {} devices from profile namespace", n)

        # 3) Reading client: the Duo-authed, cms/migration-scoped `mig`.
        mig = namespace.get("mig")
        if mig is not None:
            TiledService.get_instance().adopt_client(mig, url=TILED_URI)
            logger.info("Adopted 'mig' as the Tiled reading client")
        else:
            logger.warning("Profile namespace has no 'mig' client; Tiled read not adopted")

    def bootstrap(self, shell: Any) -> None:
        """Full handshake: run the profile, then adopt its objects."""
        self.run_profile(shell)
        self.adopt(shell.user_ns)
