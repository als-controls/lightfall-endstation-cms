"""Run the CMS profile bootstrap once device connections settle + kernel is ready."""

from __future__ import annotations

from typing import Any

from lightfall.utils.threads import invoke_in_main_thread
from loguru import logger

from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


class CMSSessionTrigger:
    """Fire the CMS profile bootstrap once the device-connection batch settles.

    The CMS device catalog is a happi backend in ``instantiate="background"``
    mode, so the :class:`DeviceConnectionManager` instantiates and connects every
    device on worker threads and emits ``all_connections_complete`` when the
    batch has fully drained (every device ONLINE/OFFLINE/TIMEOUT). That signal is
    the "devices are loaded" gate.

    On it we run :class:`ProfileSessionBootstrapper` on the GUI thread, which
    ensures the IPython kernel ("kernel is loaded") and binds the live ophyd
    objects into the kernel namespace before running the SAM scripts.  The kernel
    is created by the IPython panel itself (``ensure_kernel`` is synchronous on
    the GUI thread); we never build a competing kernel.

    A deadline timer is the safety net: if the completion signal never arrives
    (e.g. a backend load that errors before kicking ``connect_devices``), the
    bootstrap still runs in degraded mode so the session is never silently
    blocked.

    Note: ``all_connections_complete`` comes from the shared
    DeviceConnectionManager singleton.  At CMS the happi backend is the only
    background-mode backend, so the first completion is the CMS batch.  If other
    background backends are added later, scope the gate to this backend's devices.
    """

    def __init__(self, backend: Any = None) -> None:
        self._backend = backend
        self._done = False        # a bootstrap has SUCCEEDED — stop retrying
        self._running = False     # a bootstrap is in progress — block re-entry
        self._timer: Any = None   # deadline QTimer (single-shot), created in arm()
        self._manager: Any = None  # held ref to the connection manager singleton
        self._subscribed = False  # whether _on_devices_loaded is connected

    def arm(self, *, timeout_s: float = 60.0) -> None:
        """Subscribe to the devices-loaded gate and start the degraded deadline.

        Args:
            timeout_s: Seconds before the bootstrap fires in degraded mode if the
                connection batch never reports completion.
        """
        self._subscribe()
        self._start_deadline(timeout_s)
        logger.info(
            "CMS session trigger armed — waiting for device connections to settle "
            "(degraded bootstrap after {}s)",
            timeout_s,
        )

    # === Gate wiring ===

    def _subscribe(self) -> None:
        """Connect ``_on_devices_loaded`` to the manager's batch-complete signal.

        Re-arm (the retry path) must not leave a duplicate subscription, so any
        prior connection is torn down first.  A missing connection manager is the
        one genuinely-expected absence (headless unit tests): there the caller
        drives ``_on_devices_loaded()`` / ``_on_deadline()`` directly.
        """
        self._unsubscribe()
        try:
            from lightfall.devices.connection_manager import DeviceConnectionManager

            manager = DeviceConnectionManager.get_instance()
        except Exception:
            logger.exception(
                "Could not reach DeviceConnectionManager; the SAM bootstrap will "
                "rely on the degraded-mode deadline only"
            )
            return
        manager.all_connections_complete.connect(self._on_devices_loaded)
        self._manager = manager
        self._subscribed = True

    def _unsubscribe(self) -> None:
        if self._manager is not None and self._subscribed:
            try:
                self._manager.all_connections_complete.disconnect(self._on_devices_loaded)
            except Exception:
                pass
        self._subscribed = False

    def _start_deadline(self, timeout_s: float) -> None:
        # Tear down any existing timer so re-arm does not leak a ticking orphan.
        self._stop_deadline()
        try:
            from qtpy.QtCore import QTimer
        except ImportError:
            self._timer = None
            return
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setInterval(int(timeout_s * 1000))
        timer.timeout.connect(self._on_deadline)
        timer.start()
        self._timer = timer

    def _stop_deadline(self) -> None:
        if self._timer is not None:
            try:
                self._timer.stop()
                self._timer.timeout.disconnect(self._on_deadline)
            except Exception:
                pass
            self._timer = None

    # === Gate callbacks ===

    def _on_devices_loaded(self) -> None:
        """All device connections have settled — fire the bootstrap (nominal)."""
        if self._done or self._running:
            return
        self._stop_deadline()
        logger.info("CMS device connections settled — running profile bootstrap")
        self._fire()

    def _on_deadline(self) -> None:
        """Deadline elapsed before the batch settled — fire in degraded mode."""
        if self._done or self._running:
            return
        logger.warning(
            "CMS session trigger: device connections did not settle before the "
            "deadline; running the profile bootstrap in degraded mode"
        )
        self._fire()

    # === Bootstrap execution (GUI thread) ===

    def _fire(self) -> None:
        """Marshal the bootstrap onto the GUI thread (once-only).

        CRITICAL: the bootstrap ensures the IPython panel/kernel, imports
        qtconsole and pumps the Qt event loop — all of which MUST happen on the
        GUI thread.  ``all_connections_complete`` is already delivered on the GUI
        thread, but the deadline timer and direct test calls may not be, so the
        marshal is kept defensively.
        """
        if self._done or self._running:
            return
        invoke_in_main_thread(self._run_bootstrap)

    def _get_shell(self) -> Any | None:
        """Obtain the live console shell, creating the kernel if needed."""
        from lightfall.core import LFApplication

        app = LFApplication.get_instance()
        window = app.main_window if app else None
        if window is None:
            return None
        panel = window.get_panel("lightfall.panels.ipython") or window.add_panel(
            "lightfall.panels.ipython"
        )
        if panel is None or not panel.ensure_kernel():
            return None
        return panel.shell

    def _run_bootstrap(self) -> None:
        """Run the profile bootstrap. MUST execute on the GUI thread."""
        # Re-check on the main thread: several _fire() calls could have been
        # marshaled before the first ran.
        if self._done or self._running:
            return

        shell = self._get_shell()
        if shell is None:
            logger.error("CMS bootstrap: console shell unavailable; cannot run profile")
            return  # not done — the caller may retry by calling arm() again

        logger.info("CMS devices loaded — running profile-collection bootstrap")
        self._running = True
        try:
            ok = ProfileSessionBootstrapper(self._backend).bootstrap(shell)
        except Exception:
            logger.exception("CMS profile bootstrap raised")
            ok = False
        finally:
            self._running = False

        if ok:
            # Succeeded once — never re-run for this app session.
            self._done = True
            self._unsubscribe()
            self._stop_deadline()
            logger.info("CMS profile bootstrap complete")
        else:
            # Leave _done False so re-arming / retrying is possible.
            logger.error("CMS profile bootstrap did not complete")
            self._notify_failure()

    @staticmethod
    def _notify_failure() -> None:
        """Surface a bootstrap failure to the operator (best-effort)."""
        try:
            from lightfall.ui.toast import ToastManager

            ToastManager.get_instance().error(
                "CMS profile load failed",
                "The beamline profile did not load (see logs). Re-login to retry.",
            )
        except Exception:
            logger.debug("Could not show CMS bootstrap failure toast", exc_info=True)
