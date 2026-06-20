"""Run the CMS profile bootstrap once the user authenticates."""

from __future__ import annotations

from typing import Any

from loguru import logger

from lightfall.auth.session import AuthState, SessionManager
from lightfall.utils.threads import invoke_in_main_thread

from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


class CMSSessionTrigger:
    """Arms a one-shot profile bootstrap on the first AUTHENTICATED transition.

    The bootstrap runs the profile's infrastructure scripts to adopt the live
    ``RunEngine`` + Tiled client, injects the happi devices into the kernel, and
    runs the SAM framework. The device backend is passed through so its ophyd
    instances can be injected under their profile variable names.
    """

    def __init__(self, backend: Any = None) -> None:
        self._backend = backend
        self._done = False        # a bootstrap has SUCCEEDED — stop retrying
        self._running = False     # a bootstrap is in progress — block re-entry

    def arm(self) -> None:
        SessionManager.get_instance().state_changed.connect(self._on_state_changed)
        logger.info("CMS session trigger armed (waiting for NSLS-II login)")

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

    def _on_state_changed(self, new_state: Any, old_state: Any = None) -> None:
        # SessionManager.state_changed emits (new_state, old_state); the second
        # arg is optional so tests can call with just the new state.
        if self._done or self._running or new_state != AuthState.AUTHENTICATED:
            return

        # CRITICAL: this signal is emitted from the BACKGROUND login thread
        # (SessionManager.attach_session runs in a QThreadFuture and calls
        # _set_state(AUTHENTICATED) there). The bootstrap creates QWidgets (the
        # IPython panel), starts an in-process kernel and imports qtconsole, and
        # pumps the Qt event loop — all of which MUST happen on the GUI thread.
        # Running it on the login thread (a) is illegal Qt cross-thread widget
        # creation and (b) races the main thread's proactive panel-init import
        # of qtconsole, deadlocking on the Python import lock (observed on ws5:
        # MainThread stuck in importlib acquire under IPythonPanel._setup_ui).
        # Marshal the whole bootstrap onto the main thread.
        invoke_in_main_thread(self._run_bootstrap)

    def _run_bootstrap(self) -> None:
        """Run the profile bootstrap. MUST execute on the GUI thread."""
        # Re-check on the main thread: several AUTHENTICATED emits could have
        # been marshaled before the first ran.
        if self._done or self._running:
            return

        shell = self._get_shell()
        if shell is None:
            logger.error("CMS bootstrap: console shell unavailable; cannot run profile")
            return  # not done — allow a retry on a later AUTHENTICATED

        logger.info("CMS login detected — running profile-collection bootstrap")
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
            # Leave _done False so fixing the cause and re-logging in retries.
            logger.error("CMS profile bootstrap did not complete; re-login to retry")
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
