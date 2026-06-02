"""Plugin manifest for CMS (11-BM) endstation.

Defines all LUCID plugins provided by this package.
"""

from lucid.plugins.manifest import PluginEntry, PluginManifest

manifest = PluginManifest(
    name="lucid-endstation-cms",
    version="0.1.0",
    description="LUCID plugins for NSLS-II CMS beamline (11-BM)",
    plugins=[
        PluginEntry(
            type_name="device_backend",
            name="cms_profile_collection",
            import_path="lucid_endstation_cms.backends.profile_collection:ProfileCollectionBackend",
            metadata={"beamline": "11-BM CMS"},
        ),
    ],
)
