"""Plugin manifest for CMS (11-BM) endstation.

Defines all Lightfall plugins provided by this package.
"""

from lightfall.plugins.manifest import PluginEntry, PluginManifest

manifest = PluginManifest(
    name="lightfall-endstation-cms",
    version="0.1.0",
    description="Lightfall plugins for NSLS-II CMS beamline (11-BM)",
    plugins=[
        PluginEntry(
            type_name="device_backend",
            name="cms_profile_collection",
            import_path="lightfall_endstation_cms.plugin:CMSProfileCollectionPlugin",
            metadata={"beamline": "11-BM CMS"},
        ),
        PluginEntry(
            type_name="auth_provider",
            name="nsls2_tiled",
            import_path="lightfall_endstation_cms.auth.nsls2_provider:NSLS2AuthPlugin",
            metadata={"beamline": "11-BM CMS"},
            # Preload: the startup login dialog renders one button per registered
            # auth provider, so this must be in AuthProviderRegistry BEFORE the
            # dialog is shown. Non-preload (background) plugins load too late and
            # the "NSLS-II (CMS)" button would be missing from the dialog.
            preload=True,
        ),
    ],
)
