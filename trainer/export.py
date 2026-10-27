"""Save finished rides as .fit (for Strava, Garmin Connect, intervals.icu, ...) and .csv."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import Activity, Event, EventType, FileType, Manufacturer, Sport, SubSport

from .history import record_for, write_record
from .session import Ride

FIT_EPOCH_OFFSET = 631065600  # seconds between 1970-01-01 and 1989-12-31 (FIT epoch)


def ride_basename(ride: Ride) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ride.workout.name).strip("-") or "ride"
    return f"{ride.start_time:%Y-%m-%d_%H%M}_{slug}"


def save_ride(ride: Ride, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / ride_basename(ride)
    fit_path, csv_path, log_path = base.with_suffix(".fit"), base.with_suffix(".csv"), base.with_suffix(".json")
    write_fit(ride, fit_path)
    write_csv(ride, csv_path)
    write_record(record_for(ride), log_path)  # feeds the training plan
    return [fit_path, csv_path]


def write_csv(ride: Ride, path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["elapsed_s", "time", "power_w", "target_w", "cadence_rpm", "speed_kmh", "heart_rate",
                         "distance_m", "power_meter_w"])
        for s in ride.samples:
            writer.writerow([
                s.elapsed,
                s.time.isoformat(timespec="seconds"),
                s.power,
                "" if s.target is None else s.target,
                "" if s.cadence is None else s.cadence,
                "" if s.speed_kmh is None else f"{s.speed_kmh:.2f}",
                "" if s.heart_rate is None else s.heart_rate,
                f"{s.distance_m:.1f}",
                "" if s.meter_power is None else s.meter_power,
            ])


def _ms(dt) -> int:
    return round(dt.timestamp() * 1000)


def _fill_summary(msg, ride: Ride, start_ms: int, end_ms: int) -> None:
    m = ride.metrics
    msg.timestamp = end_ms
    msg.start_time = start_ms
    msg.total_elapsed_time = (end_ms - start_ms) / 1000
    msg.total_timer_time = float(ride.elapsed)
    msg.total_distance = ride.distance_m
    msg.avg_speed = ride.distance_m / ride.elapsed if ride.elapsed else 0.0
    msg.avg_power = round(m.avg_power)
    msg.max_power = m.max_power
    msg.normalized_power = round(m.normalized_power)
    msg.total_work = round(m.power_sum)
    msg.total_calories = round(m.kilojoules)  # ~1 kcal burned per kJ of work (≈24% efficiency)
    msg.sport = Sport.CYCLING
    msg.sub_sport = SubSport.INDOOR_CYCLING
    if m.avg_cadence:
        msg.avg_cadence = round(m.avg_cadence)
        msg.max_cadence = m.max_cadence
    if m.avg_heart_rate:
        msg.avg_heart_rate = round(m.avg_heart_rate)
        msg.max_heart_rate = m.max_heart_rate


def write_fit(ride: Ride, path: Path) -> None:
    if not ride.samples:
        raise ValueError("ride has no samples")
    start_ms = _ms(ride.samples[0].time)
    end_ms = _ms(ride.samples[-1].time)
    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.serial_number = 0x1C7C1E
    file_id.time_created = start_ms
    builder.add(file_id)

    def timer_event(ms: int, event_type: EventType) -> EventMessage:
        event = EventMessage()
        event.timestamp = ms
        event.event = Event.TIMER
        event.event_type = event_type
        return event

    builder.add(timer_event(start_ms, EventType.START))
    for s in ride.samples:
        record = RecordMessage()
        record.timestamp = _ms(s.time)
        record.power = s.power
        record.distance = s.distance_m
        if s.speed_kmh is not None:
            record.speed = s.speed_kmh / 3.6
        if s.cadence is not None:
            record.cadence = s.cadence
        if s.heart_rate:
            record.heart_rate = s.heart_rate
        builder.add(record)
    builder.add(timer_event(end_ms, EventType.STOP_ALL))

    lap = LapMessage()
    _fill_summary(lap, ride, start_ms, end_ms)
    lap.event = Event.LAP
    lap.event_type = EventType.STOP
    builder.add(lap)

    session = SessionMessage()
    _fill_summary(session, ride, start_ms, end_ms)
    session.event = Event.SESSION
    session.event_type = EventType.STOP
    session.first_lap_index = 0
    session.num_laps = 1
    session.threshold_power = ride.ftp
    session.normalized_power = round(ride.metrics.normalized_power)
    session.intensity_factor = ride.metrics.intensity_factor(ride.ftp)
    session.training_stress_score = ride.metrics.tss(ride.ftp)
    builder.add(session)

    activity = ActivityMessage()
    activity.timestamp = end_ms
    activity.total_timer_time = float(ride.elapsed)
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    activity.event = Event.ACTIVITY
    activity.event_type = EventType.STOP
    end = ride.samples[-1].time
    activity.local_timestamp = int(end.timestamp() + end.utcoffset().total_seconds()) - FIT_EPOCH_OFFSET
    builder.add(activity)

    builder.build().to_file(str(path))
