"""Bluetooth heart rate strap support (standard Heart Rate Service)."""
from __future__ import annotations

from typing import Callable

from bleak import BleakClient

from .common import BleDevice, uuid16

HR_SERVICE = uuid16(0x180D)
HR_MEASUREMENT = uuid16(0x2A37)


def parse_heart_rate(data: bytes) -> int | None:
    """Decode a Heart Rate Measurement (0x2A37) notification."""
    if len(data) < 2:
        return None
    if data[0] & 0x01:  # 16-bit value
        return int.from_bytes(data[1:3], "little") if len(data) >= 3 else None
    return data[1]


class HeartRateMonitor(BleDevice):
    def __init__(self, address: str, on_data: Callable[[int], None], on_disconnect: Callable[[], None]):
        super().__init__(address, on_disconnect)
        self._on_data = on_data

    async def _setup(self, client: BleakClient) -> None:
        await client.start_notify(HR_MEASUREMENT, self._handle_measurement)

    def _handle_measurement(self, _char, data: bytearray) -> None:
        bpm = parse_heart_rate(bytes(data))
        if bpm:
            self._on_data(bpm)
