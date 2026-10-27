"""User settings (%APPDATA%\\CyclingTrainer\\settings.json) and data folders."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from PySide6.QtCore import QStandardPaths

log = logging.getLogger(__name__)

APP_NAME = "CyclingTrainer"


def config_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME


def data_dir() -> Path:
    """Documents\\CyclingTrainer (follows OneDrive folder redirection)."""
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(documents or Path.home()) / APP_NAME


def rides_dir() -> Path:
    return data_dir() / "rides"


def workouts_dir() -> Path:
    return data_dir() / "workouts"


def plan_path() -> Path:
    return data_dir() / "plan.json"


@dataclass
class Settings:
    ftp: int = 200
    lthr: int = 0  # lactate-threshold heart rate; 0 = not set
    auto_pause: bool = True
    trainer_address: str = ""
    trainer_name: str = ""
    hr_address: str = ""
    hr_name: str = ""
    power_meter_address: str = ""
    power_meter_name: str = ""
    last_workout: str = ""
    plan_rides_per_week: int = 4
    plan_ride_minutes: int = 60
    plan_notes: str = ""

    @classmethod
    def load(cls) -> "Settings":
        path = config_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("Could not read %s: %s", path, exc)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        path = config_dir() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
