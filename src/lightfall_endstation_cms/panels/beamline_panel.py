"""CMS beamline-state panel — shutter/mode status and guarded controls.

Reflects the live ``beam``/``cms`` kernel objects (experimental-shutter state
and the beamline mode) and offers the common controls. Because these actions
**actuate hardware** — opening the shutter lets x-rays onto the sample; a mode
switch moves detectors and the beamstop — every actuating button is gated
behind an explicit confirmation, and (like the other CMS panels) is disabled
while a command is in flight. Status is read directly from the kernel objects
(a quick caget / attribute read); actions are issued through the console so
they appear in history exactly as if typed.
"""

from __future__ import annotations

from typing import ClassVar

from loguru import logger
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton

from lightfall.ui.panels.base import PanelMetadata

from lightfall_endstation_cms import kernel_access
from lightfall_endstation_cms.panels._base import CMSKernelPanel

_UNKNOWN = "—"


class CMSBeamlinePanel(CMSKernelPanel):
    """Experimental-shutter + beamline-mode status with confirmed controls."""

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.cms_beamline",
        name="CMS Beamline",
        description="Beam (shutter) and mode status with guarded controls",
        icon="radioactive",
        category="CMS",
        singleton=True,
        closable=True,
        keywords=["cms", "beam", "shutter", "mode", "beamline", "alignment", "measurement"],
        default_area="right",
        sidebar_group="top",
        auto_hide=True,
    )

    def _setup_ui(self) -> None:
        self._status_label = QLabel()
        self._layout.addWidget(self._status_label)

        grid = QGridLayout()
        grid.addWidget(QLabel("Beam (shutter):"), 0, 0)
        self._beam_value = QLabel(_UNKNOWN)
        grid.addWidget(self._beam_value, 0, 1)
        grid.addWidget(QLabel("Mode:"), 1, 0)
        self._mode_value = QLabel(_UNKNOWN)
        grid.addWidget(self._mode_value, 1, 1)
        grid.setColumnStretch(2, 1)
        self._layout.addLayout(grid)

        # Beam controls. Opening actuates the shutter (confirmed); closing is safe.
        beam_row = QHBoxLayout()
        self._open_btn = QPushButton("Open beam")
        self._open_btn.clicked.connect(self._on_open)
        self._close_btn = QPushButton("Close beam")
        self._close_btn.clicked.connect(self._on_close)
        beam_row.addWidget(self._open_btn)
        beam_row.addWidget(self._close_btn)
        beam_row.addStretch(1)
        self._layout.addLayout(beam_row)

        # Mode controls — both move hardware, so both are confirmed.
        mode_row = QHBoxLayout()
        self._align_btn = QPushButton("Alignment mode")
        self._align_btn.clicked.connect(self._on_alignment)
        self._measure_btn = QPushButton("Measurement mode")
        self._measure_btn.clicked.connect(self._on_measurement)
        mode_row.addWidget(self._align_btn)
        mode_row.addWidget(self._measure_btn)
        mode_row.addStretch(1)
        self._layout.addLayout(mode_row)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        actions.addWidget(self._refresh_btn)
        self._layout.addLayout(actions)

        self._layout.addStretch(1)

    def _action_widgets(self):
        return [self._open_btn, self._close_btn, self._align_btn, self._measure_btn]

    # --- status ----------------------------------------------------------

    def refresh(self) -> None:
        loaded = kernel_access.sam_is_loaded()
        if not loaded:
            self._status_label.setText("SAM framework not loaded — log in to the beamline.")
            self._beam_value.setText(_UNKNOWN)
            self._mode_value.setText(_UNKNOWN)
            self._set_controls_enabled(False)
            return

        self._status_label.setText("SAM loaded")
        self._beam_value.setText(self._read_beam_state())
        self._mode_value.setText(self._read_mode())
        self._set_controls_enabled(True)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for w in self._action_widgets():
            w.setEnabled(enabled)

    @staticmethod
    def _read_beam_state() -> str:
        beam = kernel_access.get_kernel_object("beam")
        if beam is None:
            return _UNKNOWN
        try:
            return "OPEN" if beam.is_on(verbosity=0) else "CLOSED"
        except Exception:
            logger.debug("CMSBeamlinePanel: beam.is_on() failed", exc_info=True)
            return _UNKNOWN

    @staticmethod
    def _read_mode() -> str:
        cms = kernel_access.get_kernel_object("cms")
        return str(getattr(cms, "current_mode", _UNKNOWN)) if cms is not None else _UNKNOWN

    # --- actions (actuating ones are confirmed) -------------------------

    def _confirm(self, action: str) -> bool:
        """Ask the operator to confirm an actuating action. Mockable in tests."""
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self, "Confirm beamline action", f"{action}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _on_open(self) -> None:
        if self._confirm("Open the experimental shutter (beam onto sample)"):
            self.run_guarded("beam.on()")

    def _on_close(self) -> None:
        # Closing the shutter is the safe direction — no confirmation.
        self.run_guarded("beam.off()")

    def _on_alignment(self) -> None:
        if self._confirm("Switch to ALIGNMENT mode (moves detectors/beamstop)"):
            self.run_guarded("cms.modeAlignment()")

    def _on_measurement(self) -> None:
        if self._confirm("Switch to MEASUREMENT mode (moves detectors/beamstop)"):
            self.run_guarded("cms.modeMeasurement()")
