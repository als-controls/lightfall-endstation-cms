from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


def _make_startup(tmp_path: Path) -> Path:
    for name in [
        "00-startup.py",
        "10-motors.py",
        "24-area-detector-utilities.py",
        "55-archiver.py",
        "97-user.py",
        "99-caproto-test.py",
    ]:
        (tmp_path / name).write_text("# stub\n", encoding="utf-8")
    return tmp_path


def test_profile_scripts_skips_default_blacklist(tmp_path, monkeypatch):
    startup = _make_startup(tmp_path)
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: startup
    )

    bootstrapper = ProfileSessionBootstrapper(backend=MagicMock())
    names = [p.name for p in bootstrapper._profile_scripts()]

    # Core scripts run, in order.
    assert names == ["00-startup.py", "10-motors.py", "97-user.py"]
    # Blacklisted by default are skipped.
    assert "24-area-detector-utilities.py" not in names  # telnetlib
    assert "55-archiver.py" not in names  # arvpyf
    assert "99-caproto-test.py" not in names  # non-essential test; output spam


def test_cms_profile_blacklist_env_replaces_default(tmp_path, monkeypatch):
    startup = _make_startup(tmp_path)
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: startup
    )
    # Env REPLACES the default: 10 is now skipped; 24/55 are NOT (default gone).
    monkeypatch.setenv("CMS_PROFILE_BLACKLIST", "10")

    bootstrapper = ProfileSessionBootstrapper(backend=MagicMock())
    names = [p.name for p in bootstrapper._profile_scripts()]

    assert "10-motors.py" not in names
    assert "24-area-detector-utilities.py" in names
    assert "55-archiver.py" in names
    assert "00-startup.py" in names
