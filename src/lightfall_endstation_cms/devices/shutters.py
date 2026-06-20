"""Shutter device classes for CMS (11-BM) endstation.

Extracted from: profile-collection/startup/50-bluesky-devices.py
"""

from __future__ import annotations

from ophyd import Component as Cpt
from ophyd import Device
from nslsii.devices import TwoButtonShutter


class TwoButtonShutterNC(TwoButtonShutter):
    """TwoButtonShutter variant that makes stop() a no-op (normally-closed logic)."""

    def stop(self, *args):
        ...


class TriState(Device):
    """Three-position valve: Open / Soft (partial) / Close.

    Uses two TwoButtonShutterNC sub-devices on suffixes ``V:1}`` and
    ``V:1_Soft}``.  The prefix must include everything up to (but not
    including) the ``V:`` token, e.g. ``"XF:11BMB-VA{Chm:Smpl-V"``.

    States
    ------
    ``"Open"``  : full shutter open, soft valve unchanged
    ``"Soft"``  : soft valve open, full valve closed
    ``"Close"`` : both valves closed
    """

    full = Cpt(TwoButtonShutterNC, "V:1}")
    soft = Cpt(TwoButtonShutterNC, "V:1_Soft}")

    def set(self, value: str):
        if value == "Open":
            return self.full.set("Open")
        elif value == "Soft":
            return self.soft.set("Open") & self.full.set("Close")
        elif value == "Close":
            return self.full.set("Close") & self.soft.set("Close")
        else:
            raise ValueError("value must be one of {'Open', 'Close', 'Soft'}")
