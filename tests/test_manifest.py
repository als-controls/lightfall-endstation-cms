from lightfall_endstation_cms.manifest import manifest


def test_only_auth_provider_is_preload():
    by_name = {e.name: e for e in manifest.plugins}
    assert by_name["nsls2_tiled"].preload is True
    for panel in ("cms_sample", "cms_holder", "cms_beamline"):
        assert by_name[panel].preload is False
