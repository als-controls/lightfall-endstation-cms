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

    def run_profile(
        self, shell: Any, scripts: list[Path], label: str = "profile", after_each: Any = None
    ) -> None:
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
                if after_each is not None:
                    try:
                        after_each(script, shell.user_ns)
                    except Exception:
                        logger.exception("after_each hook failed after {}", script.name)
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
        bound under ``ns[name]``. Since the backend runs in ``instantiate="none"``
        mode (so 00-startup's ``set_defaults`` runs first), each device is built
        here via the happi client.

        Each freshly built instance is also pushed into the DeviceCatalog via
        ``mark_device_live`` so the catalog/UI reflect that it is live instead of
        staying UNKNOWN. These devices bypass the DeviceConnectionManager (which
        only runs in the backend's "background" mode), so without this the GUI
        device tree would never leave the UNKNOWN state. ``mark_device_live`` only
        updates in-memory state + emits signals — it does NOT write through to the
        happi JSON (unlike ``update_device``).

        Returns the number of devices injected.
        """
        backend = self._backend
        if backend is None:
            logger.warning("No device backend supplied; skipping kernel device injection")
            return 0

        catalog = self._device_catalog()

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
            # Notify the catalog so the device tree shows the live state instead
            # of UNKNOWN (these instances never went through the connection
            # manager). Best-effort: a missing/older catalog API must not abort
            # injection.
            if catalog is not None:
                try:
                    catalog.mark_device_live(info.id, obj)
                except Exception:
                    logger.debug(
                        "mark_device_live failed for '{}'", info.name, exc_info=True
                    )

        logger.info("Injected {} happi devices into the kernel namespace", injected)
        if missing:
            logger.warning(
                "{} device(s) had no instance to inject (still connecting or "
                "failed to construct): {}",
                len(missing), missing,
            )
        return injected

    @staticmethod
    def _device_catalog() -> Any | None:
        """The DeviceCatalog singleton, or None if unavailable (e.g. tests)."""
        try:
            from lightfall.devices import DeviceCatalog

            return DeviceCatalog.get_instance()
        except Exception:
            logger.debug("DeviceCatalog unavailable; injected devices won't update UI")
            return None

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

    def adopt_reexpressed_infra(self, namespace: dict[str, Any]) -> bool:
        """Re-express 00-startup's infra onto Lightfall's RE; seed the namespace.

        Replaces running the 00-03 infra scripts and adopting a separate profile
        RunEngine (spec section 3). Attaches configure_base's bits -- redis
        ``RE.md``, the kafka publisher, ``SupplementalData``, the ``assets_path``
        hook, and the tiled document writer -- to ``get_engine().RE`` via the
        standalone helpers, then seeds the kernel namespace with the names the
        SAM scripts reference (``RE``, ``cat``/``db``/``mig``/
        ``tiled_writing_client``, ``assets_path``/``proposal_path``, ``sd``).

        Every step is best-effort (the helpers never raise); only a missing
        RunEngine -- nothing to seed -- returns False.
        """
        from lightfall_endstation_cms.assets import (
            assets_path,
            proposal_path,
            wire_assets_path,
        )
        from lightfall_endstation_cms.kafka_publisher import wire_kafka_publisher
        from lightfall_endstation_cms.run_engine_md import wire_redis_metadata
        from lightfall_endstation_cms.supplemental_data import wire_supplemental_data
        from lightfall_endstation_cms.tiled_writer import wire_tiled_writer

        engine = get_engine()
        if getattr(engine, "RE", None) is None:
            logger.error("Lightfall engine has no RE; cannot wire CMS infra")
            return False

        # Attach configure_base's bits to Lightfall's own RE (all idempotent).
        wire_redis_metadata()
        wire_kafka_publisher()
        sd = wire_supplemental_data()
        wire_assets_path()
        wire_tiled_writer()

        # Console RE is a GUI-safe proxy (as the old adopt() did).
        namespace["RE"] = ConsoleREProxy(engine)
        namespace.setdefault("assets_path", assets_path)
        namespace.setdefault("proposal_path", proposal_path)
        if sd is not None:
            namespace.setdefault("sd", sd)

        # Seed the Tiled read clients (cat/db/mig/tiled_writing_client).
        self._seed_tiled_namespace(namespace)

        # Seed the module-level imports the infra scripts (00-03, not run under
        # Arch B) leak into the shared namespace; SAM scripts reference them
        # un-imported (e.g. 90-bluesky's os.path.join).
        self._seed_profile_imports(namespace)

        # Seed device CLASSES the SAM scripts build with (e.g. 81-beam's
        # CMS_Beamline_XR uses TriState vacuum valves / StandardProsilica
        # cameras) that come from the device-defining scripts (10-52) we skip.
        self._seed_device_classes(namespace)

        # Seed the bare ophyd names (EpicsSignal, Device, Cpt, ...) the SAM
        # scripts use but never import (81-beam has zero top-level imports).
        self._seed_ophyd_names(namespace)

        logger.info(
            "Re-expressed CMS infra onto Lightfall's RE and seeded the namespace"
        )
        return True

    @staticmethod
    def _seed_tiled_namespace(namespace: dict[str, Any]) -> None:
        """Seed cat/db/mig/tiled_writing_client from the CMS service key.

        Mirrors 00-startup: ``tiled_reading_client = cat = from_profile(...)
        ["cms"]["raw"]``, ``mig = [...]["cms/migration"]``, ``db = Broker(cat)``.
        Uses the service API key (sees all cms/raw records). Best-effort: a
        missing key or unreachable server must not abort the bootstrap.
        """
        api_key = os.environ.get("TILED_BLUESKY_WRITING_API_KEY_CMS")
        if not api_key:
            logger.warning(
                "TILED_BLUESKY_WRITING_API_KEY_CMS not set; SAM cat/db/mig lookups "
                "will be unavailable"
            )
            return
        try:
            from tiled.client import from_uri

            client = from_uri(TILED_URI, api_key=api_key)
            cat = client["cms"]["raw"]
            namespace.setdefault("tiled_reading_client", cat)
            namespace.setdefault("cat", cat)
            namespace.setdefault("tiled_writing_client", cat)
            try:
                namespace.setdefault("mig", client["cms/migration"])
            except Exception:
                logger.debug("cms/migration node unavailable", exc_info=True)
            try:
                from databroker import Broker

                namespace.setdefault("db", Broker(cat))
            except Exception:
                logger.debug(
                    "databroker.Broker unavailable; 'db' not seeded", exc_info=True
                )
            logger.info("Seeded Tiled read clients (cat/db/mig) for the SAM namespace")
        except Exception:
            logger.exception(
                "Could not seed Tiled read clients; SAM cat/db/mig lookups will be "
                "unavailable"
            )

    @staticmethod
    def _seed_profile_imports(namespace: dict[str, Any]) -> None:
        """Seed the module-level imports 00-03 leak into the shared namespace.

        The SAM scripts were written to run after the infra scripts in one
        IPython namespace and reference modules those scripts import at top level
        -- ``os.path.join`` in 90-bluesky, etc. -- without re-importing. Arch B
        does not run 00-03, so replicate the common leaked ``import`` modules.
        Best-effort and non-overwriting (a script that imports its own wins).
        """
        import importlib

        # The plain ``import X`` modules 00-03 establish (see their top-of-file
        # imports). ``from ... import *`` helpers (pyOlog) are not replicated;
        # box validation surfaces any that a SAM script actually needs.
        for mod in (
            "os",
            "numpy",
            "ophyd",
            "asyncio",
            "queue",
            "threading",
            "contextlib",
        ):
            if mod in namespace:
                continue
            try:
                namespace[mod] = importlib.import_module(mod)
            except Exception:
                logger.debug(
                    "Could not seed profile import '{}'", mod, exc_info=True
                )

    @staticmethod
    def _redirect_config_paths(script: "Path", namespace: dict[str, Any]) -> None:
        """Point 90-bluesky's CMS_CONFIG_FILENAME at a readable copy off-account.

        90-bluesky hardcodes the config under xf11bm's home; it is first *read*
        by 97-user's config_load(). Production runs as xf11bm (readable, so this
        is a no-op there). When Lightfall runs as another account (e.g. rpandolfi
        for dev/validation) that path is unreadable, so after 90 defines it and
        before 97 reads it, redirect to the world-readable shared copy. No
        profile edit; the override is purely in the live namespace.
        """
        if not script.name.startswith("90-"):
            return
        path = namespace.get("CMS_CONFIG_FILENAME")
        if not path or os.access(path, os.R_OK):
            return  # readable (e.g. running as xf11bm) -> keep the profile's path
        fallback = os.environ.get(
            "CMS_CONFIG_FILENAME_FALLBACK",
            "/nsls2/data/cms/shared/config/bluesky/profile_collection/startup/.cms_config",
        )
        if os.access(fallback, os.R_OK):
            namespace["CMS_CONFIG_FILENAME"] = fallback
            logger.warning(
                "CMS_CONFIG_FILENAME {} not readable as this user; redirected to "
                "the shared copy {} (config_save will be read-only off-account)",
                path,
                fallback,
            )
        else:
            logger.error(
                "CMS_CONFIG_FILENAME {} not readable and no readable fallback at "
                "{}; config_load() will fail",
                path,
                fallback,
            )

    # Device classes the SAM scripts instantiate directly (the device-defining
    # scripts 10-52 are not run; happi provides instances, but 81-beam still
    # builds CMS_Beamline_XR from these CLASSES). Sourced from our happi device
    # modules. Confirmed by scanning the SAM set: only 81-beam references them.
    _DEVICE_CLASS_SEEDS = {
        "lightfall_endstation_cms.devices.shutters": ("TriState", "TwoButtonShutterNC"),
        "lightfall_endstation_cms.devices.area_detectors": ("StandardProsilica",),
    }

    @classmethod
    def _seed_device_classes(cls, namespace: dict[str, Any]) -> None:
        """Seed the device classes the SAM scripts reference into the namespace.

        81-beam instantiates these to build the beamline hierarchy (vacuum
        valves, diagnostic cameras). Importing the modules defines classes only
        -- no ophyd instances, so no CA connections here. Best-effort and
        non-overwriting (a script that defines its own name wins).
        """
        import importlib

        for mod_name, names in cls._DEVICE_CLASS_SEEDS.items():
            try:
                mod = importlib.import_module(mod_name)
            except Exception:
                logger.exception(
                    "Could not import {} to seed device classes", mod_name
                )
                continue
            for name in names:
                if name in namespace:
                    continue
                obj = getattr(mod, name, None)
                if obj is None:
                    logger.warning("{} has no '{}' to seed", mod_name, name)
                    continue
                namespace[name] = obj
        logger.info("Seeded device classes for the SAM scripts")

    # ophyd names the SAM scripts reference but never import. 81-beam (zero
    # top-level imports) inherited these from the legacy device scripts'
    # `from ophyd import ...`. Confirmed by scanning the SAM set -- only these
    # are referenced (the areaDetector/Signal/etc. names live only in the
    # skipped device-defining scripts). (attr_on_ophyd, name_in_namespace).
    _OPHYD_NAME_SEEDS = (
        ("EpicsSignal", "EpicsSignal"),
        ("EpicsSignalRO", "EpicsSignalRO"),
        ("EpicsMotor", "EpicsMotor"),
        ("Device", "Device"),
        ("Component", "Component"),
        ("Component", "Cpt"),
    )

    @classmethod
    def _seed_ophyd_names(cls, namespace: dict[str, Any]) -> None:
        """Seed the ophyd classes the SAM scripts reference un-imported.

        Best-effort and non-overwriting (a script importing its own name wins).
        """
        try:
            import ophyd
        except Exception:
            logger.exception("Could not import ophyd to seed its names")
            return
        for attr, alias in cls._OPHYD_NAME_SEEDS:
            if alias in namespace:
                continue
            obj = getattr(ophyd, attr, None)
            if obj is None:
                logger.warning("ophyd has no '{}' to seed", attr)
                continue
            namespace[alias] = obj
        logger.info("Seeded ophyd names for the SAM scripts")

    def bootstrap(self, shell: Any) -> bool:
        """Full handshake: run infra → adopt RE+Tiled → inject devices → run SAM.

        Returns whether adoption succeeded (the RunEngine was adopted). The SAM
        phase is best-effort: a module that fails is logged and skipped (the
        per-script handler in :meth:`run_profile`), so the session still comes
        up with RE + injected devices even if part of the framework doesn't load.
        """
        if not self.adopt_reexpressed_infra(shell.user_ns):
            return False

        # Devices + config globals first, so the SAM framework finds them on load.
        self._inject_devices(shell.user_ns)
        self._seed_namespace(shell.user_ns)

        sam = self._profile_scripts(self._keep("sam"))
        self.run_profile(shell, sam, label="sam", after_each=self._redirect_config_paths)
        return True
