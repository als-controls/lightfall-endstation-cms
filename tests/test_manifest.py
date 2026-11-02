from lightfall_endstation_cms.manifest import manifest


def test_only_auth_provider_is_preload():
    by_name = {e.name: e for e in manifest.plugins}
    assert by_name["nsls2_tiled"].preload is True
    for panel in ("cms_sample", "cms_holder", "cms_beamline"):
        assert by_name[panel].preload is False


def test_site_notes_is_a_tool_plugin_with_packaged_skill():
    from lightfall.plugins.tool_plugin import ToolPlugin

    from lightfall_endstation_cms.agents.cms_notes import CMSSiteNotesAgent

    entry = {e.name: e for e in manifest.plugins}["cms_site_notes"]
    assert entry.type_name == "tool"
    assert not manifest.get_plugins_by_type("agent")  # retired type; the loader skips it
    plugin = CMSSiteNotesAgent()
    assert isinstance(plugin, ToolPlugin)
    skill = plugin.get_skill_dir() / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\nname: cms-site-notes\n")
    assert "Tiled instances" in text
