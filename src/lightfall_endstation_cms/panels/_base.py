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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.refresh()

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
