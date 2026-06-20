"""CMS (11-BM) GUI panels (PanelPlugins) over the live kernel SAM framework."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class CMSSamplePanelPlugin(PanelPlugin):
    """Contributes the CMS Samples panel."""

    @property
    def name(self) -> str:
        return "cms_sample"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall_endstation_cms.panels.sample_panel import CMSSamplePanel

        return CMSSamplePanel


__all__ = ["CMSSamplePanelPlugin"]
