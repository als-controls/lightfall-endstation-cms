"""Plugin manifest for CMS (11-BM) endstation.

Defines all LUCID plugins provided by this package.
"""

from lucid.plugins.manifest import PluginEntry, PluginManifest

manifest = PluginManifest(
    name="lucid-endstation-cms",
    version="0.1.0",
    description="LUCID plugins for NSLS-II CMS beamline (11-BM)",
    plugins=[],
)
