"""Training metrics from 1 Hz power samples (Coggan NP / IF / TSS and power zones)."""
from __future__ import annotations

import math
from collections import deque

NP_WINDOW = 30

# Coggan power zones: (name, upper bound as fraction of FTP, inclusive)
ZONES = [
    ("Z1 Recovery", 0.55),
    ("Z2 Endurance", 0.75),
    ("Z3 Tempo", 0.90),
    ("Z4 Threshold", 1.05),
    ("Z5 VO2max", 1.20),
    ("Z6 Anaerobic", 1.50),
    ("Z7 Neuromuscular", math.inf),
]

# Coggan heart-rate zones: (name, upper bound as fraction of lactate-threshold heart rate, inclusive)
HR_ZONES = [
    ("Z1 Recovery", 0.68),
    ("Z2 Endurance", 0.83),
    ("Z3 Tempo", 0.94),
    ("Z4 Threshold", 1.05),
    ("Z5 VO2max", math.inf),
]


def zone_index(fraction: float, zones=ZONES) -> int:
    """0-based zone for a fraction of FTP (or of LTHR with zones=HR_ZONES)."""
    for i, (_, upper) in enumerate(zones):
        if fraction <= upper + 1e-9:
            return i
    return len(zones) - 1


def zone_label(zone: int) -> str:
    """'Zone 4 · Threshold' for power zone 4."""
    return f"Zone {zone} · {ZONES[zone - 1][0].split(' ', 1)[1]}"


def zone_range(zone: int, zones=ZONES, unit: str = "FTP") -> str:
    """'91-105% FTP' style range for a 1-based zone."""
    upper = zones[zone - 1][1]
    lower = zones[zone - 2][1] if zone > 1 else None
    if lower is None:
        return f"up to {upper:.0%} {unit}"
    if math.isinf(upper):
        return f"above {lower:.0%} {unit}"
    return f"{lower * 100 + 1:.0f}-{upper:.0%} {unit}"


def time_in_hr_zones(heart_rates: list[int | None], lthr: int) -> list[int]:
    seconds = [0] * len(HR_ZONES)
    if lthr:
        for bpm in heart_rates:
            if bpm:
                seconds[zone_index(bpm / lthr, HR_ZONES)] += 1
    return seconds


def normalized_power(powers: list[float]) -> float:
    if not powers:
        return 0.0
    if len(powers) < NP_WINDOW:
        return sum(powers) / len(powers)
    window_sum = sum(powers[:NP_WINDOW])
    fourth_powers = [(window_sum / NP_WINDOW) ** 4]
    for i in range(NP_WINDOW, len(powers)):
        window_sum += powers[i] - powers[i - NP_WINDOW]
        fourth_powers.append((window_sum / NP_WINDOW) ** 4)
    return (sum(fourth_powers) / len(fourth_powers)) ** 0.25


def intensity_factor(np_watts: float, ftp: float) -> float:
    return np_watts / ftp if ftp else 0.0


def training_stress(seconds: float, np_watts: float, ftp: float) -> float:
    if not ftp:
        return 0.0
    return seconds * np_watts * intensity_factor(np_watts, ftp) / (ftp * 3600) * 100


RAMP_TEST_FACTOR = 0.75  # FTP as a fraction of best 1-minute power in a ramp test


def best_average(powers: list[float], seconds: int) -> float:
    """Highest average power over any window of `seconds` consecutive 1 Hz samples."""
    if len(powers) < seconds:
        return 0.0
    window_sum = best = sum(powers[:seconds])
    for i in range(seconds, len(powers)):
        window_sum += powers[i] - powers[i - seconds]
        best = max(best, window_sum)
    return best / seconds


def ramp_test_ftp(powers: list[float]) -> int | None:
    """FTP estimate from a ramp test, or None if the ride was too short to tell."""
    best_minute = best_average(powers, 60)
    return round(best_minute * RAMP_TEST_FACTOR) if best_minute else None


def time_in_zones(powers: list[float], ftp: float) -> list[int]:
    seconds = [0] * len(ZONES)
    if ftp:
        for power in powers:
            seconds[zone_index(power / ftp)] += 1
    return seconds


class LiveMetrics:
    """Running ride totals, updated in O(1) per 1 Hz sample."""

    def __init__(self):
        self.count = 0
        self.power_sum = 0.0
        self.max_power = 0
        self._window: deque[float] = deque(maxlen=NP_WINDOW)
        self._fourth_sum = 0.0
        self._fourth_count = 0
        self._hr_sum = 0
        self._hr_count = 0
        self.max_heart_rate = 0
        self._cadence_sum = 0.0
        self._cadence_count = 0
        self.max_cadence = 0

    def add(self, power: float, heart_rate: int | None, cadence: float | None) -> None:
        self.count += 1
        self.power_sum += power
        self.max_power = max(self.max_power, round(power))
        self._window.append(power)
        if len(self._window) == NP_WINDOW:
            self._fourth_sum += (sum(self._window) / NP_WINDOW) ** 4
            self._fourth_count += 1
        if heart_rate:
            self._hr_sum += heart_rate
            self._hr_count += 1
            self.max_heart_rate = max(self.max_heart_rate, heart_rate)
        if cadence:  # average cadence conventionally excludes coasting
            self._cadence_sum += cadence
            self._cadence_count += 1
            self.max_cadence = max(self.max_cadence, round(cadence))

    @property
    def avg_power(self) -> float:
        return self.power_sum / self.count if self.count else 0.0

    @property
    def normalized_power(self) -> float:
        if not self._fourth_count:
            return self.avg_power
        return (self._fourth_sum / self._fourth_count) ** 0.25

    @property
    def kilojoules(self) -> float:
        return self.power_sum / 1000

    @property
    def avg_heart_rate(self) -> float:
        return self._hr_sum / self._hr_count if self._hr_count else 0.0

    @property
    def avg_cadence(self) -> float:
        return self._cadence_sum / self._cadence_count if self._cadence_count else 0.0

    def intensity_factor(self, ftp: float) -> float:
        return intensity_factor(self.normalized_power, ftp)

    def tss(self, ftp: float) -> float:
        return training_stress(self.count, self.normalized_power, ftp)
