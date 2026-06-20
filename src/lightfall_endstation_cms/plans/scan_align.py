"""CMS (11-BM) scan / alignment plans, ported as Lightfall PlanPlugins.

``fit_scan`` is the distinctive CMS alignment plan: scan a motor across a span,
fit the chosen detector field to a peak model (or simple statistic), and move
the motor to the fitted center. The profile-collection implementation
(``91-fit_scan.py``) was an imperative function that drove the RunEngine itself
and rendered with matplotlib ``LiveFit``/``LivePlot``; this is reimplemented as
a proper Bluesky generator that emits a normal run (so Lightfall's live viz
shows it) and fits with Lightfall's own fitting subsystem.

Lightfall core already provides plain ``scan``/``count``/``adaptive_scan`` and a
centroid-based ``tune_centroid`` alignment, so those are intentionally not
re-ported here — ``fit_scan`` adds model-based (Gaussian/Lorentzian/Voigt) and
statistical (max/min/COM) centering that those built-ins don't do.
"""

from __future__ import annotations

from typing import Annotated, Any, Generator, Literal

import numpy as np
from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp
from loguru import logger

from lightfall.plugins.plan_plugin import PlanPlugin
from lightfall.ui.annotations import Decimals, DeviceFilter, Range, Unit

# Type aliases for readability / UI generation (see plan_design reference).
Detector = Any
Motor = Any

# Fit options: simple statistics plus Lightfall fitter names. Model fits
# (peak: gaussian/lorentzian/voigt; edge: step) defer to Lightfall's fitting
# subsystem, so any fitter it registers with a "center" parameter works here.
FitKind = Literal[
    "gaussian", "lorentzian", "voigt", "step", "com", "max", "min", "none"
]
_STATISTICS = {"com", "max", "min"}


def _compute_center(xs: np.ndarray, ys: np.ndarray, fit: str) -> float | None:
    """Return the centering position for the scan, or None if not determinable.

    Simple statistics (com/max/min) are computed directly; any other name is
    treated as a Lightfall fitter (e.g. gaussian/lorentzian/voigt peak or the
    "step" error-function edge) and must expose a "center" parameter.
    """
    fit = (fit or "none").lower()
    if fit in ("none", ""):
        return None
    if len(xs) == 0 or len(ys) == 0:
        return None
    if fit == "com":
        total = float(np.sum(ys))
        return float(np.sum(xs * ys) / total) if total != 0 else None
    if fit == "max":
        return float(xs[int(np.argmax(ys))])
    if fit == "min":
        return float(xs[int(np.argmin(ys))])

    from lightfall.visualization.fitting.fitters import get_fitter

    try:
        fitter = get_fitter(fit)
    except ValueError:
        logger.warning("fit_scan: unknown fit '{}'; not centering", fit)
        return None
    result = fitter.fit(xs, ys)
    if result.success and "center" in result.parameters:
        return float(result.parameters["center"])
    logger.warning("fit_scan: {} fit did not converge ({})", fit, result.info)
    return None


def fit_scan(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    motor: Annotated[Motor, DeviceFilter(category="motor")],
    span: Annotated[float, Unit("mm"), Decimals(4), Range(0.0, 1e6)] = 1.0,
    num: Annotated[int, Range(3, 10001)] = 11,
    fit: FitKind = "gaussian",
    move_to_center: bool = True,
    exposure_time: Annotated[float, Unit("s"), Range(0.0, 3600.0)] | None = None,
    target_field: str | None = None,
    wait_time: Annotated[float, Unit("s"), Range(0.0, 60.0)] = 0.0,
    md: dict | None = None,
) -> Generator[Any, Any, dict]:
    """Scan a motor across a span, fit the detector signal, move to the center.

    The scan is centered on the motor's current position (``span`` is the total
    width). After the scan the chosen detector field is fit to ``fit`` and, if
    ``move_to_center`` and the center lies within the scanned range, the motor
    is moved there; otherwise it returns to its starting position.

    Args:
        detectors: Detectors to read at each point (the fit uses ``target_field``).
        motor: Motor to scan.
        span: Total scan width, centered on the current position (mm).
        num: Number of scan points.
        fit: Centering method — a peak model (gaussian/lorentzian/voigt) fit via
            Lightfall's fitters, a statistic (com/max/min), or "none" (scan only).
        move_to_center: Move the motor to the fitted center when found.
        exposure_time: Per-point exposure; applied via the detector's
            ``setExposureTime`` plan or ``cam.acquire_time`` when available.
        target_field: Detector field to fit (defaults to the first hinted field).
        wait_time: Settle time after each move before reading.
        md: Extra run metadata.

    Returns:
        Dict with ``center`` (fitted center or None), ``field``, ``x``, ``y``.
    """
    detectors = list(detectors)
    initial_position = yield from bps.rd(motor)
    start = initial_position - span / 2.0
    stop = initial_position + span / 2.0
    positions = np.linspace(start, stop, num)

    field = _resolve_target_field(detectors, target_field, "fit_scan")

    # Per-point exposure: prefer the device's own setExposureTime plan (CMS
    # area detectors define one), else drive cam.acquire_time directly.
    if exposure_time is not None:
        for det in detectors:
            if hasattr(det, "setExposureTime"):
                yield from det.setExposureTime(exposure_time)
            elif hasattr(det, "cam") and hasattr(det.cam, "acquire_time"):
                yield from bps.mv(det.cam.acquire_time, exposure_time)

    run_md = {
        "plan_name": "fit_scan",
        "motor": getattr(motor, "name", str(motor)),
        "fit_function": fit,
        "fit_field": field,
        "scan_span": float(span),
        "num_points": int(num),
    }
    run_md.update(md or {})

    xs: list[float] = []
    ys: list[float] = []

    @bpp.stage_decorator(detectors)
    @bpp.run_decorator(md=run_md)
    def _inner() -> Generator[Any, Any, None]:
        for x in positions:
            yield from bps.checkpoint()
            yield from bps.mv(motor, float(x))
            if wait_time:
                yield from bps.sleep(wait_time)
            reading = yield from bps.trigger_and_read([*detectors, motor])
            xs.append(float(x))
            ys.append(float(reading[field]["value"]))

    yield from _inner()

    center = _compute_center(np.asarray(xs), np.asarray(ys), fit)

    target = initial_position
    if move_to_center and center is not None:
        lo, hi = min(start, stop), max(start, stop)
        if lo <= center <= hi:
            target = center
        else:
            logger.warning(
                "fit_scan: fitted center {} outside scan range [{}, {}]; "
                "returning to start {}",
                center, lo, hi, initial_position,
            )
    yield from bps.mv(motor, target)

    logger.info("fit_scan: field={} fit={} center={}", field, fit, center)
    return {"center": center, "field": field, "x": xs, "y": ys}


def _resolve_target_field(detectors: list[Any], target_field: str | None, plan_name: str) -> str:
    """Reuse Lightfall's canonical detector-field resolver."""
    from lightfall.acquire.plans.lightfall_plans import (
        _resolve_target_field as _core_resolver,
    )

    return _core_resolver(detectors, target_field, plan_name)


def fit_edge(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    motor: Annotated[Motor, DeviceFilter(category="motor")],
    span: Annotated[float, Unit("mm"), Decimals(4), Range(0.0, 1e6)] = 1.0,
    num: Annotated[int, Range(3, 10001)] = 11,
    move_to_center: bool = True,
    exposure_time: Annotated[float, Unit("s"), Range(0.0, 3600.0)] | None = None,
    target_field: str | None = None,
    wait_time: Annotated[float, Unit("s"), Range(0.0, 60.0)] = 0.0,
    md: dict | None = None,
) -> Generator[Any, Any, dict]:
    """Scan across an edge and move the motor to the 50% (half-cut) point.

    Convenience wrapper over :func:`fit_scan` using the error-function step
    model, for knife-edge / absorption-edge alignment. The signed step height
    means it handles both rising and falling edges; the motor is moved to the
    fitted edge center (the 50% point).

    Args:
        detectors: Detectors to read at each point.
        motor: Motor to scan.
        span: Total scan width, centered on the current position (mm).
        num: Number of scan points.
        move_to_center: Move the motor to the fitted edge when found.
        exposure_time: Per-point exposure (uses current setting if None).
        target_field: Detector field to fit (defaults to the first hinted field).
        wait_time: Settle time after each move before reading.
        md: Extra run metadata.

    Returns:
        Dict with ``center`` (edge position or None), ``field``, ``x``, ``y``.
    """
    return (
        yield from fit_scan(
            detectors,
            motor,
            span=span,
            num=num,
            fit="step",
            move_to_center=move_to_center,
            exposure_time=exposure_time,
            target_field=target_field,
            wait_time=wait_time,
            md={**(md or {}), "plan_header": "fit_edge"},
        )
    )


class FitScanPlan(PlanPlugin):
    """Contributes the CMS ``fit_scan`` alignment plan."""

    @property
    def name(self) -> str:
        return "fit_scan"

    @property
    def category(self) -> str:
        return "alignment"

    def get_plan_function(self):
        return fit_scan


class FitEdgePlan(PlanPlugin):
    """Contributes the CMS ``fit_edge`` (knife-edge alignment) plan."""

    @property
    def name(self) -> str:
        return "fit_edge"

    @property
    def category(self) -> str:
        return "alignment"

    def get_plan_function(self):
        return fit_edge
