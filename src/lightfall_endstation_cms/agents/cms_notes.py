"""CMS (11-BM) site-notes skill plugin.

A concise, interim cheat-sheet for the embedded Claude agent covering the CMS
Tiled instances (and the per-entry access-policy read gotcha) plus a few
site-specific quirks. Intended to be slimmed down or retired once the CMS
deployment settles.

The notes themselves live in skills/cms_site_notes/SKILL.md.
"""

from __future__ import annotations

from pathlib import Path

from lightfall.plugins.tool_plugin import ToolPlugin


class CMSSiteNotesAgent(ToolPlugin):
    """Skill giving Claude CMS-specific context (Tiled instances + site quirks)."""

    def get_skill_dir(self) -> Path:
        return Path(__file__).parent.parent / "skills" / "cms_site_notes"

    @property
    def name(self) -> str:
        return "cms_site_notes"

    @property
    def display_name(self) -> str:
        return "CMS Site Notes"

    @property
    def description(self) -> str:
        return (
            "CMS (11-BM) site notes: the NSLS-II Tiled instances/nodes and the "
            "per-entry access-policy read gotcha (Duo identity lists zero "
            "records; the service key sees all), plus interim deployment "
            "quirks. Use when working with the data browser, Tiled, run "
            "history/scan data, login, or CMS profile/session setup."
        )

    @property
    def category(self) -> str:
        return "operations"

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def priority(self) -> int:
        return 5
