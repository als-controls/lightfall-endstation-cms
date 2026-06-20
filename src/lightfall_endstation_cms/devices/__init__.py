"""Ophyd device classes for CMS (11-BM) endstation equipment.

Sub-modules
-----------
motors          Configurable, MotorCenterAndGap, Blades, Filter
area_detectors  StandardProsilicaV33, Pilatus2MV33, Pilatus800V33, etc.
ion_chambers    IonChamber, ScaleSignal, EpicsSignalROWait, EpicsSignalROIntegrate
xspress3        OPLSXspress3Detector
shutters        TwoButtonShutterNC, TriState
diodebox        DiodeBox
serial_devices  Agilent_34970A, Keithley_2000, Minichiller, TTL_control

Lazy imports
------------
Names are resolved lazily (PEP 562): importing this package does **not** import
every sub-module.  This matters because ``area_detectors``, ``shutters`` and
``xspress3`` depend on ``nslsii`` (the ``lightfall-endstation-cms[beamline]``
extra), while ``motors``/``diodebox``/``ion_chambers`` need only ``ophyd``.  An
eager ``__init__`` would make a missing ``nslsii`` — or a single import error in
one detector module — break resolution of *every* class, including the
pure-ophyd motors.  Lazy resolution keeps each sub-module independent, which is
exactly what the happi backend needs (it imports each class by its full dotted
path, e.g. ``lightfall_endstation_cms.devices.motors.Blades``).

    from lightfall_endstation_cms.devices.motors import MotorCenterAndGap
    from lightfall_endstation_cms.devices import MotorCenterAndGap  # same thing

Setting assets_path
-------------------
Area detectors and the Xspress3 write data via ``assets_path()``.  Before
staging any such device outside a bluesky profile session, set it::

    from lightfall_endstation_cms.devices import area_detectors, xspress3
    _func = lambda: "/nsls2/data/cms/proposals/2025-1/PAS-123456/assets/"
    area_detectors.assets_path = _func
    xspress3.assets_path = _func
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# Map each public name to the sub-module that defines it. Used by __getattr__
# to import the owning sub-module on first access only.
_NAME_TO_MODULE = {
    # motors
    "Configurable": "motors",
    "MotorCenterAndGap": "motors",
    "Blades": "motors",
    "Filter": "motors",
    # area detectors
    "TIFFPluginWithFileStore": "area_detectors",
    "HDF5PluginWithFileStore": "area_detectors",
    "ProsilicaDetectorCamV33": "area_detectors",
    "StandardProsilica": "area_detectors",
    "StandardProsilicaV33": "area_detectors",
    "PilatusDetectorCamV33": "area_detectors",
    "PilatusV33": "area_detectors",
    "Pilatus800V33": "area_detectors",
    "Pilatus8002V33": "area_detectors",
    "Pilatus2MV33": "area_detectors",
    "PilatusV33_h5": "area_detectors",
    "Pilatus800V33_h5": "area_detectors",
    # ion chambers
    "ScaleSignal": "ion_chambers",
    "EpicsSignalROWait": "ion_chambers",
    "EpicsSignalROIntegrate": "ion_chambers",
    "IonChamber": "ion_chambers",
    # xspress3
    "ScanMode": "xspress3",
    "OPLSXspress3Detector": "xspress3",
    # shutters
    "TwoButtonShutterNC": "shutters",
    "TriState": "shutters",
    # diodebox
    "DiodeBox": "diodebox",
    # serial
    "Agilent_34970A": "serial_devices",
    "Keithley_2000": "serial_devices",
    "Minichiller": "serial_devices",
    "TTL_control": "serial_devices",
}

__all__ = list(_NAME_TO_MODULE)


def __getattr__(name: str):
    """Lazily import the sub-module that owns *name* (PEP 562)."""
    module_name = _NAME_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f"{__name__}.{module_name}")
    return getattr(module, name)


def __dir__():
    return sorted(__all__)


if TYPE_CHECKING:  # for type checkers / IDEs only — not executed at runtime
    from .area_detectors import (  # noqa: F401
        HDF5PluginWithFileStore,
        Pilatus2MV33,
        Pilatus800V33,
        Pilatus800V33_h5,
        Pilatus8002V33,
        PilatusDetectorCamV33,
        PilatusV33,
        PilatusV33_h5,
        ProsilicaDetectorCamV33,
        StandardProsilica,
        StandardProsilicaV33,
        TIFFPluginWithFileStore,
    )
    from .diodebox import DiodeBox  # noqa: F401
    from .ion_chambers import (  # noqa: F401
        EpicsSignalROIntegrate,
        EpicsSignalROWait,
        IonChamber,
        ScaleSignal,
    )
    from .motors import Blades, Configurable, Filter, MotorCenterAndGap  # noqa: F401
    from .serial_devices import (  # noqa: F401
        Agilent_34970A,
        Keithley_2000,
        Minichiller,
        TTL_control,
    )
    from .shutters import TriState, TwoButtonShutterNC  # noqa: F401
    from .xspress3 import OPLSXspress3Detector, ScanMode  # noqa: F401
