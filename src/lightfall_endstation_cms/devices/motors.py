"""Motor and slit device classes for CMS (11-BM) endstation.

Extracted from: profile-collection/startup/10-motors.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ophyd import Component as Cpt
from ophyd import Device, EpicsMotor, EpicsSignal


# ---------------------------------------------------------------------------
# Configurable mixin
# ---------------------------------------------------------------------------

class Configurable:
    """Mixin for multi-motor devices: save/load named positions and move motors.

    Subclasses must set:
      _config_motors : list[str]  – component attribute names to track
      _config_file   : str | Path | None  – JSON config file path

    Features:
      get(name)            – load a saved position without moving
      goto(name)           – load and move all motors to saved values
      save_position(name)  – store current motor positions under *name*
      mov(motor, value)    – absolute move (short alias)
      movr(motor, delta)   – relative move (short alias)
    """

    _config_motors: list = []
    _config_file = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, name: str):
        """Load a saved position configuration without moving motors."""
        return self.load_position(name)

    def goto(self, name: str):
        """Load a saved position and move all motors there."""
        positions_dict = self._load_config()
        entries = positions_dict.get(name)
        if entries and isinstance(entries, list):
            target_positions = entries[-1]
            print(f"Moving '{self.name}' to position '{name}'...")
            for motor_name in self._config_motors:
                if hasattr(self, motor_name) and motor_name in target_positions:
                    getattr(self, motor_name).move(target_positions[motor_name])
            self.show_position()
        else:
            print(f"No saved position found for '{name}'.")

    def save_position(self, name: str):
        """Save current motor positions under *name*."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = self._get_positions_dict()
        entry["timestamp"] = timestamp
        positions = self._load_config()
        positions.setdefault(name, []).append(entry)
        self._positions = positions
        self._save_config()
        print(f"Saved position '{name}' at {timestamp}.")

    def show_position(self):
        """Display current motor positions."""
        self._sync()
        print(f"Device '{self.name}' positions:")
        for motor_name in self._config_motors:
            if hasattr(self, motor_name):
                print(f"  {motor_name} = {getattr(self, motor_name).position}")

    def mov(self, motor_name: str, value: float):
        """Absolute move (short alias)."""
        return self.absolute_move(motor_name, value)

    def movr(self, motor_name: str, delta: float):
        """Relative move (short alias)."""
        return self.relative_move(motor_name, delta)

    def absolute_move(self, motor_name: str, value: float):
        """Move a motor to an absolute position."""
        if motor_name not in self._config_motors or not hasattr(self, motor_name):
            print(f"Invalid motor name: {motor_name}")
            return
        getattr(self, motor_name).move(value)
        print(f"{motor_name} moved to {value}")

    def relative_move(self, motor_name: str, delta: float):
        """Move a motor relative to current position."""
        if motor_name not in self._config_motors or not hasattr(self, motor_name):
            print(f"Invalid motor name: {motor_name}")
            return
        motor = getattr(self, motor_name)
        new_pos = motor.position + delta
        motor.move(new_pos)
        print(f"{motor_name} moved relatively by {delta} -> {new_pos}")

    def load_position(self, name: str):
        """Load a saved position configuration by name (no motion)."""
        positions = self._load_config()
        entries = positions.get(name)
        if entries:
            latest = entries[-1]
            print(f"Loaded position '{name}' from {latest.get('timestamp', 'unknown')}")
            for motor_name in self._config_motors:
                if motor_name in latest and hasattr(self, motor_name):
                    setattr(self, f"_{motor_name}", latest[motor_name])
            return latest
        print(f"No saved position found for '{name}'.")
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_config(self) -> dict:
        config_file = self._resolved_config_path()
        if config_file.exists():
            with open(config_file) as f:
                return json.load(f)
        return {}

    def _save_config(self):
        config_file = self._resolved_config_path()
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w") as f:
            json.dump(self._positions, f, indent=2)

    def _resolved_config_path(self) -> Path:
        if self._config_file is None:
            self._config_file = Path(f"{self.name}_config.cfg")
        return Path(self._config_file)

    def _sync(self):
        for motor_name in self._config_motors:
            if hasattr(self, motor_name):
                setattr(self, f"_{motor_name}", getattr(self, motor_name).position)

    def _get_positions_dict(self) -> dict:
        return {
            m: getattr(self, m).position
            for m in self._config_motors
            if hasattr(self, m)
        }

    @staticmethod
    def clear_config(config_file):
        """Trim each named position to only the latest entry."""
        path = Path(config_file)
        if not path.exists():
            print(f"Config file '{config_file}' does not exist.")
            return
        with open(path) as f:
            data = json.load(f)
        for key in data:
            if isinstance(data[key], list) and data[key]:
                data[key] = [data[key][-1]]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Configuration file '{config_file}' cleaned: only latest entries retained.")


# ---------------------------------------------------------------------------
# Slit / optic device classes
# ---------------------------------------------------------------------------

class MotorCenterAndGap(Device, Configurable):
    """Center-and-gap slit using four EpicsMotor records.

    Suffix convention: the prefix is everything up to (but not including)
    the closing ``}`` + axis token, e.g. ``"XF:11BMB-OP{Slt:1"``.
    """

    xc = Cpt(EpicsMotor, "-Ax:XC}Mtr")
    yc = Cpt(EpicsMotor, "-Ax:YC}Mtr")
    xg = Cpt(EpicsMotor, "-Ax:XG}Mtr")
    yg = Cpt(EpicsMotor, "-Ax:YG}Mtr")

    _config_motors = ["xc", "yc", "xg", "yg"]

    def __init__(self, *args, config_file=None, **kwargs):
        super().__init__(*args, **kwargs)
        if config_file is not None:
            self._config_file = Path(config_file)
        self._positions = self._load_config()


class Blades(Device):
    """FMB Oxford blade slit: physical T/B/O/I blades plus virtual center/gap."""

    tp = Cpt(EpicsMotor, "-Ax:T}Mtr")
    bt = Cpt(EpicsMotor, "-Ax:B}Mtr")
    ob = Cpt(EpicsMotor, "-Ax:O}Mtr")
    ib = Cpt(EpicsMotor, "-Ax:I}Mtr")
    xc = Cpt(EpicsMotor, "-Ax:XCtr}Mtr")
    yc = Cpt(EpicsMotor, "-Ax:YCtr}Mtr")
    xg = Cpt(EpicsMotor, "-Ax:XGap}Mtr")
    yg = Cpt(EpicsMotor, "-Ax:YGap}Mtr")


class Filter(Device):
    """Single attenuator foil (in/out pneumatic)."""

    sts = Cpt(EpicsSignal, "Pos-Sts")
    in_cmd = Cpt(EpicsSignal, "In-Cmd")
    out_cmd = Cpt(EpicsSignal, "Out-Cmd")
