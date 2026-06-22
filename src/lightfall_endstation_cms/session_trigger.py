"""Run the CMS profile bootstrap once all named happi devices are live."""

from __future__ import annotations

import time
from typing import Any

from lightfall.utils.threads import invoke_in_main_thread
from loguru import logger

from lightfall_endstation_cms import kernel_access
from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


class CMSSessionTrigger:
    """Arms a one-shot profile bootstrap once the named CMS devices are live.

    Instead of gating on the ``AUTHENTICATED`` SessionManager transition (which
    fires before the device catalog has finished background-instantiating ophyd
    objects), this trigger polls :func:`~lightfall_endstation_cms.kernel_access
    .devices_by_name` at a configurable interval until all requested devices
    report a live ``._ophyd_device``, then fires the bootstrap.

    If the deadline passes before all devices become live the bootstrap still
    runs in *degraded* mode (some devices may be offline) so the session is
    never silently blocked.

    The device backend is passed through so its ophyd instances can be injected
    under their profile variable names.
    """

    def __init__(self, backend: Any = None) -> None:
        self._backend = backend
        self._done = False        # a bootstrap has SUCCEEDED — stop retrying
        self._running = False     # a bootstrap is in progress — block re-entry

        # Set by arm(); None means arm() has not been called yet.
        self._device_names: list[str] | None = None
        self._deadline: float = 0.0
        self._timer: Any = None   # QTimer instance, created lazily in arm()

        # Injectable clock: tests override this to simulate elapsed time without
        # sleeping.  The default is time.monotonic (never wall-clock, so it is
        # unaffected by NTP adjustments and always strictly increasing).
        self._now = time.monotonic

    def arm(
        self,
        device_names: list[str],
        *,
        poll_ms: int = 500,
        timeout_s: float = 60.0,
    ) -> None:
        """Start polling for device liveness.

        Args:
            device_names: Happi item names to wait for.  The bootstrap fires
                when every name in this list has a live ophyd object, or when
                the timeout expires (degraded).
            poll_ms: Timer interval in milliseconds.
            timeout_s: Seconds before the trigger fires in degraded mode even
                if some devices are still missing.
        """
        self._device_names = list(device_names)
        self._deadline = self._now() + timeout_s

        # Re-arm (the documented retry path) must not leak the previous timer:
        # tear down any existing one before creating a new one, or the orphan
        # keeps ticking _poll forever (it can never stop itself once self._timer
        # is overwritten).
        if self._timer is not None:
            try:
                self._timer.stop()
                self._timer.timeout.disconnect(self._poll)
            except Exception:
                pass
            self._timer = None

        # Create a QTimer that calls _poll at each interval. Only the import is
        # guarded: a missing Qt binding (headless / pure unit test) is the one
        # genuinely-expected absence — there the caller drives _poll() manually.
        # QTimer construction itself does NOT need a running event loop, so any
        # failure there is a real bug and must surface loudly rather than
        # silently leaving the gate disarmed (SAM would never run).
        try:
            from qtpy.QtCore import QTimer
        except ImportError:
            self._timer = None
        else:
            self._timer = QTimer()
            self._timer.setInterval(poll_ms)
            self._timer.timeout.connect(self._poll)
            self._timer.start()

        logger.info(
            "CMS session trigger armed — waiting for {} device(s): {}",
            len(self._device_names),
            ", ".join(self._device_names),
        )

    def _poll(self) -> None:
        """Single poll step — called by QTimer or directly by tests.

        Checks device liveness and fires the bootstrap when all devices are live
        or the deadline has passed.  Safe to call before ``arm()`` (no-op).
        """
        if self._done or self._running:
            # Already fired or in progress — stop the timer and return.
            if self._timer is not None:
                try:
                    self._timer.stop()
                except Exception:
                    pass
            return

        if self._device_names is None:
            # arm() has not been called yet.
            return

        live = kernel_access.devices_by_name(self._device_names)
        missing = [n for n in self._device_names if n not in live]

        if not missing:
            # All requested devices are live — nominal path.
            if self._timer is not None:
                try:
                    self._timer.stop()
                except Exception:
                    pass
            self._fire()
            return

        if self._now() >= self._deadline:
            # Timed out waiting — fire in degraded mode anyway so the session
            # is never silently blocked by an offline device.
            if self._timer is not None:
                try:
                    self._timer.stop()
                except Exception:
                    pass
            logger.warning(
                "CMS session trigger: timed out waiting for device(s): {}. "
                "Running bootstrap in degraded mode.",
                ", ".join(missing),
            )
            self._fire()
            return

        # Not all live yet and deadline not reached — wait for the next tick.

    def _fire(self) -> None:
        """Marshal the bootstrap onto the GUI thread (once-only).

        CRITICAL: the bootstrap creates QWidgets (the IPython panel), starts an
        in-process kernel and imports qtconsole, and pumps the Qt event loop —
        all of which MUST happen on the GUI thread.  Running it on any other
        thread (a) is illegal Qt cross-thread widget creation and (b) races the
        main thread's proactive panel-init import of qtconsole, deadlocking on
        the Python import lock (observed on ws5: MainThread stuck in importlib
        acquire under IPythonPanel._setup_ui).  Marshal the whole bootstrap
        onto the main thread.
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

        logger.info("CMS devices live — running profile-collection bootstrap")
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
