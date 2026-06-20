from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


class _FakeShell:
    def __init__(self):
        self.user_ns = {}
        self.ran = []

    def run_cell(self, source, store_history=False):
        self.ran.append(source)
        return type("Result", (), {"error_in_exec": None})()


def test_run_profile_adds_startup_dir_to_sys_path(tmp_path, monkeypatch):
    startup = tmp_path
    # Infra scripts (kept) plus a device script (skipped) — only infra runs.
    (startup / "00-startup.py").write_text("x = 1\n", encoding="utf-8")
    (startup / "01-ad33_tmp.py").write_text("y = 2\n", encoding="utf-8")
    (startup / "10-motors.py").write_text("z = 3\n", encoding="utf-8")
    monkeypatch.setattr(
        "lightfall_endstation_cms.loader._get_profile_path", lambda: startup
    )

    saved = list(sys.path)
    try:
        shell = _FakeShell()
        ProfileSessionBootstrapper().run_profile(shell)

        # The startup dir is importable so sibling-by-filename imports resolve
        # (e.g. 86-live-spec.py -> importlib.import_module('85-suitcase-specfile')).
        assert str(startup) in sys.path
        # Only the infra scripts (00, 01) ran; the device script (10) was skipped.
        assert len(shell.ran) == 2
    finally:
        sys.path[:] = saved
