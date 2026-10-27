"""The training plan Claude generates: dated workouts and weekly focus, stored as JSON."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .fitness import monday
from .workouts import Segment, Workout, ramp_test

log = logging.getLogger(__name__)

PLAN_WEEKS = 4  # weeks planned beyond the current one


@dataclass
class PlannedWorkout:
    day: date
    name: str
    purpose: str
    segments: list[Segment] = field(default_factory=list)
    ftp_test: bool = False
    zone: int | None = None  # power zone (1-7)

    def to_workout(self) -> Workout:
        if self.ftp_test:
            return ramp_test()
        return Workout(self.name, list(self.segments), self.purpose, zone=self.zone)

    @property
    def duration(self) -> int:
        return self.to_workout().duration if not self.ftp_test else 0

    @property
    def estimated_tss(self) -> float:
        return self.to_workout().estimated_tss


@dataclass
class WeekFocus:
    monday: date
    focus: str
    target_tss: int


@dataclass
class TrainingPlan:
    created: datetime
    coach_note: str
    weeks: list[WeekFocus] = field(default_factory=list)
    workouts: list[PlannedWorkout] = field(default_factory=list)

    def workout_on(self, day: date) -> PlannedWorkout | None:
        return next((w for w in self.workouts if w.day == day), None)

    def week(self, week_monday: date) -> WeekFocus | None:
        return next((w for w in self.weeks if w.monday == week_monday), None)

    def merged_into(self, previous: TrainingPlan | None, first_day: date) -> TrainingPlan:
        """Keep the previous plan's past days (for planned-vs-actual) and replace the rest with this plan."""
        if previous is None:
            return self
        first_monday = monday(first_day)
        weeks = [w for w in previous.weeks if w.monday < first_monday] + self.weeks
        workouts = [w for w in previous.workouts if w.day < first_day] + self.workouts
        return TrainingPlan(self.created, self.coach_note, sorted(weeks, key=lambda w: w.monday),
                            sorted(workouts, key=lambda w: w.day))


def plan_window(today: date, rode_today: bool) -> tuple[date, date]:
    """First and last day Claude should plan: from today (tomorrow if already ridden) to 4 weeks out."""
    first = today + timedelta(days=1) if rode_today else today
    last = monday(today) + timedelta(weeks=PLAN_WEEKS, days=6)
    return first, last


# --- persistence ---

def _segment_to_json(s: Segment) -> dict:
    return {"duration": s.duration, "start": s.start, "end": s.end}


def save_plan(plan: TrainingPlan, path: Path) -> None:
    data = {
        "created": plan.created.isoformat(),
        "coach_note": plan.coach_note,
        "weeks": [{"monday": w.monday.isoformat(), "focus": w.focus, "target_tss": w.target_tss} for w in plan.weeks],
        "workouts": [
            {
                "day": w.day.isoformat(),
                "name": w.name,
                "purpose": w.purpose,
                "ftp_test": w.ftp_test,
                "zone": w.zone,
                "segments": [_segment_to_json(s) for s in w.segments],
            }
            for w in plan.workouts
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def load_plan(path: Path) -> TrainingPlan | None:
    try:
        data = json.loads(path.read_text())
        return TrainingPlan(
            created=datetime.fromisoformat(data["created"]),
            coach_note=data["coach_note"],
            weeks=[WeekFocus(date.fromisoformat(w["monday"]), w["focus"], w["target_tss"]) for w in data["weeks"]],
            workouts=[
                PlannedWorkout(
                    day=date.fromisoformat(w["day"]),
                    name=w["name"],
                    purpose=w["purpose"],
                    ftp_test=w.get("ftp_test", False),
                    zone=w.get("zone"),
                    segments=[Segment(s["duration"], s["start"], s["end"]) for s in w["segments"]],
                )
                for w in data["workouts"]
            ],
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("Could not read training plan %s: %s", path, exc)
        return None
