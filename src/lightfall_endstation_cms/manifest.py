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
            import_path="lightfall_endstation_cms.backends.profile_collection:ProfileCollectionBackend",
            metadata={"beamline": "11-BM CMS"},
        ),
    ],
)
