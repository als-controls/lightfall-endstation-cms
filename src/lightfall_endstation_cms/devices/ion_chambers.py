"""Ion chamber and beam intensity monitor classes for CMS (11-BM) endstation.

Extracted from:
  profile-collection/startup/25-scalers.py   (IonChamber class + ic instance)
  profile-collection/startup/26-IonChamber.py (signal monitors)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, TypeVar

import numpy as np
import ophyd
from ophyd import Component as Cpt
from ophyd import Device, EpicsSignal

A = TypeVar("A")
B = TypeVar("B")


# ---------------------------------------------------------------------------
# Derived / custom signal types
# ---------------------------------------------------------------------------

class ScaleSignal(ophyd.signal.DerivedSignal):
    """DerivedSignal that multiplies the parent signal value by a fixed factor.

    Note: ScaleSignal takes another Signal (not a PV prefix) as its first
    positional argument and therefore cannot be constructed directly from happi.
    Create it programmatically after constructing the underlying signal.

    Example::

        raw = ophyd.EpicsSignalRO("XF:11BMB-BI{IM:3}:IC1_MON", name="ic1_raw")
        scaled = ScaleSignal(raw, factor=1e9, name="ic1_nA")
    """

    def __init__(self, *args, factor: float, **kwargs):
        self._factor = factor
        super().__init__(*args, **kwargs)

    def inverse(self, value):
        return self._factor * value

    def forward(self, value):
        return value / self._factor

    def describe(self):
        desc = super().describe()
        wd = desc[self.name]
        wd["derived_type"] = "ScaleSignal"
        wd["factor"] = self._factor
        return desc


class EpicsSignalROWait(ophyd.EpicsSignalRO):
    """EpicsSignalRO with a configurable settle-wait before each read.

    Useful for signals that need time to settle (e.g., slow electrometer
    integration).  The *wait_time* delay is inserted before every read().

    Args:
        *args:       Passed through to EpicsSignalRO (first arg = PV name).
        wait_time:   Seconds to sleep before reading (default 0).
        **kwargs:    Passed through to EpicsSignalRO.
    """

    def __init__(self, *args, wait_time: float | None = None, **kwargs):
        self._wait_time = wait_time or 0.0
        super().__init__(*args, **kwargs)

    def read(self, *args, **kwargs):
        time.sleep(self._wait_time)
        return super().read(*args, **kwargs)


class EpicsSignalROIntegrate(ophyd.EpicsSignalRO):
    """EpicsSignalRO that averages *integrate_num* consecutive samples.

    Useful for noisy signals.  Optionally includes a settle-wait before the
    integration window starts.

    Args:
        *args:            Passed through to EpicsSignalRO.
        wait_time:        Seconds to sleep before integration (default 0).
        integrate_num:    Number of samples to average (default 1).
        integrate_delay:  Seconds between samples (default 0.01).
        **kwargs:         Passed through to EpicsSignalRO.
    """

    def __init__(
        self,
        *args,
        wait_time: float | None = None,
        integrate_num: int = 1,
        integrate_delay: float = 0.01,
        **kwargs,
    ):
        self._wait_time = wait_time or 0.0
        self._integrate_num = integrate_num
        self._integrate_delay = integrate_delay
        super().__init__(*args, **kwargs)

    def read(self, *args, **kwargs):
        time.sleep(self._wait_time)
        total = 0.0
        for _ in range(self._integrate_num):
            total += super().read(*args, **kwargs)[self.name]["value"]
            time.sleep(self._integrate_delay)
        average = total / self._integrate_num
        ret = super().read(*args, **kwargs)
        ret[self.name]["value"] = average
        return ret


# ---------------------------------------------------------------------------
# IonChamber device (Oxford I404)
# ---------------------------------------------------------------------------

class IonChamber(Device):
    """Oxford I404 ion chamber with 4 readout channels.

    Channels are read as log10 values in the custom read() method to match
    the CMS convention for intensity monitoring.

    PV suffix convention: prefix = ``"XF:11BMB-BI{IM:3}:"``
    """

    ch1 = Cpt(EpicsSignal, "IC1_MON")
    ch2 = Cpt(EpicsSignal, "IC2_MON")
    ch3 = Cpt(EpicsSignal, "IC3_MON")
    ch4 = Cpt(EpicsSignal, "IC4_MON")
    period_setpoint = Cpt(EpicsSignal, "PERIOD_SP")
    period_readback = Cpt(EpicsSignal, "PERIOD_MON")
    count = Cpt(EpicsSignal, "GETCS")

    def setExposureTime(self, exptime: float, **kwargs):
        while self.period_readback.get() != exptime:
            time.sleep(0.1)
            self.period_setpoint.set(exptime)
        print(f"Set Ion Chamber exposure time to {self.period_readback.get()} s")

    def read(self) -> Dict[str, Dict[str, Any]]:
        self.count.put(0)
        time.sleep(self.period_readback.get() + 0.1)
        now = datetime.now(timezone.utc).timestamp()
        return {
            f"{self.name}_ch4": {"value": np.log10(self.ch4.get()), "timestamp": now},
            f"{self.name}_ch3": {"value": np.log10(self.ch3.get()), "timestamp": now},
            f"{self.name}_ch2": {"value": np.log10(abs(self.ch2.get())), "timestamp": now},
            f"{self.name}_ch1": {"value": np.log10(self.ch1.get()), "timestamp": now},
            f"{self.name}_period_setpoint": {"value": self.period_setpoint.get(), "timestamp": now},
            f"{self.name}_period_readback": {"value": self.period_readback.get(), "timestamp": now},
            f"{self.name}_count": {"value": self.count.get(), "timestamp": now},
        }

    def trigger_and_read(self):
        from bluesky.plan_stubs import mv
        print("Triggering Ion Chamber...")
        yield from mv(self.count, 1)

    def expose(self, monitor_type: str = "all"):
        from bluesky.run_engine import RunEngine
        # NOTE: RE must be available in the calling scope for this helper.
        # Prefer using trigger_and_read() inside a bluesky plan instead.
        from IPython import get_ipython
        ip = get_ipython()
        if ip and "RE" in ip.user_ns:
            ip.user_ns["RE"](ip.user_ns["bps"].mv(self.count, 1))
        if monitor_type == "ch4":
            return self.ch4.get()
        elif monitor_type == "ch1":
            return self.ch1.get()
        elif monitor_type == "ch2":
            return self.ch2.get()
        elif monitor_type == "ch3":
            return self.ch3.get()
        else:  # 'all'
            return (self.ch1.get() + self.ch2.get()) / 2
