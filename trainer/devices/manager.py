"""Runs all Bluetooth work on a background asyncio loop and reports to the UI via Qt signals.

Bleak (WinRT) needs a multi-threaded COM apartment, which the Qt main thread is not,
so BLE lives on its own thread. Public methods are called from the UI thread; signals
are emitted from the BLE thread and Qt queues them onto the UI thread.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable

from bleak import BleakScanner
from PySide6.QtCore import QObject, Signal

from .ftms import FTMS_SERVICE, FtmsTrainer
from .hrm import HR_SERVICE, HeartRateMonitor
from .power_meter import CYCLING_POWER_SERVICE, PowerMeter
from .simulator import SIMULATOR_ADDRESS, SimulatedPowerMeter, SimulatedTrainer

log = logging.getLogger(__name__)

RECONNECT_DELAY = 3.0


@dataclass
class ScanResult:
    name: str
    address: str
    rssi: int
    kinds: set[str] = field(default_factory=set)  # "trainer", "hr" and/or "power"


class _Slot:
    """One device connection (trainer or HR strap) that reconnects automatically when lost."""

    def __init__(self, loop: asyncio.AbstractEventLoop, factory: Callable, state: Signal):
        self._loop = loop
        self._factory = factory  # (address, on_disconnect) -> device
        self._state = state
        self.device = None
        self.wanted: str | None = None
        self.on_connected: Callable[[], object] | None = None

    async def connect(self, address: str) -> None:
        await self.disconnect()
        self.wanted = address
        self._state.emit("connecting")
        try:
            await self._open(address)
        except Exception:
            self.wanted = None
            self._state.emit("disconnected")
            raise

    async def disconnect(self) -> None:
        self.wanted = None
        device, self.device = self.device, None
        if device is not None:
            await device.disconnect()
        self._state.emit("disconnected")

    async def _open(self, address: str) -> None:
        device = self._factory(address, self._lost)
        await device.connect()
        self.device = device
        self._state.emit("connected")
        if self.on_connected is not None:
            await self.on_connected()

    def _lost(self) -> None:
        self._loop.call_soon_threadsafe(self._start_reconnect)

    def _start_reconnect(self) -> None:
        self.device = None
        if self.wanted is not None:
            self._state.emit("reconnecting")
            self._loop.create_task(self._reconnect(self.wanted))

    async def _reconnect(self, address: str) -> None:
        while self.wanted == address and self.device is None:
            await asyncio.sleep(RECONNECT_DELAY)
            if self.wanted != address:
                return
            try:
                await self._open(address)
            except Exception as exc:
                log.info("Reconnect to %s failed: %s", address, exc)


class DeviceManager(QObject):
    trainer_data = Signal(object)  # BikeData
    heart_rate = Signal(int)
    trainer_state = Signal(str)  # disconnected | connecting | connected | reconnecting
    hr_state = Signal(str)
    power_meter_data = Signal(object)  # PowerMeterReading
    power_meter_state = Signal(str)
    scan_finished = Signal(list)  # list[ScanResult]
    error = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="ble", daemon=True)
        self._thread.start()
        self._scanning = False
        self._closed = False
        self._trainer_command: tuple[str, float] | None = None

        self._sim_trainer_power = 0.0  # feeds the simulated power meter

        def on_trainer_data(data):
            if data.power_w is not None:
                self._sim_trainer_power = data.power_w
            self.trainer_data.emit(data)

        def make_trainer(address: str, on_disconnect):
            if address == SIMULATOR_ADDRESS:
                return SimulatedTrainer(on_trainer_data, on_disconnect)
            return FtmsTrainer(address, self.trainer_data.emit, on_disconnect)

        def make_power_meter(address: str, on_disconnect):
            if address == SIMULATOR_ADDRESS:
                return SimulatedPowerMeter(lambda: self._sim_trainer_power, self.power_meter_data.emit)
            return PowerMeter(address, self.power_meter_data.emit, on_disconnect)

        self._trainer = _Slot(self._loop, make_trainer, self.trainer_state)
        self._trainer.on_connected = self._send_trainer_command
        self._hrm = _Slot(
            self._loop, lambda address, lost: HeartRateMonitor(address, self.heart_rate.emit, lost), self.hr_state
        )
        self._power_meter = _Slot(self._loop, make_power_meter, self.power_meter_state)

    # --- called from the UI thread ---

    def scan(self, timeout: float = 6.0) -> None:
        if not self._scanning:
            self._submit(self._scan(timeout))

    def connect_trainer(self, address: str) -> None:
        self._submit(self._trainer.connect(address))

    def disconnect_trainer(self) -> None:
        self._submit(self._trainer.disconnect())

    def connect_hrm(self, address: str) -> None:
        self._submit(self._hrm.connect(address))

    def disconnect_hrm(self) -> None:
        self._submit(self._hrm.disconnect())

    def connect_power_meter(self, address: str) -> None:
        self._submit(self._power_meter.connect(address))

    def disconnect_power_meter(self) -> None:
        self._submit(self._power_meter.disconnect())

    def set_target_power(self, watts: int) -> None:
        self._set_trainer_command(("power", watts))

    def set_grade(self, grade_pct: float) -> None:
        self._set_trainer_command(("grade", grade_pct))

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True

        async def close_all():
            await self._trainer.disconnect()
            await self._hrm.disconnect()
            await self._power_meter.disconnect()

        try:
            asyncio.run_coroutine_threadsafe(close_all(), self._loop).result(timeout=5)
        except Exception:
            log.exception("Error while disconnecting devices")
        self._loop.call_soon_threadsafe(self._loop.stop)

    # --- BLE thread ---

    def _submit(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop).add_done_callback(self._report_failure)

    def _report_failure(self, future: Future) -> None:
        if future.cancelled() or future.exception() is None:
            return
        exc = future.exception()
        log.error("Bluetooth task failed", exc_info=exc)
        self.error.emit(str(exc) or type(exc).__name__)

    def _set_trainer_command(self, command: tuple[str, float]) -> None:
        if command != self._trainer_command:
            self._trainer_command = command
            self._submit(self._send_trainer_command())

    async def _send_trainer_command(self) -> None:
        trainer, command = self._trainer.device, self._trainer_command
        if trainer is None or command is None:
            return
        kind, value = command
        if kind == "power":
            await trainer.set_target_power(int(value))
        else:
            await trainer.set_grade(value)

    async def _scan(self, timeout: float) -> None:
        self._scanning = True
        try:
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        finally:
            self._scanning = False
        results = []
        for device, adv in found.values():
            uuids = {u.lower() for u in adv.service_uuids}
            kinds = {kind for kind, uuid in (("trainer", FTMS_SERVICE), ("hr", HR_SERVICE)) if uuid in uuids}
            if CYCLING_POWER_SERVICE in uuids and "trainer" not in kinds:
                kinds.add("power")  # trainers advertise Cycling Power too; list only real power meters
            if kinds:
                name = device.name or adv.local_name or device.address
                results.append(ScanResult(name, device.address, adv.rssi, kinds))
        results.sort(key=lambda r: -r.rssi)
        self.scan_finished.emit(results)
