"""CMS holder panel — a slot map over the live kernel ``Holder`` objects.

Reads the holder's ``_samples`` (a ``{slot: Sample}`` dict) to show which
sample sits in each slot, and drives ``gotoSample`` through the console. Like
the sample panel, it reimplements nothing: it reflects and drives the same
``Holder`` instances the operator uses in the console.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListWidget, QPushButton

from lightfall.ui.panels.base import PanelMetadata

from lightfall_endstation_cms import kernel_access
from lightfall_endstation_cms.panels._base import CMSKernelPanel

# CapillaryHolder/PositionalHolder/… all derive from Holder (MRO match).
_HOLDER_BASES = ("Holder",)


class CMSHolderPanel(CMSKernelPanel):
    """Shows the selected holder's slot→sample map and navigates to a slot."""

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.cms_holder",
        name="CMS Holder",
        description="Sample-holder slot map; go to a slot via the kernel",
        icon="view-grid",
        category="CMS",
        singleton=True,
        closable=True,
        keywords=["cms", "holder", "bar", "slot", "sample", "goto"],
        default_area="right",
        sidebar_group="top",
        auto_hide=True,
    )

    def _setup_ui(self) -> None:
        self._holders: dict[str, Any] = {}
        self._status_label = QLabel()
        self._layout.addWidget(self._status_label)

        row = QHBoxLayout()
        row.addWidget(QLabel("Holder:"))
        self._holder_combo = QComboBox()
        self._holder_combo.currentTextChanged.connect(lambda *_: self._update_slots())
        row.addWidget(self._holder_combo, 1)
        self._layout.addLayout(row)

        self._slots = QListWidget()
        self._slots.currentItemChanged.connect(lambda *_: self._update_actions())
        self._layout.addWidget(self._slots)

        actions = QHBoxLayout()
        self._goto_btn = QPushButton("Go to sample")
        self._goto_btn.clicked.connect(self._on_goto)
        actions.addWidget(self._goto_btn)
        actions.addStretch(1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        actions.addWidget(self._refresh_btn)
        self._layout.addLayout(actions)

        self._layout.addStretch(1)

    def _action_widgets(self):
        return [self._goto_btn]

    # --- behaviour -------------------------------------------------------

    def refresh(self) -> None:
        loaded = kernel_access.sam_is_loaded()
        self._holders = kernel_access.find_kernel_objects(*_HOLDER_BASES) if loaded else {}

        if not loaded:
            self._status_label.setText("SAM framework not loaded — log in to the beamline.")
        else:
            self._status_label.setText(f"{len(self._holders)} holder(s) in the kernel")

        current = self._holder_combo.currentText()
        self._holder_combo.blockSignals(True)
        self._holder_combo.clear()
        self._holder_combo.addItems(sorted(self._holders))
        if current and current in self._holders:
            self._holder_combo.setCurrentText(current)
        self._holder_combo.blockSignals(False)
        self._update_slots()

    def _update_slots(self) -> None:
        self._slots.clear()
        holder = self._holders.get(self._holder_combo.currentText())
        samples = getattr(holder, "_samples", {}) if holder is not None else {}
        for slot in sorted(samples, key=lambda k: int(k)):
            name = getattr(samples[slot], "name", "?")
            self._slots.addItem(f"{slot}: {name}")
        self._update_actions()

    def _selected_slot(self) -> int | None:
        item = self._slots.currentItem()
        if item is None:
            return None
        try:
            return int(item.text().split(":", 1)[0])
        except ValueError:
            return None

    def _update_actions(self) -> None:
        self._goto_btn.setEnabled(
            self._selected_slot() is not None and kernel_access.sam_is_loaded()
        )

    def _on_goto(self) -> None:
        holder_var = self._holder_combo.currentText()
        slot = self._selected_slot()
        if holder_var and slot is not None:
            self.run_guarded(f"{holder_var}.gotoSample({slot})")
