"""Structured workouts: segments with target power as a fraction of FTP.

Built-in workouts are defined below; more can be loaded from Zwift (.zwo) and
.mrc (percent of FTP) and .erg (watts) course files.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .metrics import normalized_power

log = logging.getLogger(__name__)

WORKOUT_EXTENSIONS = (".zwo", ".mrc", ".erg")
FREE_RIDE_ESTIMATE = 0.6  # assumed intensity of free-ride segments for TSS estimates
RAMP_TEST_EXPECTED_LIMIT = 1.25  # typical step at which a ramp test ends, as fraction of FTP
FILL_INTENSITY = 0.65  # zone 2 riding used to lengthen a workout to the rider's ride duration
FIT_TOLERANCE = 120  # seconds; shorter differences are not worth a filler segment


@dataclass(frozen=True)
class Segment:
    duration: int  # seconds
    start: float | None = None  # fraction of FTP; None means free ride (no target)
    end: float | None = None  # ramps go from start to end; defaults to start

    def __post_init__(self):
        if self.end is None and self.start is not None:
            object.__setattr__(self, "end", self.start)

    @property
    def is_free(self) -> bool:
        return self.start is None

    def target_at(self, offset: float) -> float | None:
        if self.start is None:
            return None
        if self.duration <= 0:
            return self.start
        progress = min(max(offset / self.duration, 0.0), 1.0)
        return self.start + (self.end - self.start) * progress


@dataclass
class Workout:
    name: str
    segments: list[Segment] = field(default_factory=list)
    description: str = ""
    source: Path | None = None
    ramp_test: bool = False  # ends when the rider stops and estimates a new FTP
    zone: int | None = None  # power zone (1-7) of the main set; see metrics.ZONES

    def fitted(self, minutes: int) -> Workout:
        """This workout lengthened to `minutes` with zone 2 riding before the cool-down.

        Workouts are never shortened: if it is already as long or longer, it is returned unchanged.
        """
        extra = minutes * 60 - self.duration
        if extra < FIT_TOLERANCE or self.open_ended or self.ramp_test:
            return self
        segments = list(self.segments)
        last = segments[-1]
        cooldown_at = len(segments) - 1 if not last.is_free and last.end < last.start else len(segments)
        segments.insert(cooldown_at, Segment(extra, FILL_INTENSITY))
        return Workout(self.name, segments, self.description, self.source, zone=self.zone)

    @property
    def open_ended(self) -> bool:
        return not self.segments

    @property
    def duration(self) -> int:
        return sum(s.duration for s in self.segments)

    @property
    def estimated_tss(self) -> float:
        """TSS if every target is ridden exactly (free ride counted at 60% FTP)."""
        fractions: list[float] = []
        for segment in self.segments:
            if self.ramp_test and not segment.is_free and segment.start > RAMP_TEST_EXPECTED_LIMIT:
                break  # nobody rides a ramp test to its last step
            for t in range(segment.duration):
                target = segment.target_at(t)
                fractions.append(FREE_RIDE_ESTIMATE if target is None else target)
        intensity = normalized_power(fractions)
        return len(fractions) * intensity**2 / 3600 * 100

    @property
    def max_target(self) -> float:
        return max((max(s.start, s.end) for s in self.segments if not s.is_free), default=0.0)

    def locate(self, t: float) -> tuple[int, Segment, int] | None:
        """Return (index, segment, segment start time) for elapsed time t, or None past the end."""
        start = 0
        for i, segment in enumerate(self.segments):
            if t < start + segment.duration:
                return i, segment, start
            start += segment.duration
        return None

    def target_at(self, t: float) -> float | None:
        located = self.locate(t)
        if located is None:
            return None
        _, segment, start = located
        return segment.target_at(t - start)


def _steady(minutes: float, fraction: float) -> Segment:
    return Segment(round(minutes * 60), fraction)


def _ramp(minutes: float, start: float, end: float) -> Segment:
    return Segment(round(minutes * 60), start, end)


RAMP_TEST_WARMUP = _ramp(5, 0.35, 0.50)
RAMP_TEST_START = 0.55
RAMP_TEST_STEP = 0.06  # per minute, as a fraction of the current FTP
RAMP_TEST_STEPS = 30  # far beyond anyone's limit, so the test always ends by exhaustion


def ramp_test() -> Workout:
    steps = [_steady(1, RAMP_TEST_START + i * RAMP_TEST_STEP) for i in range(RAMP_TEST_STEPS)]
    return Workout(
        "Ramp FTP Test",
        [RAMP_TEST_WARMUP, *steps],
        "5 minutes warm-up, then the target rises 6% of FTP every minute until you can't hold it. "
        "Stop pedalling when you're done: the test ends and suggests a new FTP "
        "(75% of your best 1-minute power).",
        ramp_test=True,
    )


def builtin_workouts() -> list[Workout]:
    from .library import library_workouts  # the library builds on this module

    return [
        Workout("Free Ride", [], "No targets. Change resistance with the slope buttons or Up/Down keys."),
        ramp_test(),
        *library_workouts(),
    ]


def load_zwo(path: Path) -> Workout:
    root = ET.parse(path).getroot()
    body = root.find("workout")
    if body is None:
        raise ValueError("no <workout> element")

    segments: list[Segment] = []
    for element in body:
        tag = element.tag.lower()
        attrs = {k.lower(): v for k, v in element.attrib.items()}

        def num(key: str, default: float | None = None) -> float | None:
            return float(attrs[key]) if key in attrs else default

        duration = round(num("duration", 0))
        if tag in ("warmup", "cooldown", "ramp"):
            segments.append(Segment(duration, num("powerlow"), num("powerhigh")))
        elif tag == "steadystate":
            power = num("power")
            if power is None and "powerlow" in attrs:
                power = (num("powerlow") + num("powerhigh", num("powerlow"))) / 2
            segments.append(Segment(duration, power))
        elif tag == "intervalst":
            on = Segment(round(num("onduration", 0)), num("onpower", num("poweronlow")))
            off = Segment(round(num("offduration", 0)), num("offpower", num("poweroffhigh")))
            segments.extend([on, off] * int(num("repeat", 1)))
        elif tag in ("freeride", "maxeffort"):
            segments.append(Segment(duration))

    segments = [s for s in segments if s.duration > 0]
    if not segments:
        raise ValueError("workout has no segments")
    name = (root.findtext("name") or "").strip() or path.stem
    return Workout(name, segments, (root.findtext("description") or "").strip(), path)


def load_course_file(path: Path, ftp: int) -> Workout:
    """Load an .mrc (minutes/percent) or .erg (minutes/watts) course file."""
    header: dict[str, str] = {}
    unit = "watts" if path.suffix.lower() == ".erg" else "percent"
    points: list[tuple[float, float]] = []
    section = None
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        upper = line.upper()
        if not line:
            continue
        if upper.startswith("["):
            if "END" in upper:
                section = None
            elif "HEADER" in upper:
                section = "header"
            elif "DATA" in upper:
                section = "data"
            continue
        if section == "header":
            if "=" in line:
                key, value = line.split("=", 1)
                header[key.strip().upper()] = value.strip()
            elif "WATTS" in upper:
                unit = "watts"
            elif "PERCENT" in upper:
                unit = "percent"
        elif section == "data":
            parts = line.split()
            try:
                points.append((float(parts[0]), float(parts[1])))
            except (IndexError, ValueError):
                pass

    if len(points) < 2:
        raise ValueError("no course data")
    if unit == "watts":
        try:
            scale = 1 / float(header.get("FTP", ftp))
        except ValueError:
            scale = 1 / ftp
    else:
        scale = 1 / 100

    segments = [
        Segment(round((t1 - t0) * 60), v0 * scale, v1 * scale)
        for (t0, v0), (t1, v1) in zip(points, points[1:])
        if t1 > t0
    ]
    name = Path(header.get("FILE NAME", "")).stem or path.stem
    return Workout(name, segments, header.get("DESCRIPTION", ""), path)


def load_workout(path: Path, ftp: int) -> Workout:
    if path.suffix.lower() == ".zwo":
        return load_zwo(path)
    return load_course_file(path, ftp)


def load_library(folder: Path, ftp: int) -> list[Workout]:
    workouts = builtin_workouts()
    if folder.is_dir():
        for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if path.suffix.lower() not in WORKOUT_EXTENSIONS:
                continue
            try:
                workouts.append(load_workout(path, ftp))
            except Exception as exc:
                log.warning("Skipping workout %s: %s", path.name, exc)
    return workouts
