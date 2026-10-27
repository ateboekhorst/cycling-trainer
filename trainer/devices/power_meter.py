"""Bluetooth power meters (standard Cycling Power Service), e.g. on the crank or pedals.

Single-sided meters (left only) measure one leg and report double that as total power; the app
uses the value as reported.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from bleak import BleakClient

from .common import BleDevice, uuid16

CYCLING_POWER_SERVICE = uuid16(0x1818)
POWER_MEASUREMENT = uuid16(0x2A63)

CRANK_TIME_UNIT = 1024  # crank event times are in 1/1024 s
CADENCE_HOLD = 3.0  # seconds without a new crank event before cadence drops to 0


@dataclass
class PowerData:
    power_w: int
    crank_revs: int | None = None  # cumulative, wraps at 65536
    crank_time: int | None = None  # last crank event, 1/1024 s, wraps at 65536


def parse_power_measurement(data: bytes) -> PowerData | None:
    """Decode a Cycling Power Measurement (0x2A63) notification."""
    if len(data) < 4:
        return None
    flags, power = struct.unpack_from("<Hh", data, 0)
    pos = 4
    sizes_before_crank = [(0x0001, 1), (0x0004, 2), (0x0010, 6)]  # balance, accumulated torque, wheel data
    for flag, size in sizes_before_crank:
        if flags & flag:
            pos += size
    out = PowerData(power_w=max(0, power))
    if flags & 0x0020 and len(data) >= pos + 4:
        out.crank_revs, out.crank_time = struct.unpack_from("<HH", data, pos)
    return out


class CadenceFromCrank:
    """Turns cumulative crank revolutions and event times into cadence (rpm)."""

    def __init__(self):
        self._last: tuple[int, int] | None = None
        self._cadence = 0.0
        self._seconds_since_event = 0.0

    def update(self, revs: int, event_time: int, elapsed: float) -> float:
        """`elapsed` is the time in seconds since the previous notification."""
        if self._last is None:
            self._last = (revs, event_time)
            return 0.0
        d_revs = (revs - self._last[0]) % 65536
        d_time = (event_time - self._last[1]) % 65536
        if d_revs and d_time:
            self._cadence = d_revs * 60 * CRANK_TIME_UNIT / d_time
            self._seconds_since_event = 0.0
            self._last = (revs, event_time)
        else:
            self._seconds_since_event += elapsed
            if self._seconds_since_event > CADENCE_HOLD:
                self._cadence = 0.0
        return self._cadence


@dataclass
class PowerMeterReading:
    power_w: int
    cadence_rpm: float | None


class PowerMeter(BleDevice):
    def __init__(self, address: str, on_data: Callable[[PowerMeterReading], None], on_disconnect: Callable[[], None]):
        super().__init__(address, on_disconnect)
        self._on_data = on_data
        self._cadence = CadenceFromCrank()
        self._last_notification: float | None = None

    async def _setup(self, client: BleakClient) -> None:
        await client.start_notify(POWER_MEASUREMENT, self._handle_measurement)

    def _handle_measurement(self, _char, data: bytearray) -> None:
        import time

        parsed = parse_power_measurement(bytes(data))
        if parsed is None:
            return
        now = time.monotonic()
        elapsed = now - self._last_notification if self._last_notification else 0.0
        self._last_notification = now
        cadence = None
        if parsed.crank_revs is not None:
            cadence = self._cadence.update(parsed.crank_revs, parsed.crank_time, elapsed)
        self._on_data(PowerMeterReading(parsed.power_w, cadence))
