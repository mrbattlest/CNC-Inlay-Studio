"""Thin pyserial wrapper. Owns the Serial port handle.

Not thread-safe by design — only the worker QThread should call its methods.
The MachineController routes all UI calls through Qt signals so cross-thread
access is queued automatically.
"""
from __future__ import annotations

from dataclasses import dataclass

import serial
from serial.tools import list_ports as serial_list_ports

from cnc_inlay.control.grbl import BAUD


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str

    def display(self) -> str:
        if self.description and self.description != self.device:
            return f"{self.device}  —  {self.description}"
        return self.device


def list_serial_ports() -> list[PortInfo]:
    """Enumerate serial ports, putting likely USB-CNC bridges first.

    Lunyee 3018 PRO MAX uses a CH340 USB-serial bridge; we sort it (and other
    common USB-serial chips) to the top of the list.
    """
    ports = list(serial_list_ports.comports())

    def score(p) -> int:
        desc = (p.description or "").lower()
        for keyword in (
            "ch340", "ch341", "wchusb",
            "silabs", "cp210",
            "ftdi", "ft232",
            "usb-serial", "usb serial",
        ):
            if keyword in desc:
                return 0
        if "usb" in desc:
            return 1
        return 2

    ports.sort(key=score)
    return [
        PortInfo(device=p.device, description=p.description or "")
        for p in ports
    ]


class SerialIO:
    """Owns a serial.Serial handle, exposes read/write helpers."""

    def __init__(self, port: str, baud: int = BAUD) -> None:
        self._port_name = port
        self._baud = baud
        self._port: serial.Serial | None = None

    def open(self) -> None:
        self._port = serial.Serial(self._port_name, self._baud, timeout=0.05)

    def close(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None

    @property
    def is_open(self) -> bool:
        return self._port is not None and self._port.is_open

    def write(self, data: bytes) -> None:
        if self._port is None or not self._port.is_open:
            return
        self._port.write(data)
        try:
            self._port.flush()
        except Exception:
            pass

    def write_line(self, line: str) -> None:
        if not line.endswith("\n"):
            line = line + "\n"
        self.write(line.encode("ascii", errors="replace"))

    def read_line(self) -> str | None:
        """Read one line. Returns None when no data is available within timeout."""
        if self._port is None or not self._port.is_open:
            return None
        try:
            raw = self._port.readline()
        except Exception:
            return None
        if not raw:
            return None
        try:
            return raw.decode("ascii", errors="replace").strip()
        except Exception:
            return None
