"""Ride state: collects sensor readings, advances the workout clock and records 1 Hz samples."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .devices.ftms import BikeData
from .devices.power_meter import PowerMeterReading
from .metrics import LiveMetrics
from .workouts import Workout

STALE_AFTER = 3.0  # seconds without data before a reading counts as zero/missing


@dataclass
class Live:
    """Sensor values for one ride tick."""

    power: float = 0.0
    cadence: float | None = None
    speed_kmh: float | None = None
    heart_rate: int | None = None
    meter_power: float | None = None  # the bike's power meter, shown for comparison only


class SensorBuffer:
    """Collects device readings as they arrive (1-4 Hz) and summarises them per tick.

    Power for training always comes from the trainer; a power meter is only recorded alongside it.
    """

    def __init__(self):
        self._power: deque[tuple[float, int]] = deque()
        self._meter_power: deque[tuple[float, int]] = deque()
        self._cadence: tuple[float, float] | None = None
        self._meter_cadence: tuple[float, float] | None = None
        self._speed: tuple[float, float] | None = None
        self._strap_hr: tuple[float, int] | None = None
        self._trainer_hr: tuple[float, int] | None = None

    @staticmethod
    def _append(readings: deque, now: float, watts: int) -> None:
        readings.append((now, max(0, watts)))
        while readings and readings[0][0] < now - 10:
            readings.popleft()

    @staticmethod
    def _average(readings: deque, seconds: float, now: float) -> float | None:
        recent = [w for t, w in readings if t >= now - seconds]
        if recent:
            return sum(recent) / len(recent)
        if readings and readings[-1][0] >= now - STALE_AFTER:
            return float(readings[-1][1])
        return None

    def add_bike_data(self, data: BikeData, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if data.power_w is not None:
            self._append(self._power, now, data.power_w)
        if data.cadence_rpm is not None:
            self._cadence = (now, data.cadence_rpm)
        if data.speed_kmh is not None:
            self._speed = (now, data.speed_kmh)
        if data.heart_rate:
            self._trainer_hr = (now, data.heart_rate)

    def add_power_meter(self, reading: PowerMeterReading, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._append(self._meter_power, now, reading.power_w)
        if reading.cadence_rpm is not None:
            self._meter_cadence = (now, reading.cadence_rpm)

    def add_heart_rate(self, bpm: int, now: float | None = None) -> None:
        self._strap_hr = (time.monotonic() if now is None else now, bpm)

    def power_avg(self, seconds: float, now: float | None = None) -> float:
        """Trainer power over the last `seconds`, 0 when the trainer has gone quiet."""
        return self._average(self._power, seconds, time.monotonic() if now is None else now) or 0.0

    def meter_power_avg(self, seconds: float, now: float | None = None) -> float | None:
        """Power-meter power, or None without a (fresh) power meter."""
        return self._average(self._meter_power, seconds, time.monotonic() if now is None else now)

    def snapshot(self, now: float | None = None) -> Live:
        now = time.monotonic() if now is None else now

        def fresh(reading):
            return reading[1] if reading and reading[0] >= now - STALE_AFTER else None

        cadence = fresh(self._cadence)
        return Live(
            power=self.power_avg(1.5, now),
            cadence=cadence if cadence is not None else fresh(self._meter_cadence),
            speed_kmh=fresh(self._speed),
            heart_rate=fresh(self._strap_hr) or fresh(self._trainer_hr),
            meter_power=self.meter_power_avg(1.5, now),
        )


class RideState(Enum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


@dataclass
class Sample:
    elapsed: int
    time: datetime
    power: int
    cadence: int | None
    speed_kmh: float | None
    heart_rate: int | None
    target: int | None
    distance_m: float
    meter_power: int | None = None  # the bike's power meter, for comparison


class Ride:
    PEDALING_CADENCE = 15
    PEDALING_POWER = 20
    AUTO_PAUSE_AFTER = 3  # seconds without pedaling

    def __init__(self, workout: Workout, ftp: int, auto_pause: bool = True):
        self.workout = workout
        self.ftp = ftp
        self.auto_pause = auto_pause
        self.intensity = 1.0  # scales all workout targets (Up/Down keys)
        self.erg = True
        self.grade = 0.0
        self.state = RideState.READY
        self.elapsed = 0
        self.distance_m = 0.0
        self.samples: list[Sample] = []
        self.metrics = LiveMetrics()
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None
        self._idle = 0
        self._auto_paused = False

    # --- targets ---

    def target_fraction(self) -> float | None:
        return self.workout.target_at(self.elapsed)

    def target_watts(self) -> int | None:
        fraction = self.target_fraction()
        return None if fraction is None else round(fraction * self.ftp * self.intensity)

    @property
    def auto_paused(self) -> bool:
        return self.state == RideState.PAUSED and self._auto_paused

    @property
    def ramp_test_over(self) -> bool:
        """A ramp test ends once the rider stops pedalling after the warm-up."""
        workout = self.workout
        if not workout.ramp_test or self.state == RideState.FINISHED:
            return False
        if self.elapsed <= workout.segments[0].duration:
            return False
        return self.auto_paused or self.workout_complete

    @property
    def workout_complete(self) -> bool:
        return not self.workout.open_ended and self.elapsed >= self.workout.duration

    def erg_target(self) -> int | None:
        """Watts the trainer should hold in ERG mode, or None for slope mode."""
        return self.target_watts() if self.erg else None

    # --- state ---

    def start(self, now: datetime | None = None) -> None:
        if self.state == RideState.READY:
            self.start_time = now or datetime.now().astimezone()
            self.state = RideState.RUNNING

    def pause(self, auto: bool = False) -> None:
        if self.state == RideState.RUNNING:
            self.state = RideState.PAUSED
            self._auto_paused = auto

    def resume(self) -> None:
        if self.state == RideState.PAUSED:
            self.state = RideState.RUNNING
            self._idle = 0

    def toggle(self) -> None:
        if self.state == RideState.READY:
            self.start()
        elif self.state == RideState.RUNNING:
            self.pause()
        elif self.state == RideState.PAUSED:
            self.resume()

    def finish(self, now: datetime | None = None) -> None:
        if self.state in (RideState.RUNNING, RideState.PAUSED):
            self.end_time = self.samples[-1].time if self.samples else (now or datetime.now().astimezone())
        self.state = RideState.FINISHED

    # --- clock ---

    def update(self, live: Live, now: datetime | None = None) -> None:
        """Advance one second: handle auto start/pause and record a sample while running."""
        now = now or datetime.now().astimezone()
        pedaling = (live.cadence or 0) >= self.PEDALING_CADENCE or live.power >= self.PEDALING_POWER

        if self.state == RideState.READY and pedaling:
            self.start(now)
        elif self.state == RideState.PAUSED and pedaling and self._auto_paused:
            self.resume()
        elif self.state == RideState.RUNNING and self.auto_pause:
            self._idle = 0 if pedaling else self._idle + 1
            if self._idle >= self.AUTO_PAUSE_AFTER:
                self.pause(auto=True)

        if self.state != RideState.RUNNING:
            return
        self.distance_m += (live.speed_kmh or 0) / 3.6
        self.samples.append(
            Sample(
                elapsed=self.elapsed,
                time=now,
                power=round(live.power),
                cadence=None if live.cadence is None else round(live.cadence),
                speed_kmh=live.speed_kmh,
                heart_rate=live.heart_rate,
                target=self.target_watts(),
                distance_m=self.distance_m,
                meter_power=None if live.meter_power is None else round(live.meter_power),
            )
        )
        self.metrics.add(live.power, live.heart_rate, live.cadence)
        self.elapsed += 1
