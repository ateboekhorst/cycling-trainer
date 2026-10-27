"""Ride log: one small JSON summary per saved ride, next to its .fit file."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from .session import Ride

log = logging.getLogger(__name__)


@dataclass
class RideRecord:
    start: datetime
    workout: str
    duration_s: int
    tss: float
    intensity_factor: float
    normalized_power: float
    avg_power: float
    distance_m: float
    kilojoules: float
    ftp: int

    @property
    def day(self) -> date:
        return self.start.date()


def record_for(ride: Ride) -> RideRecord:
    m = ride.metrics
    return RideRecord(
        start=ride.start_time,
        workout=ride.workout.name,
        duration_s=ride.elapsed,
        tss=round(m.tss(ride.ftp), 1),
        intensity_factor=round(m.intensity_factor(ride.ftp), 3),
        normalized_power=round(m.normalized_power, 1),
        avg_power=round(m.avg_power, 1),
        distance_m=round(ride.distance_m, 1),
        kilojoules=round(m.kilojoules, 1),
        ftp=ride.ftp,
    )


def write_record(record: RideRecord, path: Path) -> None:
    data = asdict(record)
    data["start"] = record.start.isoformat()
    path.write_text(json.dumps(data, indent=2))


def load_history(folder: Path) -> list[RideRecord]:
    records = []
    if folder.is_dir():
        for path in folder.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                data["start"] = datetime.fromisoformat(data["start"])
                records.append(RideRecord(**data))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                log.warning("Skipping ride log %s: %s", path.name, exc)
    return sorted(records, key=lambda r: r.start)
