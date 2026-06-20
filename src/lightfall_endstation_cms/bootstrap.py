"""Run the CMS profile in Lightfall's console kernel and adopt its objects."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from lightfall.acquire.engine import get_engine
from lightfall.acquire.engine.console_proxy import ConsoleREProxy
from lightfall.services.tiled_service import TiledService

TILED_URI = "https://tiled.nsls2.bnl.gov"

# Profile scripts to RUN, by numeric filename prefix. Devices now come from the
# happi backend (see plugin.py), so the profile is no longer the device source
# and we run ONLY its infrastructure scripts:
#
#   00-startup          RunEngine (nslsii.configure_base), tiled_writing_client,
#                       tiled_reading_client/mig, assets_path(), set_defaults
#   01-ad33_tmp         area-detector v3.3 compatibility shim
#   02-tiled-writer     subscribes the Tiled document writer to the RunEngine
#   03-async            async/RunEngine plumbing
#
# Everything from 10 onward defines devices, plans or helpers that are being
# migrated to Lightfall (happi devices today; Lightfall plans next), and is no
# longer executed in the kernel. Running them would also fail, since the device
# globals they reference (e.g. 94-sample needs 10-motors) are no longer created.
#
# Override with the CMS_PROFILE_KEEP env var (comma-separated prefixes, which
# fully REPLACES this default) — e.g. to temporarily run a migrated-but-not-yet
# script during a transition.
DEFAULT_PROFILE_KEEP = frozenset({"00", "01", "02", "03"})


class ProfileSessionBootstrapper:
    """Runs the CMS profile's infra scripts in the live kernel and adopts the
    RunEngine + write-scoped Tiled client. Devices come from happi, not here."""

    def _keep(self) -> set[str]:
        """Numeric prefixes of profile scripts to run.

        Honors the ``CMS_PROFILE_KEEP`` env var (comma-separated prefixes),
        which fully REPLACES :data:`DEFAULT_PROFILE_KEEP` when set.
        """
        env = os.environ.get("CMS_PROFILE_KEEP")
        if env is not None:
            return {s.strip() for s in env.split(",") if s.strip()}
        return set(DEFAULT_PROFILE_KEEP)

    def _profile_scripts(self) -> list[Path]:
        """Ordered infra profile scripts to run. BEAMLINE SEAM (overridden in tests).

        Skips backup/experimental variants and any script whose numeric prefix
        is not in the keep-set (see :meth:`_keep`).
        """
        from lightfall_endstation_cms.loader import _get_profile_path

        startup = _get_profile_path()
        keep = self._keep()
        scripts: list[Path] = []
        for p in sorted(startup.glob("[0-9]*.py")):
            if p.name.endswith((".pybak", ".bak")) or "_new." in p.name:
                continue
            prefix = p.name.split("-")[0]
            if prefix not in keep:
                logger.debug("Skipping non-infra profile script: {}", p.name)
                continue
            scripts.append(p)
        return scripts

    @staticmethod
    def _pump_events() -> None:
        """Let the GUI repaint / stay responsive during the long profile load.

        run_profile runs synchronously on the main (GUI) thread, so without
        pumping the event loop the main window shows but never paints until the
        whole profile finishes. Pumping between scripts draws it and updates it
        progressively. No-op if there is no running QApplication (e.g. tests).
        """
        try:
            from PySide6.QtCore import QEventLoop
            from PySide6.QtWidgets import QApplication
        except ImportError:
            return
        try:
            app = QApplication.instance()
            if app is not None:
                # Exclude user-input events: the profile isn't fully loaded yet
                # (no adopted RunEngine), so a stray button click mid-load could
                # act on uninitialized state. We only want paint/timer events.
                app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        except Exception:
            logger.debug("_pump_events: processEvents failed", exc_info=True)

    @staticmethod
    def _make_progress(total: int):
        """Modal progress dialog for the profile load, or None (headless/tests).

        The profile load is long and runs on the main thread; a modal progress
        dialog gives the operator visible feedback (and blocks stray input)
        instead of a frozen, blank window.
        """
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QApplication, QProgressDialog
        except ImportError:
            return None
        if QApplication.instance() is None:
            return None
        try:
            parent = None
            try:
                from lightfall.core import LFApplication

                app = LFApplication.get_instance()
                parent = app.main_window if app else None
            except Exception:
                parent = None
            dlg = QProgressDialog("Loading CMS beamline profile…", None, 0, total, parent)
            dlg.setWindowTitle("CMS profile")
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            dlg.setCancelButton(None)  # a partial profile load can't be safely interrupted
            dlg.setMinimumDuration(0)
            dlg.setValue(0)
            return dlg
        except Exception:
            logger.debug("Could not create profile progress dialog", exc_info=True)
            return None

    def run_profile(self, shell: Any) -> None:
        """Execute the profile scripts into the kernel shell (beamline)."""
        scripts = self._profile_scripts()
        # Paint the (already-shown) main window before the long load begins.
        self._pump_events()

        # Put the startup dir on sys.path so scripts that import a sibling
        # script by filename resolve (e.g. 86-live-spec.py does
        # importlib.import_module('85-suitcase-specfile')). The real beamline
        # IPython profile makes the startup dir importable; replicate that.
        if scripts:
            startup_dir = str(scripts[0].parent)
            if startup_dir not in sys.path:
                # Append (not insert) so the startup dir can't shadow stdlib /
                # site-packages; sibling-by-filename imports resolve regardless.
                sys.path.append(startup_dir)
                logger.info("Added profile startup dir to sys.path: {}", startup_dir)

        progress = self._make_progress(len(scripts))
        try:
            for i, script in enumerate(scripts):
                if progress is not None:
                    progress.setLabelText(f"Loading CMS profile: {script.name}")
                    progress.setValue(i)  # modal QProgressDialog pumps events here
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
                        logger.error(
                            "Profile script {} raised: {}", script.name, result.error_in_exec
                        )
                except Exception:
                    logger.exception("Unexpected error running profile script {}", script.name)
                # Keep the GUI alive/updating between scripts.
                self._pump_events()
        finally:
            if progress is not None:
                progress.setValue(len(scripts))
                progress.close()

    def adopt(self, namespace: dict[str, Any]) -> bool:
        """Adopt the RunEngine and the write-scoped Tiled client from the namespace.

        Must run AFTER the infra profile load so that load-time
        ``RE.subscribe(...)`` / ``RE.md[...]`` wiring binds to the raw RE.
        Devices are NOT adopted here — they come from the happi backend.

        Returns True if the RunEngine was adopted (the core success condition;
        a missing ``mig`` is logged but still counts as success), False if the
        profile produced no ``RE`` so nothing could be adopted.
        """
        # If the profile failed before creating RE (e.g. configure_base could
        # not reach Redis), there is nothing to adopt — log and bail rather than
        # raising a KeyError, so a partial load degrades gracefully.
        run_engine = namespace.get("RE")
        if run_engine is None:
            logger.error(
                "Profile namespace has no 'RE' — profile load likely failed; "
                "skipping RE/device/Tiled adoption"
            )
            return False

        engine = get_engine()

        # 1) Engine adopts the profile's RE (subscriptions/preprocessors intact),
        #    then rebind the console name `RE` to a GUI-safe proxy.
        engine.adopt(run_engine)
        namespace["RE"] = ConsoleREProxy(engine)
        logger.info("Adopted profile RunEngine; console RE is now a ConsoleREProxy")

        # 2) Wire the detectors' assets_path. The happi-instantiated area
        #    detectors / Xspress3 need a callable returning the per-proposal
        #    assets directory before they can stage. 00-startup defines
        #    assets_path() (a closure over the live RE.md cycle/data_session);
        #    lift it onto the device modules so happi-built detectors can write.
        #    Best-effort: if nslsii is absent the modules won't import, but
        #    RE/Tiled adoption must still succeed.
        self._wire_assets_path(namespace)

        # 3) Tiled client: adopt the write-scoped, data-visible client.
        #    `mig`/`cat` use username=None -> interactive Duo (CannotPrompt
        #    in the GUI) and, even when authed, only show runs whose
        #    access_tags match the user, so they browse empty and cannot
        #    write. `tiled_writing_client` (00-startup.py) is from_profile(
        #    "nsls2", api_key=...)["cms"]["raw"]: it carries write:data/
        #    write:metadata/create:node and, as a service principal, sees
        #    all entries. TiledService drives its TiledWriter from this
        #    same client, so adopting it fixes empty reads AND writes.
        #    Fall back to `mig` if the profile predates the writing client.
        client = namespace.get("tiled_writing_client")
        label = "tiled_writing_client (cms/raw, write-scoped)"
        if client is None:
            client = namespace.get("mig")
            label = "mig (cms/migration, legacy fallback)"
        if client is not None:
            TiledService.get_instance().adopt_client(client, url=TILED_URI)
            logger.info("Adopted {} as the Tiled client", label)
        else:
            logger.warning(
                "Profile namespace has no 'tiled_writing_client' or 'mig'; "
                "Tiled not adopted"
            )
        return True

    @staticmethod
    def _wire_assets_path(namespace: dict[str, Any]) -> None:
        """Point the device modules' ``assets_path`` at the profile's callable.

        ``area_detectors`` and ``xspress3`` expose a module-level
        ``assets_path`` hook that must be a callable before any detector stages
        (see those modules). 00-startup defines ``assets_path()``; share it so
        the happi-instantiated detectors write to the correct per-proposal
        directory. Best-effort: importing the device modules needs ``nslsii``,
        so a failure here must not abort RE/Tiled adoption.
        """
        assets_path = namespace.get("assets_path")
        if not callable(assets_path):
            logger.warning(
                "Profile namespace has no callable 'assets_path'; detector "
                "staging will raise until it is set"
            )
            return
        try:
            from lightfall_endstation_cms.devices import area_detectors, xspress3

            area_detectors.assets_path = assets_path
            xspress3.assets_path = assets_path
            logger.info("Wired assets_path onto area_detectors and xspress3 modules")
        except Exception:
            logger.exception(
                "Could not wire assets_path onto device modules (nslsii missing?)"
            )

    def bootstrap(self, shell: Any) -> bool:
        """Full handshake: run the infra profile, then adopt RE + Tiled.

        Returns whether adoption succeeded (the RunEngine was adopted).
        """
        self.run_profile(shell)
        return self.adopt(shell.user_ns)
