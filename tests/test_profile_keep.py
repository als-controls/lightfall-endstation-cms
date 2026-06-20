"""The bootstrap runs the profile in two phases: infra, then the SAM framework.

Devices come from happi (and are injected between phases), so the device-defining
scripts are never run; infra (00-03) brings up RE+Tiled and the SAM set
(81/94/95/96/97/991) brings up the console framework.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.bootstrap import (
    DEFAULT_INFRA_KEEP,
    DEFAULT_SAM_KEEP,
    ProfileSessionBootstrapper,
)


def _make_startup(tmp_path: Path) -> Path:
    for name in [
        "00-startup.py",
        "01-ad33_tmp.py",
        "02-tiled-writer.py",
        "03-async.py",
        "10-motors.py",
        "20-area-detectors.py",
        "81-beam.py",
        "94-sample.py",
        "97-user.py",
        "991-modular-table.py",
    ]:
        (tmp_path / name).write_text("# stub\n", encoding="utf-8")
    return tmp_path


def _names(bs, phase):
    return [p.name for p in bs._profile_scripts(bs._keep(phase))]


def test_infra_phase_runs_only_infra(tmp_path, monkeypatch):
    startup = _make_startup(tmp_path)
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: startup
    )
    bs = ProfileSessionBootstrapper()
    assert _names(bs, "infra") == [
        "00-startup.py", "01-ad33_tmp.py", "02-tiled-writer.py", "03-async.py",
    ]


def test_sam_phase_runs_framework_not_device_definers(tmp_path, monkeypatch):
    startup = _make_startup(tmp_path)
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: startup
    )
    bs = ProfileSessionBootstrapper()
    sam = _names(bs, "sam")
    # SAM framework runs (in order); device-definers and infra do not.
    assert sam == ["81-beam.py", "94-sample.py", "97-user.py", "991-modular-table.py"]
    for not_sam in ("00-startup.py", "10-motors.py", "20-area-detectors.py"):
        assert not_sam not in sam


def test_default_keep_sets():
    assert DEFAULT_INFRA_KEEP == frozenset({"00", "01", "02", "03"})
    assert DEFAULT_SAM_KEEP == frozenset({"81", "82", "94", "95", "96", "97", "991"})


def test_env_overrides_replace_defaults(tmp_path, monkeypatch):
    startup = _make_startup(tmp_path)
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: startup
    )
    monkeypatch.setenv("CMS_PROFILE_KEEP", "00,10")
    monkeypatch.setenv("CMS_PROFILE_SAM_KEEP", "94")
    bs = ProfileSessionBootstrapper()
    assert _names(bs, "infra") == ["00-startup.py", "10-motors.py"]
    assert _names(bs, "sam") == ["94-sample.py"]


def test_sam_hosting_disabled_when_sam_keep_empty(tmp_path, monkeypatch):
    startup = _make_startup(tmp_path)
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: startup
    )
    monkeypatch.setenv("CMS_PROFILE_SAM_KEEP", "")
    bs = ProfileSessionBootstrapper()
    assert _names(bs, "sam") == []  # inject-only, no SAM modules
