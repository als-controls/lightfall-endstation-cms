"""Run the CMS profile in Lightfall's console kernel and adopt its objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from lightfall.acquire.engine import get_engine
from lightfall.acquire.engine.console_proxy import ConsoleREProxy
from lightfall.services.tiled_service import TiledService

TILED_URI = "https://tiled.nsls2.bnl.gov"


class ProfileSessionBootstrapper:
    """Loads the CMS profile into the live kernel and adopts RE/devices/mig."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def _profile_scripts(self) -> list[Path]:
        """Ordered profile scripts to run. BEAMLINE SEAM (overridden in tests)."""
        from lightfall_endstation_cms.loader import _get_profile_path

        startup = _get_profile_path()
        return sorted(
            p for p in startup.glob("[0-9]*.py")
            if not p.name.endswith((".pybak", ".bak")) and "_new." not in p.name
        )

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
