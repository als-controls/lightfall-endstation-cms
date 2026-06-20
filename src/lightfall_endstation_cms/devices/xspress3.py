"""Xspress3 fluorescence detector class for CMS (11-BM) endstation.

Extracted from: profile-collection/startup/27-Xspress3.py

BUG FIX: The original stage() method referenced the bare name ``xs``
(a global variable) when constructing the HDF5 write-path template.
This has been corrected to ``self.name``.

``assets_path`` must be set before staging — see area_detectors.py for the
same pattern.
"""

from __future__ import annotations

import itertools
import time as ttime
from collections import OrderedDict
from enum import Enum

from ophyd import Component as Cpt
from ophyd import DeviceStatus, EpicsSignal, Signal
from ophyd.areadetector.filestore_mixins import FileStorePluginBase
from ophyd.utils import set_and_wait
from nslsii.detectors.xspress3 import (
    Xspress3Channel,
    Xspress3Detector,
    Xspress3FileStore,
    XspressTrigger,
)
from ophyd.areadetector.plugins import PluginBase
from ophyd.device import Staged

# ---------------------------------------------------------------------------
# Module-level assets_path hook (same pattern as area_detectors.py)
# ---------------------------------------------------------------------------
assets_path = None  # type: ignore[assignment]


def _get_assets_path() -> str:
    if callable(assets_path):
        return assets_path()
    raise RuntimeError(
        "xspress3.assets_path is not set.  "
        "Assign a callable before staging."
    )


# ---------------------------------------------------------------------------
# Scan mode enum
# ---------------------------------------------------------------------------

class ScanMode(Enum):
    step = 1
    fly = 2


# ---------------------------------------------------------------------------
# Custom file-store mixin with fly-scan support
# ---------------------------------------------------------------------------

class Xspress3FileStoreFlyable(Xspress3FileStore):
    """Xspress3 file-store that supports both step and fly scan modes."""

    @property
    def filestore_res(self):
        raise Exception("don't want to be here")

    @property
    def filestore_spec(self):
        if self.parent._mode is ScanMode.fly:
            return "XPS3_FLY"
        return "XSP3"

    def generate_datum(self, key, timestamp, datum_kwargs):
        if self.parent._mode is ScanMode.step:
            return super().generate_datum(key, timestamp, datum_kwargs)
        elif self.parent._mode is ScanMode.fly:
            # Skip one MRO level intentionally for fly-scan bulk-mode
            return FileStorePluginBase.generate_datum(self, key, timestamp, datum_kwargs)

    def warmup(self):
        """Prime the HDF5 plugin by running a single acquisition."""
        print("Warming up the hdf5 plugin...", end="")
        set_and_wait(self.enable, 1)
        sigs = OrderedDict([
            (self.parent.settings.array_callbacks, 1),
            (self.parent.settings.image_mode, "Single"),
            (self.parent.settings.trigger_mode, "Internal"),
            (self.parent.settings.acquire_time, 1),
            (self.parent.settings.acquire, 1),
        ])
        original_vals = {sig: sig.get() for sig in sigs}
        for sig, val in sigs.items():
            ttime.sleep(0.1)
            set_and_wait(sig, val)
        for sig, val in reversed(list(original_vals.items())):
            ttime.sleep(0.1)
            set_and_wait(sig, val)
        print("done")

    def describe(self):
        desc = super().describe()
        if self.parent._mode is ScanMode.fly:
            spec = {
                "external": "FileStore:",
                "dtype": "array",
                "shape": (self.parent.settings.num_images.get(), 3, 4096),
                "source": self.prefix,
            }
            return {self.parent._f_key: spec}
        return super().describe()


# ---------------------------------------------------------------------------
# Custom trigger mixin with fly-scan support
# ---------------------------------------------------------------------------

class XspressTriggerFlyable(XspressTrigger):
    """Xspress3 trigger that dispatches correctly in both step and fly modes."""

    def trigger(self):
        if self._staged != Staged.yes:
            raise RuntimeError("not staged")
        self._status = DeviceStatus(self)
        self.settings.erase.put(1)
        self._acquisition_signal.put(1, wait=False)
        trigger_time = ttime.time()
        if self._mode is ScanMode.step:
            for sn in self.read_attrs:
                if sn.startswith("channel") and "." not in sn:
                    self.dispatch(getattr(self, sn).name, trigger_time)
        elif self._mode is ScanMode.fly:
            self.dispatch(self._f_key, trigger_time)
        else:
            raise Exception(f"unexpected mode {self._mode}")
        self._abs_trigger_count += 1
        return self._status


# ---------------------------------------------------------------------------
# Full detector class
# ---------------------------------------------------------------------------

class OPLSXspress3Detector(XspressTriggerFlyable, Xspress3Detector):
    """CMS Xspress3 fluorescence detector (single channel, step + fly modes).

    PV prefix: ``"XF:11BM-ES{Xsp:1}:"``
    """

    roi_data = Cpt(PluginBase, "ROIDATA:")
    channel1 = Cpt(Xspress3Channel, "C1_", channel_num=1, read_attrs=["rois"])
    acquisition_time = Cpt(EpicsSignal, "AcquireTime")
    capture_mode = Cpt(EpicsSignal, "HDF5:Capture")
    erase = Cpt(EpicsSignal, "ERASE")
    array_counter = Cpt(EpicsSignal, "ArrayCounter_RBV")
    create_dir = Cpt(EpicsSignal, "HDF5:FileCreateDir")
    hdf5 = Cpt(Xspress3FileStoreFlyable, "HDF1:", write_path_template="")
    fly_next = Cpt(Signal, value=False)

    def __init__(
        self,
        prefix,
        *,
        f_key="fluor",
        configuration_attrs=None,
        read_attrs=None,
        **kwargs,
    ):
        self._f_key = f_key
        if configuration_attrs is None:
            configuration_attrs = [
                "external_trig",
                "total_points",
                "spectra_per_point",
                "settings",
                "rewindable",
            ]
        if read_attrs is None:
            read_attrs = ["channel1", "hdf5"]
        super().__init__(
            prefix,
            configuration_attrs=configuration_attrs,
            read_attrs=read_attrs,
            **kwargs,
        )
        self._mode = ScanMode.step

    def stage(self, *args, **kwargs):
        if self.spectra_per_point.get() != 1:
            raise NotImplementedError("multi spectra per point not supported yet")
        base = _get_assets_path()
        # BUG FIX: was ``xs.name`` (global), corrected to ``self.name``
        self.hdf5.write_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.hdf5.read_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.hdf5.reg_root = base + self.name
        ret = super().stage(*args, **kwargs)
        self._datum_counter = itertools.count()
        return ret

    def trigger(self):
        self._status = DeviceStatus(self)
        self.settings.erase.put(1)
        self._acquisition_signal.put(1, wait=False)
        trigger_time = ttime.time()
        for sn in self.read_attrs:
            if sn.startswith("channel") and "." not in sn:
                self.generate_datum(getattr(self, sn).name, trigger_time)
        self._abs_trigger_count += 1
        return self._status

    def unstage(self):
        self.settings.trigger_mode.put(1)  # 'Software'
        super().unstage()
        self._datum_counter = None

    def stop(self):
        ret = super().stop()
        self.hdf5.stop()
        return ret
