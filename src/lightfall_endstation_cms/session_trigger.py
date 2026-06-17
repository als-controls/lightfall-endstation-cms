"""Run the CMS profile bootstrap once the user authenticates."""

from __future__ import annotations

from typing import Any

from loguru import logger

from lightfall.auth.session import AuthState, SessionManager

from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


class CMSSessionTrigger:
    """Arms a one-shot profile bootstrap on the first AUTHENTICATED transition."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._done = False

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
        if self._done or new_state != AuthState.AUTHENTICATED:
            return
        shell = self._get_shell()
        if shell is None:
            logger.error("CMS bootstrap: console shell unavailable; cannot run profile")
            return
        self._done = True
        logger.info("CMS login detected — running profile-collection bootstrap")
        ProfileSessionBootstrapper(self._backend).bootstrap(shell)
