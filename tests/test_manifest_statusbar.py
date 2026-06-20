"""Verify the NSLS-II ring status plugin is registered and importable."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.manifest import manifest


def _find(name):
    return next((e for e in manifest.plugins if e.name == name), None)


def test_statusbar_entry_registered():
    entry = _find("nsls2_beam_status")
    assert entry is not None
    assert entry.type_name == "statusbar"
    assert entry.import_path == (
        "lightfall_endstation_cms.statusbar.nsls2_beam_status:NSLS2BeamStatusPlugin"
    )


def test_statusbar_entry_import_path_resolves():
    entry = _find("nsls2_beam_status")
    module_path, _, attr = entry.import_path.partition(":")
    module = importlib.import_module(module_path)
    assert hasattr(module, attr)
