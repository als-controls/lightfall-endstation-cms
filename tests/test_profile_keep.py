"""The bootstrap runs only the profile's infrastructure scripts.

Devices come from happi, so the post-login profile run is limited to the
infra scripts (00-03) that produce the RunEngine and Tiled client. Everything
from 10 onward (devices, plans, helpers) is skipped.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.bootstrap import DEFAULT_PROFILE_KEEP, ProfileSessionBootstrapper


def _make_startup(tmp_path: Path) -> Path:
    for name in [
        "00-startup.py",
        "01-ad33_tmp.py",
        "02-tiled-writer.py",
        "03-async.py",
        "10-motors.py",
        "20-area-detectors.py",
        "94-sample.py",
        "991-modular-table.py",
    ]:
        (tmp_path / name).write_text("# stub\n", encoding="utf-8")
    return tmp_path


def test_profile_scripts_runs_only_infra(tmp_path, monkeypatch):
    startup = _make_startup(tmp_path)
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: startup
    )

    names = [p.name for p in ProfileSessionBootstrapper()._profile_scripts()]

    # Infra scripts run, in order.
    assert names == ["00-startup.py", "01-ad33_tmp.py", "02-tiled-writer.py", "03-async.py"]
    # Device / plan / helper scripts are not executed.
    for skipped in ("10-motors.py", "20-area-detectors.py", "94-sample.py", "991-modular-table.py"):
        assert skipped not in names


def test_default_keep_is_infra_only():
    assert DEFAULT_PROFILE_KEEP == frozenset({"00", "01", "02", "03"})


def test_cms_profile_keep_env_replaces_default(tmp_path, monkeypatch):
    startup = _make_startup(tmp_path)
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: startup
    )
    # Env REPLACES the default keep-set: now only 00 and 10 run.
    monkeypatch.setenv("CMS_PROFILE_KEEP", "00,10")

    names = [p.name for p in ProfileSessionBootstrapper()._profile_scripts()]

    assert names == ["00-startup.py", "10-motors.py"]
    assert "02-tiled-writer.py" not in names
