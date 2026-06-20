"""DiodeBox device class for CMS (11-BM) endstation.

Extracted from: profile-collection/startup/42-diodebox.py

Hardware: ioLogik E1214/E1200 series analogue output controller.
PV prefix convention: ``"XF:11BMB-CT{DIODE-Local:3}"``
"""

from __future__ import annotations

import time

from ophyd import Component as Cpt
from ophyd import Device, EpicsSignal


class DiodeBox(Device):
    """4-channel analogue I/O box (ioLogik).

    Each channel has a setpoint (SP) and readback (RB) signal.
    Channel numbering follows the hardware labelling (0–3).

    Example::

        diodebox3 = DiodeBox("XF:11BMB-CT{DIODE-Local:3}", name="Diodebox3")
        diodebox3.set(channel=0, setpoint=1.5)   # set channel 0 to 1.5 V
        diodebox3.get(channel=0)                  # read channel 0 RB
    """

    A0_SP = Cpt(EpicsSignal, "OutCh00:Data-SP")
    A1_SP = Cpt(EpicsSignal, "OutCh01:Data-SP")
    A2_SP = Cpt(EpicsSignal, "OutCh02:Data-SP")
    A3_SP = Cpt(EpicsSignal, "OutCh03:Data-SP")

    A0_RB = Cpt(EpicsSignal, "InCh00:Data-RB")
    A1_RB = Cpt(EpicsSignal, "InCh01:Data-RB")
    A2_RB = Cpt(EpicsSignal, "InCh02:Data-RB")
    A3_RB = Cpt(EpicsSignal, "InCh03:Data-RB")

    def set(self, channel: int = 0, setpoint: float = 0) -> None:
        """Set a target value on *channel*."""
        getattr(self, f"A{channel}_SP").set(setpoint)
        print(f"{self.name}: Channel {channel} is set to {setpoint}.")

    def get(self, channel: int = 0) -> float:
        """Return the readback value for *channel*."""
        rb = getattr(self, f"A{channel}_RB").get()
        print(f"{self.name}: Channel {channel} is {rb}.")
        return rb

    def set_and_waitRB(self, channel: int = 0, setpoint: float = 0, tol: float = 0.001) -> None:
        """Set *channel* and block until the readback converges."""
        sp = getattr(self, f"A{channel}_SP")
        rb = getattr(self, f"A{channel}_RB")
        sp.set(setpoint)
        t0 = time.time()
        while abs(rb.get() - setpoint) > tol:
            time.sleep(0.05)
        print(f"Time elapsed = {time.time() - t0:.4f} s")
        print(f"{self.name}: Channel {channel} is set to {setpoint}.")
