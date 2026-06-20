"""Tests for the CMS scan/alignment PlanPlugins."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.plans import FitScanPlan, fit_scan
from lightfall_endstation_cms.plans.scan_align import _compute_center


def test_plugin_metadata():
    plugin = FitScanPlan()
    assert plugin.name == "fit_scan"
    assert plugin.category == "alignment"
    assert plugin.get_plan_function() is fit_scan
    assert plugin.type_name == "plan"


def test_manifest_registers_fit_scan():
    from lightfall_endstation_cms.manifest import manifest

    entry = next(p for p in manifest.plugins if p.name == "fit_scan")
    assert entry.type_name == "plan"
    assert entry.import_path.endswith("plans:FitScanPlan")


def test_compute_center_statistics():
    xs = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    ys = np.array([0.0, 1.0, 4.0, 1.0, 0.0])
    assert _compute_center(xs, ys, "max") == 0.0
    assert _compute_center(xs, ys, "min") in (-2.0, 2.0)
    assert _compute_center(xs, ys, "com") == pytest.approx(0.0, abs=1e-9)
    assert _compute_center(xs, ys, "none") is None


def test_compute_center_gaussian():
    xs = np.linspace(-5, 5, 41)
    ys = 100.0 * np.exp(-((xs - 1.2) ** 2) / (2 * 0.7**2)) + 3.0
    center = _compute_center(xs, ys, "gaussian")
    assert center == pytest.approx(1.2, abs=0.05)


def test_fit_scan_centers_motor_on_gaussian():
    """End-to-end: run fit_scan against a simulated Gaussian detector and
    confirm it moves the motor to the fitted peak center."""
    pytest.importorskip("bluesky")
    from bluesky import RunEngine
    from ophyd.sim import SynAxis, SynGauss

    motor = SynAxis(name="motor")
    det = SynGauss("det", motor, "motor", center=0.3, Imax=100, sigma=0.4)

    RE = RunEngine()
    RE(fit_scan([det], motor, span=4.0, num=31, fit="gaussian"))

    # The motor should have been moved to ~the gaussian center.
    assert motor.position == pytest.approx(0.3, abs=0.1)


def test_fit_scan_none_returns_to_start():
    pytest.importorskip("bluesky")
    from bluesky import RunEngine
    from ophyd.sim import SynAxis, SynGauss

    motor = SynAxis(name="motor")
    motor.set(1.5).wait()
    det = SynGauss("det", motor, "motor", center=0.3, Imax=100, sigma=0.4)

    RE = RunEngine()
    RE(fit_scan([det], motor, span=4.0, num=21, fit="none"))

    # fit="none" -> no centering; motor returns to where it started.
    assert motor.position == pytest.approx(1.5, abs=1e-6)
