"""Bluetooth FTMS (Fitness Machine Service) smart trainer support.

Elite Suito, Direto, Justo and Avanti (and most other smart trainers) expose FTMS:
Indoor Bike Data notifications carry power, cadence and speed, and the Fitness
Machine Control Point accepts ERG (target power) and simulation (grade) commands.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass
from typing import Callable

from bleak import BleakClient

from .common import BleDevice, uuid16

log = logging.getLogger(__name__)

FTMS_SERVICE = uuid16(0x1826)
INDOOR_BIKE_DATA = uuid16(0x2AD2)
CONTROL_POINT = uuid16(0x2AD9)

OP_REQUEST_CONTROL = 0x00
OP_SET_TARGET_POWER = 0x05
OP_START = 0x07
OP_SET_SIMULATION = 0x11
OP_RESPONSE = 0x80

RESULT_SUCCESS = 0x01
RESULT_CONTROL_NOT_PERMITTED = 0x05

RESPONSE_TIMEOUT = 3.0


@dataclass
class BikeData:
    """One Indoor Bike Data update. Fields the trainer did not send are None."""

    speed_kmh: float | None = None
    cadence_rpm: float | None = None
    power_w: int | None = None
    heart_rate: int | None = None


class _Reader:
    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos

    def take(self, fmt: str) -> int:
        (value,) = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += struct.calcsize(fmt)
        return value

    def skip(self, size: int) -> None:
        if self.pos + size > len(self.data):
            raise struct.error("truncated")
        self.pos += size


def parse_indoor_bike_data(data: bytes) -> BikeData:
    """Decode an Indoor Bike Data (0x2AD2) notification per the FTMS spec."""
    out = BikeData()
    if len(data) < 2:
        return out
    flags = int.from_bytes(data[0:2], "little")
    r = _Reader(data, 2)
    try:
        if not flags & 0x0001:  # "More Data" clear means instantaneous speed is present
            out.speed_kmh = r.take("<H") / 100
        if flags & 0x0002:  # average speed
            r.skip(2)
        if flags & 0x0004:
            out.cadence_rpm = r.take("<H") / 2
        if flags & 0x0008:  # average cadence
            r.skip(2)
        if flags & 0x0010:  # total distance (uint24)
            r.skip(3)
        if flags & 0x0020:  # resistance level
            r.skip(2)
        if flags & 0x0040:
            out.power_w = r.take("<h")
        if flags & 0x0080:  # average power
            r.skip(2)
        if flags & 0x0100:  # expended energy: total, per hour, per minute
            r.skip(5)
        if flags & 0x0200:
            out.heart_rate = r.take("<B")
    except struct.error:
        log.debug("Truncated Indoor Bike Data: %s", data.hex())
    return out


class FtmsTrainer(BleDevice):
    def __init__(self, address: str, on_data: Callable[[BikeData], None], on_disconnect: Callable[[], None]):
        super().__init__(address, on_disconnect)
        self._on_data = on_data
        self._lock = asyncio.Lock()
        self._pending_op: int | None = None
        self._response: asyncio.Future[int] | None = None

    async def _setup(self, client: BleakClient) -> None:
        # Indications on the control point must be enabled before writing to it.
        await client.start_notify(CONTROL_POINT, self._handle_response)
        await client.start_notify(INDOOR_BIKE_DATA, self._handle_bike_data)
        await self._take_control()

    async def set_target_power(self, watts: int) -> None:
        await self._command(struct.pack("<Bh", OP_SET_TARGET_POWER, int(watts)))

    async def set_grade(self, grade_pct: float, crr: float = 0.004, cw: float = 0.51) -> None:
        payload = struct.pack(
            "<BhhBB", OP_SET_SIMULATION, 0, round(grade_pct * 100), round(crr * 10000), round(cw * 100)
        )
        await self._command(payload)

    async def _take_control(self) -> None:
        result = await self._write(bytes([OP_REQUEST_CONTROL]))
        if result != RESULT_SUCCESS:
            log.warning("Trainer refused control (result %s); is another app connected?", result)
            return
        await self._write(bytes([OP_START]))

    async def _command(self, payload: bytes) -> None:
        result = await self._write(payload)
        if result == RESULT_CONTROL_NOT_PERMITTED:
            # Trainers drop control after a reset or reconnect; ask again and retry once.
            await self._take_control()
            result = await self._write(payload)
        if result != RESULT_SUCCESS:
            log.warning("Trainer rejected command %s (result %s)", payload.hex(), result)

    async def _write(self, payload: bytes) -> int | None:
        async with self._lock:
            if self._client is None:
                return None
            self._pending_op = payload[0]
            self._response = asyncio.get_running_loop().create_future()
            try:
                await self._client.write_gatt_char(CONTROL_POINT, payload, response=True)
                return await asyncio.wait_for(self._response, RESPONSE_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning("No control point response to %s", payload.hex())
                return None
            finally:
                self._response = None

    def _handle_response(self, _char, data: bytearray) -> None:
        if len(data) < 3 or data[0] != OP_RESPONSE or data[1] != self._pending_op:
            return
        if self._response is not None and not self._response.done():
            self._response.set_result(data[2])

    def _handle_bike_data(self, _char, data: bytearray) -> None:
        self._on_data(parse_indoor_bike_data(bytes(data)))
