"""A fake trainer so the app can be tried and developed without hardware."""
from __future__ import annotations

import asyncio
import math
import random
from typing import Callable

from .ftms import BikeData

SIMULATOR_ADDRESS = "simulator"

RIDER_KG = 80.0
CDA = 0.32
CRR = 0.004
AIR_DENSITY = 1.2


def speed_for_power(watts: float, grade_pct: float) -> float:
    """Steady-state road speed (km/h) for a power output on a given grade."""
    slope = math.atan(grade_pct / 100)
    resist = RIDER_KG * 9.81 * (CRR * math.cos(slope) + math.sin(slope))

    def power_at(v: float) -> float:
        return v * resist + 0.5 * AIR_DENSITY * CDA * v**3

    lo, hi = 0.0, 30.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if power_at(mid) < watts:
            lo = mid
        else:
            hi = mid
    return lo * 3.6


class SimulatedPowerMeter:
    """A left-only crank power meter that reads a few percent below the (simulated) trainer."""

    address = SIMULATOR_ADDRESS
    OFFSET = 0.96  # a slightly weaker left leg, doubled

    def __init__(self, trainer_power: Callable[[], float], on_data: Callable):
        self._trainer_power = trainer_power
        self._on_data = on_data
        self._task: asyncio.Task | None = None
        self._rng = random.Random()

    async def connect(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        from .power_meter import PowerMeterReading

        while True:
            await asyncio.sleep(0.5)
            power = max(0.0, self._trainer_power() * self.OFFSET + self._rng.gauss(0, 6))
            self._on_data(PowerMeterReading(round(power), 89 + self._rng.gauss(0, 2) if power > 0 else 0.0))


class SimulatedTrainer:
    address = SIMULATOR_ADDRESS

    def __init__(self, on_data: Callable[[BikeData], None], on_disconnect: Callable[[], None]):
        self._on_data = on_data
        self._task: asyncio.Task | None = None
        self._target: int | None = None
        self._grade = 0.0
        self._power = 0.0
        self._heart_rate = 70.0
        self._rng = random.Random()

    async def connect(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def set_target_power(self, watts: int) -> None:
        self._target = watts

    async def set_grade(self, grade_pct: float) -> None:
        self._target = None
        self._grade = grade_pct

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            # In slope mode the virtual rider pushes harder uphill.
            goal = self._target if self._target is not None else max(60.0, 170 + 25 * self._grade)
            self._power += (goal - self._power) * 0.35
            power = max(0.0, self._power + self._rng.gauss(0, 5))
            grade = self._grade if self._target is None else 0.0
            self._heart_rate += (60 + 0.45 * power - self._heart_rate) * 0.01
            self._on_data(
                BikeData(
                    speed_kmh=speed_for_power(power, grade),
                    cadence_rpm=max(0.0, 88 + self._rng.gauss(0, 2)),
                    power_w=round(power),
                    heart_rate=round(self._heart_rate),
                )
            )
