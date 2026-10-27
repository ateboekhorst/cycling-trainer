import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from trainer.coach import (
    CoachError,
    CoachRequest,
    PlanOut,
    SegmentOut,
    WeekOut,
    WorkoutOut,
    build_context,
    request_plan_api,
    request_plan_claude_code,
    to_plan,
)
from trainer.fitness import fitness_on
from trainer.history import RideRecord, load_history, record_for, write_record
from trainer.plan import PlannedWorkout, TrainingPlan, load_plan, plan_window, save_plan
from trainer.session import Live, Ride
from trainer.workouts import Segment, Workout, ramp_test

TODAY = date(2026, 10, 21)  # a Wednesday


def record(day: date, tss: float, workout: str = "Sweet Spot", minutes: int = 60) -> RideRecord:
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).replace(hour=18)
    return RideRecord(start, workout, minutes * 60, tss, 0.8, 200, 190, 30000, 700, 250)


def workout_out(day: date, name="SS", minutes=60, pct=90, ftp_test=False, library=None, zone=3) -> WorkoutOut:
    segments = [] if ftp_test or library else [SegmentOut(duration_s=minutes * 60, start_pct=pct, end_pct=pct)]
    return WorkoutOut(date=day.isoformat(), name=name, purpose="p", ftp_test=ftp_test,
                      library_workout=library, power_zone=zone, segments=segments)


def request(records=(), previous=None, rides_per_week=3) -> CoachRequest:
    first, last = plan_window(TODAY, rode_today=any(r.day == TODAY for r in records))
    return CoachRequest(TODAY, 250, rides_per_week, 60, "", list(records), previous, first, last)


# --- fitness & estimates ---

def test_fitness_rises_with_training_and_form_drops():
    records = [record(TODAY - timedelta(days=d), 100) for d in range(1, 15)]
    f = fitness_on(records, TODAY)
    assert 20 < f.ctl < 40 and f.atl > 80 and f.tsb < -40
    assert fitness_on([], TODAY).ctl == 0


def test_estimated_tss():
    one_hour_at_ftp = Workout("x", [Segment(3600, 1.0)])
    assert one_hour_at_ftp.estimated_tss == pytest.approx(100)
    assert 15 < ramp_test().estimated_tss < 35  # ~17 min: counted only up to where riders usually stop


def test_plan_window_covers_current_week_plus_four():
    first, last = plan_window(TODAY, rode_today=False)
    assert first == TODAY and last == date(2026, 11, 22) and last.weekday() == 6
    assert plan_window(TODAY, rode_today=True)[0] == TODAY + timedelta(days=1)


# --- ride log ---

def test_ride_log_round_trip(tmp_path: Path):
    ride = Ride(Workout("Test", [Segment(600, 0.8)]), ftp=250)
    t0 = datetime(2026, 10, 20, 18, 0, tzinfo=timezone(timedelta(hours=2)))
    for i in range(120):
        ride.update(Live(power=200, cadence=90, speed_kmh=30), t0 + timedelta(seconds=i))
    rec = record_for(ride)
    write_record(rec, tmp_path / "a.json")
    loaded = load_history(tmp_path)
    assert loaded == [rec] and loaded[0].day == date(2026, 10, 20)


# --- Claude's answer is held to the rider's constraints ---

def test_to_plan_enforces_window_one_per_day_and_rides_per_week():
    records = [record(TODAY - timedelta(days=2), 60)]  # Monday ride: 1 of 3 done this week
    req = request(records)
    out = PlanOut(
        coach_note="note",
        weeks=[WeekOut(week_start="2026-10-19", focus="build")],
        workouts=[
            workout_out(TODAY - timedelta(days=1)),  # before the window
            workout_out(TODAY),
            workout_out(TODAY, name="dup"),  # second ride same day
            workout_out(TODAY + timedelta(days=2)),
            workout_out(TODAY + timedelta(days=3)),  # 4th ride this week -> dropped
            workout_out(date(2026, 11, 30)),  # after the window
            workout_out(date(2026, 10, 27), ftp_test=True),
        ],
    )
    plan = to_plan(out, req)
    days = [w.day for w in plan.workouts]
    assert days == [TODAY, TODAY + timedelta(days=2), date(2026, 10, 27)]
    test = plan.workouts[-1]
    assert test.ftp_test and test.name == "Ramp FTP Test" and test.to_workout().ramp_test
    assert plan.weeks[0].target_tss == round(2 * plan.workouts[0].estimated_tss)


def test_segments_are_converted_from_percent():
    req = request()
    out = PlanOut(coach_note="", weeks=[], workouts=[WorkoutOut(
        date=TODAY.isoformat(), name="Ramp", purpose="", ftp_test=False, library_workout=None, power_zone=3,
        segments=[SegmentOut(duration_s=300, start_pct=50, end_pct=75), SegmentOut(duration_s=0, start_pct=90, end_pct=90),
                  SegmentOut(duration_s=120, start_pct=None, end_pct=None)],
    )])
    segs = to_plan(out, req).workouts[0].segments
    assert segs == [Segment(300, 0.5, 0.75), Segment(120, None, None)]


def test_context_reports_compliance_and_open_rides():
    previous = TrainingPlan(datetime.now(timezone.utc), "", [], [
        PlannedWorkout(TODAY - timedelta(days=2), "Threshold", "", [Segment(3600, 0.95)]),
        PlannedWorkout(TODAY - timedelta(days=1), "VO2", "", [Segment(3600, 0.8)]),
        PlannedWorkout(TODAY + timedelta(days=1), "Endurance", "", [Segment(3600, 0.65)]),
    ])
    req = request([record(TODAY - timedelta(days=2), 70, "Threshold")], previous)
    ctx = build_context(req)
    ride = ctx["rides_last_6_weeks"][0]
    assert ride["planned_workout"] == "Threshold" and ride["planned_tss"] == 90 and ride["tss"] == 70
    assert [m["workout"] for m in ctx["missed_planned_workouts"]] == ["VO2"]
    assert ctx["constraints"]["rides_still_open_this_week"] == 2
    assert [w["workout"] for w in ctx["previous_plan_upcoming"]] == ["Endurance"]


def test_request_plan_with_fake_client():
    captured = {}

    def parse(**kwargs):
        captured.update(kwargs)
        out = PlanOut(coach_note="Go", weeks=[], workouts=[workout_out(TODAY)])
        return SimpleNamespace(stop_reason="end_turn", parsed_output=out, _request_id="req_1", usage=None)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    plan = request_plan_api(request(), client)
    assert plan.coach_note == "Go" and len(plan.workouts) == 1
    assert captured["model"] == "claude-opus-5-5" and captured["output_format"] is PlanOut
    assert '"rides_per_week": 3' in captured["messages"][0]["content"]


# --- persistence & merge ---

def test_plan_save_load_and_merge(tmp_path: Path):
    old = TrainingPlan(datetime.now(timezone.utc), "old", [], [
        PlannedWorkout(TODAY - timedelta(days=1), "Past", "", [Segment(600, 0.7)]),
        PlannedWorkout(TODAY + timedelta(days=1), "Stale", "", [Segment(600, 0.7)]),
    ])
    new = TrainingPlan(datetime.now(timezone.utc), "new", [], [
        PlannedWorkout(TODAY + timedelta(days=2), "Fresh", "", [Segment(600, 0.9)]),
        PlannedWorkout(TODAY + timedelta(days=5), "Test", "", [], ftp_test=True),
    ])
    merged = new.merged_into(old, first_day=TODAY)
    assert [w.name for w in merged.workouts] == ["Past", "Fresh", "Test"]

    save_plan(merged, tmp_path / "plan.json")
    loaded = load_plan(tmp_path / "plan.json")
    assert loaded.workouts == merged.workouts and loaded.coach_note == "new"
    assert load_plan(tmp_path / "missing.json") is None


# --- Claude Code route ---

def fake_claude_code(payload: dict, captured: dict):
    def run(command, **kwargs):
        captured["command"], captured["kwargs"] = command, kwargs
        return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
    return run


def test_claude_code_route_parses_structured_output(monkeypatch):
    monkeypatch.setattr("trainer.coach._supports_flag", lambda exe, flag: True)
    out = PlanOut(coach_note="Via CC", weeks=[], workouts=[workout_out(TODAY)])
    captured = {}
    run = fake_claude_code({"type": "result", "subtype": "success", "is_error": False,
                            "structured_output": out.model_dump()}, captured)
    plan = request_plan_claude_code(request(), "claude.exe", run=run)
    assert plan.coach_note == "Via CC" and len(plan.workouts) == 1
    cmd = captured["command"]
    assert cmd[0] == "claude.exe" and "--print" in cmd and cmd[cmd.index("--tools") + 1] == ""
    assert json.loads(cmd[cmd.index("--json-schema") + 1])["title"] == "PlanOut"
    assert '"rides_per_week": 3' in captured["kwargs"]["input"]


def test_claude_code_route_reports_errors(monkeypatch):
    monkeypatch.setattr("trainer.coach._supports_flag", lambda exe, flag: False)
    run = fake_claude_code({"type": "result", "subtype": "success", "is_error": True,
                            "result": "Not logged in · Please run /login"}, {})
    with pytest.raises(CoachError, match="log in"):
        request_plan_claude_code(request(), "claude.exe", run=run)

    def crash(command, **kwargs):
        return SimpleNamespace(stdout="", stderr="boom\nfatal: something broke", returncode=1)
    with pytest.raises(CoachError, match="something broke"):
        request_plan_claude_code(request(), "claude.exe", run=crash)


def test_api_key_takes_precedence_over_claude_code(monkeypatch):
    import trainer.coach as coach

    monkeypatch.setattr(coach, "stored_api_key", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(coach, "find_claude_code", lambda: "claude.exe")
    assert coach.backend_name().startswith("Claude Code")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert coach.backend_name() == "Claude API"


def test_only_real_api_keys_are_used(monkeypatch):
    import keyring
    import trainer.coach as coach

    monkeypatch.setattr(keyring, "get_password", lambda service, user: "ANTHROPIC_API_KEY")
    assert coach.stored_api_key() is None
    monkeypatch.setattr(keyring, "get_password", lambda service, user: "sk-ant-api03-abc")
    assert coach.stored_api_key() == "sk-ant-api03-abc"
    with pytest.raises(ValueError, match="sk-ant-"):
        coach.store_api_key("ANTHROPIC_API_KEY")


# --- workout library ---

def test_library_has_ten_workouts_per_zone_with_unique_names():
    from collections import Counter

    from trainer.library import library_workouts

    lib = library_workouts()
    assert Counter(w.zone for w in lib) == {1: 10, 2: 10, 3: 10, 4: 10, 5: 10, 6: 3, 7: 1}
    assert len({w.name for w in lib}) == len(lib) == 54
    assert all(w.duration >= 30 * 60 and w.description.startswith(f"Zone {w.zone}") for w in lib)
    by_zone = {z: [w.estimated_tss * 36 / w.duration for w in lib if w.zone == z] for z in range(1, 6)}
    averages = [sum(v) / len(v) for v in by_zone.values()]  # mean IF^2 rises from zone 1 to 4
    assert averages[:4] == sorted(averages[:4])


def test_fitted_adds_zone2_before_cooldown_and_never_shortens():
    from trainer.library import library_workouts

    w = next(w for w in library_workouts() if w.name == "Sweet Spot 4x8")
    longer = w.fitted(90)
    assert longer.duration == 90 * 60 and longer.zone == 3
    assert longer.segments[-2] == Segment(30 * 60, 0.65) and longer.segments[-1] == w.segments[-1]
    assert w.fitted(45) is w and w.fitted(61) is w


def test_plan_uses_library_workouts_fitted_to_ride_duration():
    req = request()  # 60-minute rides
    out = PlanOut(coach_note="", weeks=[], workouts=[
        workout_out(TODAY, name="x", library="VO2max 5x3", zone=5),  # 50 min -> extended to 60
        workout_out(TODAY + timedelta(days=2), name="Mine", library="No Such Workout", zone=2),  # falls back
        workout_out(TODAY + timedelta(days=4), name="Too long", library="Long Steady 120", zone=2),
    ])
    out.workouts[1].segments = [SegmentOut(duration_s=3600, start_pct=90, end_pct=90)]  # its own fallback
    # out.workouts[2] has no segments of its own and its library pick is too long: dropped
    plan = to_plan(out, req)
    vo2, own = plan.workouts
    assert vo2.name == "VO2max 5x3" and vo2.zone == 5 and vo2.to_workout().duration == 3600
    assert own.name == "Mine" and own.zone == 2 and own.segments == [Segment(3600, 0.9)]


def test_system_prompt_lists_the_library_by_zone():
    from trainer.coach import system_prompt

    prompt = system_prompt()
    assert "Zone 5 · VO2max (106-120% FTP):" in prompt and "- Sweet Spot 2x20 (60 min" in prompt


def test_library_workouts_reach_their_power_zone():
    from trainer.library import library_workouts
    from trainer.metrics import zone_index

    for w in library_workouts():
        highest = zone_index(w.max_target) + 1
        assert highest >= w.zone, f"{w.name} never reaches zone {w.zone}"
        assert f"({zone_range_text(w.zone)})" in w.description


def zone_range_text(zone):
    from trainer.metrics import zone_range

    return zone_range(zone)


def test_heart_rate_zones_from_lthr():
    from trainer.metrics import HR_ZONES, time_in_hr_zones, zone_index, zone_range

    assert [zone_index(bpm / 170, HR_ZONES) for bpm in (110, 140, 155, 170, 185)] == [0, 1, 2, 3, 4]
    assert time_in_hr_zones([110, None, 155, 185], 170) == [1, 0, 1, 0, 1]
    assert time_in_hr_zones([150], 0) == [0, 0, 0, 0, 0]
    assert zone_range(1) == "up to 55% FTP" and zone_range(4) == "91-105% FTP" and zone_range(7) == "above 150% FTP"
