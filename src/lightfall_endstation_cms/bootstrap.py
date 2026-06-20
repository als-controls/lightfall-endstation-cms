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

# The profile runs in the kernel in two phases.
#
# INFRA (run first): the infrastructure scripts that stand up the RunEngine and
# Tiled clients, by numeric filename prefix:
#   00-startup       RunEngine (nslsii.configure_base), tiled_writing_client,
#                    tiled_reading_client/mig, assets_path(), set_defaults
#   01-ad33_tmp      area-detector v3.3 compatibility shim
#   02-tiled-writer  subscribes the Tiled document writer to the RunEngine
#   03-async         async/RunEngine plumbing
# After INFRA we adopt the RunEngine + Tiled client and INJECT the happi device
# instances into the kernel namespace under their profile variable names.
#
# SAM (run after injection): CMS's high-level beamline/sample framework, which
# the console relies on. These reference device globals (now injected), the
# adopted RE, and caget/caput:
#   90-bluesky       detselect, config_load/config_update, valve/pump helpers,
#                    data/metadata output (defines functions only — does NOT
#                    recreate the RunEngine, so it is safe over the adopted RE)
#   81-beam          beam, the Beamline/CMS_Beamline_XR hierarchy, cms, get_beamline
#   82-beamstop      beamstop helpers
#   94-sample        CoordinateSystem/Axis/Sample/Stage/SampleStage/Holder
#   95-sample-custom per-experiment Sample subclasses
#   96-automation    automated measurement loops
#   97-user          get_default_stage and user setup (needs config_load from 90)
#   991-modular-table modular-table coordinate system
# The device-DEFINING scripts (10/19/20/25/26/27/41-52) are intentionally NOT
# run — those devices come from happi and are injected. Scripts that need
# unavailable deps (24 telnetlib, 55 arvpyf, 85/86 spec) are excluded by
# omission. Config globals those device scripts would set (beamline_stage, the
# detector-enable flags) are seeded into the namespace before this phase (see
# :meth:`_seed_namespace`).
#
# Per-phase overrides via the CMS_PROFILE_KEEP / CMS_PROFILE_SAM_KEEP env vars
# (comma-separated prefixes, each fully REPLACING its default) — handy for
# tuning the SAM set against the live beamline without a code change. Setting
# CMS_PROFILE_SAM_KEEP empty disables SAM hosting (inject devices only).
DEFAULT_INFRA_KEEP = frozenset({"00", "01", "02", "03"})
DEFAULT_SAM_KEEP = frozenset({"90", "81", "82", "94", "95", "96", "97", "991"})


class ProfileSessionBootstrapper:
    """Hosts the CMS profile in the live kernel: runs infra → adopts the
    RunEngine + Tiled client → injects happi devices → runs the SAM framework."""

    def __init__(self, backend: Any = None) -> None:
        # The happi device backend, used to inject ophyd instances into the
        # kernel namespace so the SAM framework finds its device globals.
        self._backend = backend

    def _keep(self, phase: str) -> set[str]:
        """Numeric prefixes of profile scripts to run for *phase*.

        Honors a per-phase env override (``CMS_PROFILE_KEEP`` for infra,
        ``CMS_PROFILE_SAM_KEEP`` for sam), each fully REPLACING its default.
        """
        env_var = "CMS_PROFILE_KEEP" if phase == "infra" else "CMS_PROFILE_SAM_KEEP"
        default = DEFAULT_INFRA_KEEP if phase == "infra" else DEFAULT_SAM_KEEP
        env = os.environ.get(env_var)
        if env is not None:
            return {s.strip() for s in env.split(",") if s.strip()}
        return set(default)

    def _profile_scripts(self, keep: set[str]) -> list[Path]:
        """Ordered profile scripts whose prefix is in *keep*. BEAMLINE SEAM.

        Skips backup/experimental variants.
        """
        from lightfall_endstation_cms.loader import _get_profile_path

        startup = _get_profile_path()
        scripts: list[Path] = []
        for p in sorted(startup.glob("[0-9]*.py")):
            if p.name.endswith((".pybak", ".bak")) or "_new." in p.name:
                continue
            prefix = p.name.split("-")[0]
            if prefix not in keep:
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

    def run_profile(self, shell: Any, scripts: list[Path], label: str = "profile") -> None:
        """Execute the given profile *scripts* into the kernel shell (beamline)."""
        if not scripts:
            return
        logger.info("Running {} profile phase: {} scripts", label, len(scripts))
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

    def _inject_devices(self, namespace: dict[str, Any]) -> int:
        """Bind the happi device instances into the kernel namespace.

        The SAM framework (``81-beam``/``94-sample``/…) references devices by
        their profile variable names (``smx``, ``pilatus2M``, …). The happi DB's
        item names are exactly those (see cms_happi.json), so each device is
        bound under ``ns[name]``. Prefers the instance the backend already built
        in the background (so the GUI catalog and the console share one object);
        otherwise instantiates it via the happi client.

        Returns the number of devices injected.
        """
        backend = self._backend
        if backend is None:
            logger.warning("No device backend supplied; skipping kernel device injection")
            return 0

        injected = 0
        missing: list[str] = []
        for info in backend.list_devices(active_only=False):
            obj = getattr(info, "_ophyd_device", None)
            if obj is None:
                obj = self._instantiate_device(backend, info.name)
                if obj is not None:
                    # Share the instance back so the GUI catalog uses it too.
                    try:
                        info._ophyd_device = obj
                    except Exception:
                        pass
            if obj is None:
                missing.append(info.name)
                continue
            namespace[info.name] = obj
            injected += 1

        logger.info("Injected {} happi devices into the kernel namespace", injected)
        if missing:
            logger.warning(
                "{} device(s) had no instance to inject (still connecting or "
                "failed to construct): {}",
                len(missing), missing,
            )
        return injected

    @staticmethod
    def _instantiate_device(backend: Any, name: str) -> Any | None:
        """Instantiate one ophyd device via the backend's happi client, or None."""
        client = getattr(backend, "_client", None)
        if client is None:
            return None
        try:
            results = client.search(name=name)
            if results:
                return results[0].get()
        except Exception as exc:
            # Expected when an IOC is offline (the PV won't connect in time) —
            # warn and skip rather than dumping a traceback; the device is left
            # out of the kernel and the session continues.
            logger.warning("Could not instantiate device '{}' (IOC offline?): {}", name, exc)
        return None

    @staticmethod
    def _seed_namespace(namespace: dict[str, Any]) -> None:
        """Seed config globals the device scripts would have set.

        The SAM framework reads config values that are normally defined by the
        device-defining scripts we no longer run — ``beamline_stage`` (set in
        10-motors) selects the sample-stage PVs in 81-beam/94-sample, and the
        detector-enable flags are read by 20-area-detectors. Seed them (honoring
        env overrides) before the SAM phase so those modules resolve. Existing
        values are not overwritten.
        """
        def _flag(env: str, default: bool) -> bool:
            val = os.environ.get(env)
            return default if not val else val.lower() in ("1", "true", "yes", "on")

        seeds = {
            "beamline_stage": os.environ.get("CMS_BEAMLINE_STAGE", "default"),
            "Camera_on": _flag("CMS_CAMERA_ON", True),
            "Pilatus300_on": _flag("CMS_PILATUS300_ON", False),
            "Pilatus800_on": _flag("CMS_PILATUS800_ON", True),
            "Pilatus800_2_on": _flag("CMS_PILATUS800_2_ON", False),
            "Pilatus2M_on": _flag("CMS_PILATUS2M_ON", True),
        }
        for key, value in seeds.items():
            namespace.setdefault(key, value)
        logger.info("Seeded SAM config globals: beamline_stage={}", seeds["beamline_stage"])

    def bootstrap(self, shell: Any) -> bool:
        """Full handshake: run infra → adopt RE+Tiled → inject devices → run SAM.

        Returns whether adoption succeeded (the RunEngine was adopted). The SAM
        phase is best-effort: a module that fails is logged and skipped (the
        per-script handler in :meth:`run_profile`), so the session still comes
        up with RE + injected devices even if part of the framework doesn't load.
        """
        infra = self._profile_scripts(self._keep("infra"))
        self.run_profile(shell, infra, label="infra")

        if not self.adopt(shell.user_ns):
            return False

        # Devices + config globals first, so the SAM framework finds them on load.
        self._inject_devices(shell.user_ns)
        self._seed_namespace(shell.user_ns)

        sam = self._profile_scripts(self._keep("sam"))
        self.run_profile(shell, sam, label="sam")
        return True
