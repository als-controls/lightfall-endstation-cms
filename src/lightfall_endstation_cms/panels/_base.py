"""Shared base for CMS panels that drive the live kernel SAM objects.

Centralizes the access pattern (via :mod:`lightfall_endstation_cms.kernel_access`)
and the in-flight guard: while a console command runs, the panel's action
widgets are disabled so an impatient second click can't re-enter the busy
kernel. After the command returns (the ``ConsoleREProxy`` blocks until the plan
finishes while pumping Qt), :meth:`refresh` re-reads kernel state and restores
the correct enabled/disabled state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from lightfall.ui.panels.base import BasePanel

from lightfall_endstation_cms import kernel_access

if TYPE_CHECKING:
    from PySide6.QtWidgets import QAbstractButton


class CMSKernelPanel(BasePanel):
    """BasePanel that issues guarded commands to the live kernel SAM objects."""

    # SAM is hosted by the devices-live bootstrap, which finishes AFTER this
    # panel loads (post-login). Poll until it appears so the panel flips from
    # "not loaded" to live without a manual Refresh. The poll stops as soon as
    # SAM is up (or after the cap, beyond which the Refresh button still works).
    _SAM_POLL_MS = 3000
    _SAM_POLL_CAP_MS = 600000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sam_poll_timer: object | None = None
        self._sam_poll_elapsed_ms = 0
        self.refresh()
        if not kernel_access.sam_is_loaded():
            self._start_sam_ready_poll()

    def _start_sam_ready_poll(self) -> None:
        """Begin polling for SAM hosting (best-effort; no-op without a Qt loop)."""
        if self._sam_poll_timer is not None:
            return
        try:
            from PySide6.QtCore import QTimer
        except Exception:
            return
        self._sam_poll_timer = QTimer(self)
        self._sam_poll_timer.setInterval(self._SAM_POLL_MS)
        self._sam_poll_timer.timeout.connect(self._on_sam_ready_poll)
        self._sam_poll_timer.start()

    def _on_sam_ready_poll(self) -> None:
        """Flip the panel to live once SAM is hosted; give up after the cap."""
        self._sam_poll_elapsed_ms += self._SAM_POLL_MS
        if kernel_access.sam_is_loaded():
            self._stop_sam_ready_poll()
            self.refresh()
        elif self._sam_poll_elapsed_ms >= self._SAM_POLL_CAP_MS:
            self._stop_sam_ready_poll()

    def _stop_sam_ready_poll(self) -> None:
        if self._sam_poll_timer is not None:
            self._sam_poll_timer.stop()
            self._sam_poll_timer = None

    def _action_widgets(self) -> list[QAbstractButton]:
        """Action buttons to disable while a console command is in flight.

        Subclasses override to list their action buttons (not Refresh).
        """
        return []

    def refresh(self) -> None:
        """Re-read kernel state and update the UI. Subclasses override."""

    def run_guarded(self, code: str) -> bool:
        """Run *code* in the console with action widgets disabled while busy.

        Returns whether the command was dispatched (False if no kernel).
        """
        widgets = self._action_widgets()
        for w in widgets:
            w.setEnabled(False)
        try:
            return kernel_access.execute_in_console(code)
        finally:
            # The kernel state may have changed; re-scan and let refresh()
            # restore the correct enabled/disabled state of the actions.
            self.refresh()
