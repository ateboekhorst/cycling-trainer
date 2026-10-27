"""Training load from ride history: Fitness (CTL), Fatigue (ATL) and Form (TSB)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .history import RideRecord

CTL_DAYS = 42
ATL_DAYS = 7


@dataclass
class Fitness:
    ctl: float = 0.0
    atl: float = 0.0

    @property
    def tsb(self) -> float:
        return self.ctl - self.atl


def daily_tss(records: list[RideRecord]) -> dict[date, float]:
    totals: dict[date, float] = defaultdict(float)
    for r in records:
        totals[r.day] += r.tss
    return dict(totals)


def fitness_on(records: list[RideRecord], day: date) -> Fitness:
    """Fitness at the start of `day` (before that day's rides), from all earlier rides."""
    daily = daily_tss(records)
    fitness = Fitness()
    if not daily:
        return fitness
    current = min(daily)
    while current < day:
        tss = daily.get(current, 0.0)
        fitness.ctl += (tss - fitness.ctl) / CTL_DAYS
        fitness.atl += (tss - fitness.atl) / ATL_DAYS
        current += timedelta(days=1)
    return fitness


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())
