"""Serial/socket-based instrument classes for CMS (11-BM) endstation.

Extracted from: profile-collection/startup/41-endstation-serial-dev.py

NOTE: These devices open live TCP sockets in __init__ and therefore connect
to the instrument hardware immediately on instantiation.  They are not
suitable for happi lazy-loading patterns without a connection-deferred wrapper.
They are included here as importable classes; instantiation should be done
explicitly in the session startup if the corresponding hardware is present.

Moxa NPort server address: ``10.68.82.73`` (updated 2020-11-12).
"""

from __future__ import annotations

import re
import socket
import time

import numpy
from ophyd import Device


# ---------------------------------------------------------------------------
# Agilent 34970A data acquisition unit
# ---------------------------------------------------------------------------

class Agilent_34970A(Device):
    """Agilent 34970A data acquisition / switch unit.

    Communicates over a Moxa NPort TCP socket.
    Includes:
      HP34901  – 20-channel multiplexer (slot 1)
      HP34907  – DIO/DAC card (slot 3)

    Moxa port 9 → socket ``10.68.82.73:4009``
    """

    def __init__(self, prefix="", *args, read_attrs=None,
                 configuration_attrs=None, name="Agilent_34970A",
                 parent=None, **kwargs):
        super().__init__(prefix=prefix, *args, read_attrs=read_attrs,
                         configuration_attrs=configuration_attrs, name=name,
                         parent=parent, **kwargs)
        self.connect_socket()
        self.HP34901_channel = 100
        self.HP34907_channel = 300

    # --- Socket helpers ---

    def connect_socket(self):
        self.server_address = "10.68.82.73"
        self.port_number = 9
        self.server_port = 4000 + self.port_number
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print(self.server_address)
        self.sock.connect((self.server_address, self.server_port))
        self.sock.settimeout(0.5)

    def disconnect_socket(self):
        self.sock.close()

    def send_socket(self, msg: str):
        self.sock.send(msg.encode("ascii", "ignore"))

    def send_get_reply(self, msg: str, verbosity: int = 3) -> str:
        self.send_socket(msg)
        time.sleep(0.5)
        return self.read_socket(verbosity=verbosity)

    def read_socket(self, timeout_s: float = 3, verbosity: int = 3) -> str:
        start_time = time.time()
        terminator = chr(0x18)
        amount_received = 0
        amount_cutoff = 5000
        txt = ""
        msg_received = ""
        while (terminator not in txt
               and time.time() - start_time < timeout_s
               and amount_received < amount_cutoff):
            try:
                data = self.sock.recv(1)
            except Exception:
                break
            amount_received += len(data)
            txt = data.decode("ascii")
            msg_received += txt
        msg_received = msg_received.replace(terminator, "")
        if time.time() - start_time > timeout_s:
            if verbosity >= 1:
                print(f"Read timeout after {time.time() - start_time:.1f} s.")
            return ""
        if verbosity >= 2:
            print(msg_received)
        return msg_received

    # --- HP34901 20-channel multiplexer ---

    def reset_Agilent34970A(self, verbosity=3):
        self.send_socket("*RST\n")

    def reset_HP34901(self, verbosity=3):
        self.send_socket(f"SYSTEM:CPON {self.HP34901_channel}\n")

    def readDCV(self, channel: int, verbosity: int = 1) -> float:
        if not 1 <= channel <= 20:
            print("Invalid multiplexer channel number; must be 1-20.")
            return 0.0
        rc = self.HP34901_channel + channel
        self.send_socket(f"INPUT:IMP:AUTO ON, (@{rc})\n")
        self.send_socket(f"SENSE:ZERO:AUTO ON, (@{rc})\n")
        self.send_socket(f"MEAS:VOLT:DC? AUTO,MAX, (@{rc})\n")
        dcv = float(self.read_socket(verbosity=1))
        if verbosity > 1:
            print(f"Channel {channel} is {dcv} VDC.")
        return dcv

    # --- HP34907 DIO/DAC card ---

    def setDAC(self, channel: int, voltage: float, verbosity: int = 1) -> int:
        if not 1 <= channel <= 2:
            print("Invalid DAC channel number; must be 1 or 2.")
            return 0
        if not -12.0 <= voltage <= 12.0:
            print("Invalid DAC voltage; must be within +/-12 V.")
            return 0
        dc = self.HP34907_channel + channel + 3
        self.send_socket(f"SOURCE:VOLTAGE {voltage}, (@{dc})\n")
        if verbosity > 1:
            print(f"DAC output channel {channel} set to {voltage} VDC.")
        return 1

    def readDAC(self, channel: int, verbosity: int = 1) -> float:
        if not 1 <= channel <= 2:
            print("Invalid DAC channel number; must be 1 or 2.")
            return 0.0
        dc = self.HP34907_channel + channel + 3
        self.send_socket(f"SOURCE:VOLTAGE? (@{dc})\n")
        voltage = float(self.read_socket(verbosity=1))
        if verbosity > 1:
            print(f"DAC output channel {channel} set to {voltage} VDC.")
        return voltage

    def writeByteDIO(self, channel: int, value: int, verbosity: int = 1) -> int:
        if not 1 <= channel <= 2:
            print("Invalid DIO channel number; must be 1 or 2.")
            return 0
        dc = self.HP34907_channel + channel
        self.send_socket(f"SOURCE:DIGITAL:DATA:BYTE {value}, (@{dc})\n")
        if verbosity > 1:
            print(f"DIO output channel {channel} set to {value}.")
        return 1

    def readByteDIO(self, channel: int, verbosity: int = 1) -> int:
        if not 1 <= channel <= 2:
            print("Invalid DIO channel number; must be 1 or 2.")
            return 0
        dc = self.HP34907_channel + channel
        self.send_socket(f"SOURCE:DIGITAL:DATA:BYTE? (@{dc})\n")
        value = int(self.read_socket(verbosity=1))
        if verbosity > 1:
            print(f"DIO output channel {channel} = {value}.")
        return value


# ---------------------------------------------------------------------------
# Keithley 2000 digital multimeter
# ---------------------------------------------------------------------------

class Keithley_2000(Device):
    """Keithley 2000 DMM over Moxa NPort TCP socket.

    Moxa port 10 → socket ``10.68.82.73:4010``
    """

    def __init__(self, prefix="", *args, read_attrs=None,
                 configuration_attrs=None, name="Keithley_2000",
                 parent=None, **kwargs):
        super().__init__(prefix=prefix, *args, read_attrs=read_attrs,
                         configuration_attrs=configuration_attrs, name=name,
                         parent=parent, **kwargs)
        self.connect_socket()

    def connect_socket(self):
        self.server_address = "10.68.82.73"
        self.port_number = 10
        self.server_port = 4000 + self.port_number
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print(self.server_address)
        self.sock.connect((self.server_address, self.server_port))
        self.sock.settimeout(0.5)

    def disconnect_socket(self):
        self.sock.close()

    def send_socket(self, msg: str):
        self.sock.send(msg.encode("ascii", "ignore"))

    def send_get_reply(self, msg: str, verbosity: int = 3) -> str:
        self.send_socket(msg)
        time.sleep(0.5)
        return self.read_socket(verbosity=verbosity)

    def read_socket(self, timeout_s: float = 3, verbosity: int = 3) -> str:
        start_time = time.time()
        terminator = chr(0x18)
        txt = ""
        msg_received = ""
        amount_received = 0
        while (terminator not in txt
               and time.time() - start_time < timeout_s
               and amount_received < 5000):
            try:
                data = self.sock.recv(1)
            except Exception:
                break
            amount_received += len(data)
            txt = data.decode("ascii")
            msg_received += txt
        msg_received = msg_received.replace(terminator, "")
        if time.time() - start_time > timeout_s:
            if verbosity >= 1:
                print(f"Read timeout after {time.time() - start_time:.1f} s.")
            return ""
        if verbosity >= 2:
            print(msg_received)
        return msg_received

    def selectChannel(self, channel: int, verbosity: int = 1) -> int:
        if not 1 <= channel <= 10:
            print("Invalid channel; must be 1-10.")
            return 0
        self.send_socket(f":ROUT:CLOS (@{channel})\r")
        if verbosity > 1:
            print(f"Keithley 2000 channel set to {channel}.")
        return 1

    def readOhm(self, channel: int, verbosity: int = 1) -> float:
        self.selectChannel(channel, verbosity=1)
        time.sleep(0.2)
        self.send_socket(":SENS:FUNC 'RES'\r")
        time.sleep(0.1)
        self.send_socket(":SENS:DATA?\r")
        time.sleep(0.1)
        ohm = float(self.read_socket(verbosity=1))
        if verbosity > 1:
            print(f"Resistance on channel {channel}: {ohm} Ω")
        return ohm

    def readDCV(self, channel: int, verbosity: int = 1) -> float:
        self.selectChannel(channel, verbosity=1)
        time.sleep(0.2)
        self.send_socket(":SENS:FUNC 'VOLT:DC'\r")
        time.sleep(0.1)
        self.send_socket(":SENS:DATA?\r")
        time.sleep(0.1)
        dcv = float(self.read_socket(verbosity=1))
        if verbosity > 1:
            print(f"DC voltage on channel {channel}: {dcv} V")
        return dcv

    def readThermister30kohm(self, channel: int, verbosity: int = 1) -> float:
        ohm = self.readOhm(channel, verbosity=1)
        a, b, c = 0.000932681, 0.000221455, 0.000000126
        T = 1.0 / (a + b * numpy.log(ohm) + c * numpy.log(ohm) ** 3) - 273.15
        if verbosity > 1:
            print(f"Temperature (30k-ohm thermistor) on channel {channel}: {T:.2f} °C")
        return T

    def readThermister100kohm(self, channel: int, verbosity: int = 1) -> float:
        ohm = self.readOhm(channel, verbosity=1)
        a, b, c = 0.000827094, 0.000204256, 1.15042e-07
        T = 1.0 / (a + b * numpy.log(ohm) + c * numpy.log(ohm) ** 3) - 273.15
        if verbosity > 1:
            print(f"Temperature (100k-ohm thermistor) on channel {channel}: {T:.2f} °C")
        return T

    def readPt100(self, channel: int, verbosity: int = 1) -> float:
        ohm = self.readOhm(channel, verbosity=1)
        c0, c1, c2, c3, c4, c5, c6, c7 = (
            -245.19, 2.5293, -0.066046, 4.0422e-3,
            -2.0697e-6, -0.025422, 1.6883e-3, -1.3601e-6,
        )
        T = ohm * (c1 + ohm * (c2 + ohm * (c3 + c4 * ohm)))
        T /= 1.0 + ohm * (c5 + ohm * (c6 + c7 * ohm))
        T += c0
        if verbosity > 1:
            print(f"Temperature (Pt100 RTD) on channel {channel}: {T:.2f} °C")
        return T


# ---------------------------------------------------------------------------
# Minichiller temperature controller
# ---------------------------------------------------------------------------

class Minichiller(Device):
    """Minichiller temperature controller over Moxa NPort TCP socket.

    Moxa port 11 → socket ``10.68.82.73:4011``
    """

    def __init__(self, prefix="", *args, read_attrs=None,
                 configuration_attrs=None, name="Minichiller",
                 parent=None, **kwargs):
        super().__init__(prefix=prefix, *args, read_attrs=read_attrs,
                         configuration_attrs=configuration_attrs, name=name,
                         parent=parent, **kwargs)
        self.connect_socket()

    def connect_socket(self):
        self.server_address = "10.68.82.73"
        self.port_number = 11
        self.server_port = 4000 + self.port_number
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print(self.server_address)
        self.sock.connect((self.server_address, self.server_port))
        self.sock.settimeout(0.5)

    def disconnect_socket(self):
        self.sock.close()

    def send_socket(self, msg: str):
        self.sock.send(msg.encode("ascii", "ignore"))

    def read_socket(self, timeout_s: float = 3, verbosity: int = 3) -> str:
        start_time = time.time()
        terminator = chr(0x18)
        txt = ""
        msg_received = ""
        amount_received = 0
        while (terminator not in txt
               and time.time() - start_time < timeout_s
               and amount_received < 5000):
            try:
                data = self.sock.recv(1)
            except Exception:
                break
            amount_received += len(data)
            txt = data.decode("ascii")
            msg_received += txt
        msg_received = msg_received.replace(terminator, "")
        if time.time() - start_time > timeout_s:
            if verbosity >= 1:
                print(f"Read timeout after {time.time() - start_time:.1f} s.")
            return ""
        if verbosity >= 2:
            print(msg_received)
        return msg_received

    def setTemp(self, degC: float, verbosity: int = 1) -> int:
        sign = "-" if degC < 0 else "+"
        ad = abs(degC)
        if ad >= 10:
            sign += "0"
        elif ad >= 1:
            sign += "00"
        elif ad >= 0.1:
            sign += "000"
        else:
            sign += "0000"
        temperature = abs(int(degC * 100))
        self.send_socket(f"SP@{sign}{temperature}\r\n")
        if verbosity > 1:
            self.readTemp(verbosity=verbosity)
        return 1

    def readTemp(self, verbosity: int = 1) -> float:
        self.send_socket("SP?\r\n")
        raw = self.read_socket(verbosity=1)
        degC = int(raw[2:]) / 100.0
        if verbosity > 1:
            print(f"Temperature setpoint: {degC} °C")
        return degC


# ---------------------------------------------------------------------------
# TTL control (via Agilent DIO) – not an ophyd Device
# ---------------------------------------------------------------------------

class TTL_control:
    """TTL port control using the two 8-bit DIO channels on Agilent 34970A.

    Requires a live ``Agilent_34970A`` instance to be passed as *agilent*.

    Note: The original code referenced a bare global ``agilent``.  This class
    has been refactored to take it as an explicit constructor argument.
    """

    def __init__(self, agilent: Agilent_34970A, name: str = "TTL_control",
                 description: str = ""):
        self.agilent = agilent
        self.name = name
        self.description = description

    def readPort(self, unit: int, port: int, verbosity: int = 2) -> int:
        if not 1 <= unit <= 2:
            print("Invalid TTL unit number; must be 1 or 2.")
            return 0
        if not 1 <= port <= 8:
            print("Invalid TTL port number; must be 1-8.")
            return 0
        value = self.agilent.readByteDIO(unit, verbosity=1)
        onoff = int(bin(value)[2:].zfill(8)[-(port)])
        if verbosity > 1:
            print(f"TTL unit {unit} port {port} is currently {onoff}.")
        return onoff

    def setPort(self, unit: int, port: int, onoff: int, verbosity: int = 2) -> int:
        b = self.readPort(unit, port, verbosity=1)
        if onoff == b:
            if verbosity > 1:
                print(f"TTL unit {unit} port {port} already {onoff}.")
            return 0
        value = self.agilent.readByteDIO(unit, verbosity=1)
        if onoff == 1:
            value += 2 ** (port - 1)
        else:
            value -= 2 ** (port - 1)
        self.agilent.writeByteDIO(unit, value, verbosity=1)
        b_new = self.readPort(unit, port, verbosity=1)
        if b_new != onoff:
            print(f"ERROR: TTL unit {unit} port {port} still {b_new}.")
            return 0
        if verbosity > 1:
            print(f"TTL unit {unit} port {port} set to {b_new}.")
        return 1

    def setPortOn(self, unit: int, port: int, verbosity: int = 2) -> int:
        return self.setPort(unit, port, 1, verbosity=verbosity)

    def setPortOff(self, unit: int, port: int, verbosity: int = 2) -> int:
        return self.setPort(unit, port, 0, verbosity=verbosity)
