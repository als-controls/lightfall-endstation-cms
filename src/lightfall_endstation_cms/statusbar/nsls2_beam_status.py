"""NSLS-II ring status indicator for the Lightfall status bar (CMS 11-BM).

Displays storage-ring current, lifetime, operating mode, and beam
availability, sourced from EPICS PVs via NSLS2BeamStatusService.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import qtawesome as qta
from PySide6.QtCore import QUrl, Slot
from PySide6.QtGui import QDesktopServices

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.ui.theme import ThemeManager
from lightfall.ui.toast import ToastManager
from lightfall.utils.logging import logger

from lightfall_endstation_cms.services.nsls2_beam_status import (
    NSLS2BeamStatusService,
    is_nominal,
    status_level,
)

if TYPE_CHECKING:
    from lightfall_endstation_cms.services.nsls2_beam_status import NSLS2BeamData


class NSLS2BeamStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing NSLS-II storage-ring status.

    Color coding reflects live ring health (not just the shutter PV):
    success/green when nominal, warning/amber when beam is present but degraded
    (low current or lifetime), error/red when beam is down (shutter closed or
    current effectively zero), and text_secondary/gray when offline /
    disconnected. Clicking opens the NSLS-II operating-status page.
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.nsls2_beam",
        name="NSLS-II Beam Status",
        description="Shows NSLS-II storage-ring current and status",
        priority=45,
        position="permanent",
        tooltip="NSLS-II ring status - click for details",
    )

    BEAM_STATUS_URL = "https://www.bnl.gov/nsls2/operating-status.php"

    def __init__(self) -> None:
        super().__init__()
        self._service: NSLS2BeamStatusService | None = None
        self._last_beam_available: bool | None = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        return "nsls2_beam_status"

    def on_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl(self.BEAM_STATUS_URL))

    def update(self) -> None:
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        try:
            service = NSLS2BeamStatusService.get_instance()
            self._service = service
            if not service.is_running:
                service.start()
            if service.is_connected and service.current_data is not None:
                self._update_display_data(service.current_data)
            else:
                self._update_display_offline()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Could not get NSLS-II beam status: {}", e)
            self._update_display_offline()

    def connect_signals(self) -> None:
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        self._theme_manager.colors_changed.connect(self.update)
        try:
            service = NSLS2BeamStatusService.get_instance()
            self._service = service
            service.status_changed.connect(self._on_status_changed)
            service.connection_changed.connect(self._on_connection_changed)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Could not connect to NSLS2BeamStatusService: {}", e)

    def disconnect_signals(self) -> None:
        if self._service is not None:
            try:
                self._service.status_changed.disconnect(self._on_status_changed)
                self._service.connection_changed.disconnect(self._on_connection_changed)
            except RuntimeError:
                pass
        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    @Slot(object)
    def _on_status_changed(self, data: NSLS2BeamData) -> None:
        if self._service is not None and not self._service.is_connected:
            self._update_display_offline()
        else:
            self._update_display_data(data)

    @Slot(bool)
    def _on_connection_changed(self, connected: bool) -> None:
        if not connected:
            self._update_display_offline()
        else:
            self.update()

    def _update_display_data(self, data: NSLS2BeamData) -> None:
        if (
            self._last_beam_available is not None
            and data.beam_available != self._last_beam_available
        ):
            self._notify_status_change(data.beam_available)
        self._last_beam_available = data.beam_available

        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        colors = self._theme_manager.colors
        # Dynamic, value-driven coloring: green only when the ring is genuinely
        # nominal, amber when beam is present but degraded, red when it is down.
        # Keying off status_level() (not data.beam_available alone) means a
        # shutter that reads "open" at 0 mA no longer paints the bar green.
        color = {
            "nominal": colors.success,
            "degraded": colors.warning,
            "down": colors.error,
        }[status_level(data)]

        self.set_icon(qta.icon("ri.sun-line", color=color))
        # When everything is nominal, keep the bar quiet -- show just the green
        # icon. Off-nominal (low current/lifetime, or beam down) keeps the
        # numbers visible so operators notice. Full detail is always in the
        # tooltip.
        if is_nominal(data):
            self.set_text("")
        else:
            self.set_text(f"{data.beam_current:.0f} mA | {data.lifetime:.1f}h")
        self.set_color(color)
        self.set_tooltip(self._build_tooltip(data))

    def _notify_status_change(self, beam_available: bool) -> None:
        toast = ToastManager.get_instance()
        link = f'<a href="{self.BEAM_STATUS_URL}">Operating Status</a>'
        if beam_available:
            toast.success(
                "NSLS-II Beam Available",
                f"Storage-ring beam is now available · {link}",
                duration=10000,
            )
        else:
            toast.warning(
                "NSLS-II Beam Unavailable",
                f"Storage-ring beam is no longer available · {link}",
                duration=10000,
            )

    def _update_display_offline(self) -> None:
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        secondary = self._theme_manager.colors.text_secondary
        self.set_icon(qta.icon("ri.sun-line", color=secondary))
        self.set_text("Offline")
        self.set_color(secondary)
        error_msg = ""
        if self._service and self._service.last_error:
            error_msg = f"\nError: {self._service.last_error}"
        self.set_tooltip(f"NSLS-II ring status unavailable{error_msg}")

    def _build_tooltip(self, data: NSLS2BeamData) -> str:
        lines = [
            "NSLS-II Storage Ring",
            "-" * 25,
            f"Current: {data.beam_current:.1f} mA",
            f"Lifetime: {data.lifetime:.1f} hours",
            f"Mode: {data.mode or 'unknown'}",
            f"Beam: {'Available' if data.beam_available else 'Unavailable'}"
            f" ({data.shutter_status or '?'})",
            f"Top-off: {data.topoff_state or 'unknown'}",
            f"Next injection: {data.next_injection or 'unknown'}",
        ]
        if data.ops_message:
            lines.extend(["", "Operations:", data.ops_message])
        if data.timestamp:
            lines.extend(["", f"Updated: {data.timestamp.strftime('%H:%M:%S')}"])
        return "\n".join(lines)

    def get_introspection_data(self) -> dict[str, Any]:
        data = super().get_introspection_data()
        try:
            service = NSLS2BeamStatusService.get_instance()
            data.update(service.get_introspection_data())
        except Exception:
            data["nsls2_beam_connected"] = False
        return data
