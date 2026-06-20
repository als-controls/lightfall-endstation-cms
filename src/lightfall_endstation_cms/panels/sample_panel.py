"""CMS sample panel — a GUI veneer over the live kernel SAM objects.

This is the first CMS-specialized PanelPlugin and the template for the rest. It
does NOT reimplement any measurement logic: it discovers the ``Sample`` objects
hosted in the live IPython kernel (see :mod:`lightfall_endstation_cms.kernel_access`)
and drives them through the console, so a button press is identical to the
operator typing the command — GUI and console share the same objects and state.

Scope (intentionally minimal scaffolding):
    * show whether the SAM framework is loaded (``cms`` present);
    * list the ``Sample`` instances found in the kernel (by base class, so the
      beamtime's variable names don't need to be known);
    * snap / measure the selected sample via the console.

Long-running actions currently run on the GUI thread via the console (like the
operator typing them); routing measurement through the engine for a
non-blocking, cancellable run is a follow-up.
"""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QWidget,
)

from lightfall.ui.panels.base import BasePanel, PanelMetadata

from lightfall_endstation_cms import kernel_access

# SAM base classes whose instances are "samples" (MRO match, no import needed).
_SAMPLE_BASES = ("Sample_Generic",)


class CMSSamplePanel(BasePanel):
    """Lists kernel-resident CMS samples and snaps/measures the selected one."""

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.cms_sample",
        name="CMS Samples",
        description="Snap/measure CMS samples hosted in the IPython kernel",
        icon="flask",
        category="CMS",
        singleton=True,
        closable=True,
        keywords=["cms", "sample", "measure", "snap", "sam"],
        default_area="right",
        sidebar_group="top",
        auto_hide=True,
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.refresh()

    def _setup_ui(self) -> None:
        self._status = QLabel()
        self._layout.addWidget(self._status)

        self._samples = QListWidget()
        self._samples.currentItemChanged.connect(lambda *_: self._update_actions())
        self._layout.addWidget(self._samples)

        # Exposure time for measure().
        exp_row = QHBoxLayout()
        exp_row.addWidget(QLabel("Exposure (s):"))
        self._exposure = QDoubleSpinBox()
        self._exposure.setRange(0.0, 3600.0)
        self._exposure.setDecimals(3)
        self._exposure.setValue(1.0)
        exp_row.addWidget(self._exposure)
        exp_row.addStretch(1)
        self._layout.addLayout(exp_row)

        # Actions.
        actions = QHBoxLayout()
        self._snap_btn = QPushButton("Snap")
        self._snap_btn.clicked.connect(self._on_snap)
        self._measure_btn = QPushButton("Measure")
        self._measure_btn.clicked.connect(self._on_measure)
        actions.addWidget(self._snap_btn)
        actions.addWidget(self._measure_btn)
        actions.addStretch(1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        actions.addWidget(self._refresh_btn)
        self._layout.addLayout(actions)

        self._layout.addStretch(1)

    # --- behaviour -------------------------------------------------------

    def refresh(self) -> None:
        """Re-scan the live kernel for SAM state and Sample instances."""
        loaded = kernel_access.sam_is_loaded()
        samples = kernel_access.find_kernel_objects(*_SAMPLE_BASES) if loaded else {}

        if not loaded:
            self._status.setText(
                "SAM framework not loaded — log in to the beamline to host it."
            )
        else:
            self._status.setText(
                f"SAM loaded · {len(samples)} sample(s) in the kernel"
            )

        selected = self._current_sample_name()
        self._samples.clear()
        for name in sorted(samples):
            self._samples.addItem(name)
        # Restore selection if still present.
        if selected:
            matches = self._samples.findItems(selected, _exact_match())
            if matches:
                self._samples.setCurrentItem(matches[0])
        self._update_actions()

    def _current_sample_name(self) -> str | None:
        item = self._samples.currentItem() if hasattr(self, "_samples") else None
        return item.text() if item is not None else None

    def _update_actions(self) -> None:
        enabled = self._current_sample_name() is not None and kernel_access.sam_is_loaded()
        self._snap_btn.setEnabled(enabled)
        self._measure_btn.setEnabled(enabled)

    def _on_snap(self) -> None:
        name = self._current_sample_name()
        if name:
            kernel_access.execute_in_console(f"{name}.snap()")

    def _on_measure(self) -> None:
        name = self._current_sample_name()
        if name:
            exposure = self._exposure.value()
            kernel_access.execute_in_console(f"{name}.measure({exposure})")


def _exact_match():
    """Qt.MatchFlag.MatchExactly, imported lazily to keep import light."""
    from PySide6.QtCore import Qt

    return Qt.MatchFlag.MatchExactly
