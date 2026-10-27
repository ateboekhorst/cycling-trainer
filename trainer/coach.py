"""Claude as the coach: generates and adjusts the 4-week training plan and its workouts."""
from __future__ import annotations

import functools
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

import anthropic
from pydantic import BaseModel

from .config import config_dir
from .fitness import daily_tss, fitness_on, monday
from .history import RideRecord
from .library import library_workouts
from .metrics import ZONES, zone_label, zone_range
from .plan import PlannedWorkout, TrainingPlan, WeekFocus
from .workouts import FIT_TOLERANCE, Segment, Workout

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
MAX_TOKENS = 20000  # stays below the SDK's non-streaming limit
HISTORY_DAYS = 42
RAMP_TEST_NAME = "Ramp FTP Test"
KEYRING_SERVICE = "CyclingTrainer"
KEYRING_USER = "anthropic_api_key"
CLAUDE_CODE_TIMEOUT = 600  # seconds
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash when started from pythonw

SYSTEM_PROMPT = """You are an experienced cycling coach. You write structured indoor workouts for a rider who \
trains on a smart trainer in ERG mode, and you plan their training with one goal: raising their FTP.

How to plan:
- Plan from the first to the last day you are given. Each Monday-to-Sunday week gets exactly the number of rides \
the rider asked for (for the current week, the number of rides still open), at most one per day. Spread them out \
and avoid hard sessions on consecutive days.
- Every workout lasts the rider's chosen duration (within 2 minutes) and includes a warm-up and a cool-down.
- Build FTP with progressive overload: sweet spot, threshold and over-unders as the backbone, VO2max work for \
top-end, and endurance or recovery rides to absorb the load. Use a lighter week when accumulated fatigue calls \
for it (roughly one in four weeks).
- Think in TSS (TSS = hours x IF^2 x 100). Keep the weekly load rising gradually from what the rider actually \
did recently; their Fitness (CTL) should climb by roughly 3-6 points per week in build weeks.
- Adjust to what actually happened. Compare planned and actual TSS for recent days: if sessions were missed or \
cut short, or Form (TSB) is below about -25, ease off rather than cramming missed work in; if the rider \
completes everything and Form is above about -10, progress. Never schedule the remainder of a missed week on \
top of the next one.
- Schedule an FTP test (set ftp_test to true and leave segments empty) when the rider has never tested or their \
last test is 6 or more weeks old, and otherwise at the end of a build block. Plan an easy day before a test. \
The test is a ramp test and counts as one of the week's rides.

Workout library and variety:
- Below is the rider's workout library, grouped by the power zone (% of FTP) of each workout's main set. Use it \
for most sessions: set library_workout to the exact name and leave segments empty. A library workout shorter than \
the rider's ride duration is lengthened automatically with zone 2 riding; don't pick one that is longer.
- Keep training varied: don't repeat a library workout within two weeks, and mix workouts within a zone.
- Write your own workout (library_workout null, with segments) only when nothing in the library fits the purpose.
- Set power_zone (1-7, Coggan power zones) to the zone of the main set, as in the library.

Workout format for your own workouts:
- Segments are consecutive. duration_s is in whole seconds. start_pct and end_pct are the target power as \
whole-number percentages of FTP at the start and end of the segment (equal for steady efforts, different for \
ramps). Use null for both only for a free-ride segment.
- Write repeated intervals out as individual segments.
- Give each workout a short descriptive name (for example "Sweet Spot 3x12") and a one-sentence purpose.

Also give each week a short focus line in words (no TSS numbers: the app calculates and shows TSS itself), \
and write a coach_note of 2-4 sentences to the rider explaining the plan \
and what you changed since the previous version and why."""


def library_catalogue() -> str:
    lines = []
    for zone in range(1, len(ZONES) + 1):
        lines.append(f"\n{zone_label(zone)} ({zone_range(zone)}):")
        for w in library_workouts():
            if w.zone == zone:
                purpose = w.description.split(": ", 1)[-1]
                lines.append(f"- {w.name} ({w.duration // 60} min, ~{w.estimated_tss:.0f} TSS): {purpose}")
    return "\n".join(lines)


def system_prompt() -> str:
    return f"{SYSTEM_PROMPT}\n\nWorkout library:{library_catalogue()}"


class SegmentOut(BaseModel):
    duration_s: int
    start_pct: Optional[int]
    end_pct: Optional[int]


class WorkoutOut(BaseModel):
    date: str  # YYYY-MM-DD
    name: str
    purpose: str
    ftp_test: bool
    library_workout: Optional[str]  # exact library name, or null for a custom workout
    power_zone: int  # Coggan power zone 1-7 of the main set
    segments: list[SegmentOut]


class WeekOut(BaseModel):
    week_start: str  # Monday, YYYY-MM-DD
    focus: str


class PlanOut(BaseModel):
    coach_note: str
    weeks: list[WeekOut]
    workouts: list[WorkoutOut]


class CoachError(Exception):
    pass


@dataclass
class CoachRequest:
    today: date
    ftp: int
    rides_per_week: int
    ride_minutes: int
    notes: str
    records: list[RideRecord]
    previous: TrainingPlan | None
    first_day: date
    last_day: date
    lthr: int = 0  # lactate-threshold heart rate, 0 if unknown

    def rides_done_this_week(self) -> int:
        start = monday(self.today)
        return len({r.day for r in self.records if start <= r.day < self.first_day})


# --- API key (Windows Credential Manager via keyring, else the SDK's own lookup) ---

API_KEY_PREFIX = "sk-ant-"


def stored_api_key() -> str | None:
    try:
        import keyring

        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        log.exception("Could not read the API key from the credential store")
        return None
    if key and not key.startswith(API_KEY_PREFIX):
        log.warning("Ignoring stored API key: it doesn't look like an Anthropic key")
        return None
    return key


def store_api_key(key: str) -> None:
    """Store a key (empty removes it). Raises ValueError for text that isn't an Anthropic API key."""
    import keyring

    if key and not key.startswith(API_KEY_PREFIX):
        raise ValueError(f"That isn't an Anthropic API key. Keys start with '{API_KEY_PREFIX}'.")
    if key:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except keyring.errors.PasswordDeleteError:
            pass


def make_client() -> anthropic.Anthropic:
    key = stored_api_key()
    try:
        return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    except anthropic.AnthropicError as exc:
        raise CoachError("No Anthropic API key. Click 'API key…' on the Plan tab to add one.") from exc


# --- prompt ---

def build_context(req: CoachRequest) -> dict:
    fitness = fitness_on(req.records, req.today)
    since = req.today - timedelta(days=HISTORY_DAYS)
    planned = {w.day: w for w in req.previous.workouts} if req.previous else {}

    rides = []
    for r in req.records:
        if r.day < since:
            continue
        plan = planned.get(r.day)
        rides.append({
            "date": r.day.isoformat(),
            "weekday": r.day.strftime("%a"),
            "workout": r.workout,
            "minutes": round(r.duration_s / 60),
            "tss": round(r.tss),
            "intensity_factor": round(r.intensity_factor, 2),
            "planned_workout": plan.name if plan else None,
            "planned_tss": round(plan.estimated_tss) if plan else None,
        })
    ridden_days = {r.day for r in req.records}
    missed = [
        {"date": w.day.isoformat(), "workout": w.name, "planned_tss": round(w.estimated_tss)}
        for w in planned.values()
        if since <= w.day < req.first_day and w.day not in ridden_days and w.day != req.today
    ]

    daily = daily_tss(req.records)
    weekly = []
    for weeks_back in range(5, -1, -1):
        start = monday(req.today) - timedelta(weeks=weeks_back)
        weekly.append({
            "week_start": start.isoformat(),
            "actual_tss": round(sum(daily.get(start + timedelta(days=d), 0.0) for d in range(7))),
        })

    ftp_tests = [r for r in req.records if r.workout == RAMP_TEST_NAME and r.duration_s >= 600]
    return {
        "today": f"{req.today.isoformat()} ({req.today.strftime('%A')})",
        "rider": {
            "ftp_watts": req.ftp,
            "threshold_heart_rate": req.lthr or None,
            "last_ftp_test": ftp_tests[-1].day.isoformat() if ftp_tests else None,
            "notes": req.notes or None,
        },
        "constraints": {
            "plan_from": req.first_day.isoformat(),
            "plan_until": req.last_day.isoformat(),
            "rides_per_week": req.rides_per_week,
            "rides_still_open_this_week": max(0, req.rides_per_week - req.rides_done_this_week()),
            "ride_minutes": req.ride_minutes,
        },
        "fitness_today": {
            "ctl_fitness": round(fitness.ctl, 1),
            "atl_fatigue": round(fitness.atl, 1),
            "tsb_form": round(fitness.tsb, 1),
        },
        "weekly_tss_last_6_weeks": weekly,
        "rides_last_6_weeks": rides,
        "missed_planned_workouts": missed,
        "previous_plan_upcoming": [
            {"date": w.day.isoformat(), "workout": w.name, "planned_tss": round(w.estimated_tss)}
            for w in (req.previous.workouts if req.previous else [])
            if w.day >= req.first_day
        ],
    }


def build_prompt(req: CoachRequest) -> str:
    action = "Update the training plan" if req.previous else "Create a training plan"
    return f"{action} for this rider.\n\n{json.dumps(build_context(req), indent=2)}"


# --- request ---

def request_plan(req: CoachRequest) -> TrainingPlan:
    """Ask Claude for a plan: through the API when a key is set, otherwise through Claude Code."""
    if has_api_key():
        return request_plan_api(req)
    exe = find_claude_code()
    if exe:
        return request_plan_claude_code(req, exe)
    raise CoachError("No Anthropic API key and no Claude Code found. Click 'API key…' or install Claude Code.")


def has_api_key() -> bool:
    return bool(stored_api_key() or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def backend_name() -> str | None:
    if has_api_key():
        return "Claude API"
    if find_claude_code():
        return "Claude Code (your subscription)"
    return None


def request_plan_api(req: CoachRequest, client: anthropic.Anthropic | None = None) -> TrainingPlan:
    client = client or make_client()
    try:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # retry on another model if a safety classifier declines
            system=system_prompt(),
            messages=[{"role": "user", "content": build_prompt(req)}],
            output_format=PlanOut,
        )
    except anthropic.AuthenticationError as exc:
        raise CoachError("The Anthropic API key was rejected. Check it with 'API key…'.") from exc
    except anthropic.RateLimitError as exc:
        raise CoachError("Claude is rate limited right now. Try again in a minute.") from exc
    except anthropic.APIStatusError as exc:
        raise CoachError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise CoachError("Could not reach Claude. Check the internet connection.") from exc

    if response.stop_reason == "refusal":
        raise CoachError("Claude declined to write the plan.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise CoachError("Claude's plan was incomplete. Try again.")
    log.info("Plan generated (request %s, usage %s)", response._request_id, response.usage)
    return to_plan(response.parsed_output, req)


# --- Claude Code route (uses the rider's Claude subscription) ---

def find_claude_code() -> str | None:
    """Newest Claude Code bundled with the VS Code extension, else `claude` on PATH."""
    binary = "claude.exe" if sys.platform == "win32" else "claude"
    bundled = glob.glob(str(Path.home() / ".vscode" / "extensions" / "anthropic.claude-code-*" /
                            "resources" / "native-binary" / binary))
    if bundled:
        def version(path: str) -> tuple[int, ...]:
            match = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", path)
            return tuple(int(n) for n in match.groups()) if match else (0,)

        return max(bundled, key=version)
    return shutil.which("claude")


@functools.cache
def _supports_flag(exe: str, flag: str) -> bool:
    try:
        help_text = subprocess.run([exe, "--help"], capture_output=True, text=True, encoding="utf-8",
                                   timeout=30, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return flag in help_text


def request_plan_claude_code(req: CoachRequest, exe: str, run=subprocess.run) -> TrainingPlan:
    command = [
        exe, "--print",
        "--output-format", "json",
        "--json-schema", json.dumps(PlanOut.model_json_schema()),
        "--system-prompt", system_prompt(),
        "--model", MODEL,
        "--tools", "",  # a plain answer: no file, shell or web access
        "--strict-mcp-config",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if _supports_flag(exe, "--effort"):
        command += ["--effort", "high"]
    workdir = config_dir()  # neutral folder, so no project instructions are picked up
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        proc = run(command, input=build_prompt(req), capture_output=True, text=True, encoding="utf-8",
                   cwd=workdir, timeout=CLAUDE_CODE_TIMEOUT, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise CoachError("Claude Code took too long to answer. Try again.") from exc
    except OSError as exc:
        raise CoachError(f"Could not start Claude Code: {exc}") from exc

    try:
        result = json.loads(proc.stdout)
    except ValueError:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise CoachError(f"Claude Code failed: {detail[-1] if detail else f'exit code {proc.returncode}'}")
    if result.get("is_error") or result.get("subtype") != "success":
        message = str(result.get("result") or result.get("subtype") or "unknown error")
        if "login" in message.lower() or "auth" in message.lower():
            message += " Open Claude Code and log in with your subscription."
        raise CoachError(f"Claude Code: {message}")
    try:
        out = PlanOut.model_validate(result["structured_output"])
    except (KeyError, ValueError) as exc:
        raise CoachError("Claude Code returned an incomplete plan. Try again.") from exc
    log.info("Plan generated via Claude Code (%s turns, %s ms)", result.get("num_turns"), result.get("duration_ms"))
    return to_plan(out, req)


def to_plan(out: PlanOut, req: CoachRequest) -> TrainingPlan:
    """Convert Claude's answer, dropping anything outside the rider's constraints."""
    per_week: dict[date, int] = {monday(req.today): req.rides_done_this_week()}
    library = {w.name.lower(): w for w in library_workouts()}
    workouts: list[PlannedWorkout] = []
    for w in sorted(out.workouts, key=lambda w: w.date):
        try:
            day = date.fromisoformat(w.date)
        except ValueError:
            log.warning("Dropping workout with bad date %r", w.date)
            continue
        week = monday(day)
        if not req.first_day <= day <= req.last_day or any(p.day == day for p in workouts):
            log.warning("Dropping workout on %s: outside the plan window or a second ride that day", day)
            continue
        if per_week.get(week, 0) >= req.rides_per_week:
            log.warning("Dropping workout on %s: week already has %d rides", day, req.rides_per_week)
            continue
        planned = _resolve(w, day, req.ride_minutes, library)
        if planned is None:
            continue
        per_week[week] = per_week.get(week, 0) + 1
        workouts.append(planned)

    weeks = []
    for wk in out.weeks:
        try:
            weeks.append(WeekFocus(monday(date.fromisoformat(wk.week_start)), wk.focus, 0))
        except ValueError:
            continue
    for wk in weeks:
        wk.target_tss = round(sum(w.estimated_tss for w in workouts if monday(w.day) == wk.monday))
    return TrainingPlan(datetime.now().astimezone(), out.coach_note, weeks, workouts)


def _resolve(w: WorkoutOut, day: date, ride_minutes: int, library: dict[str, Workout]) -> PlannedWorkout | None:
    """Turn one of Claude's workouts into a PlannedWorkout: an FTP test, a library workout or its own segments."""
    if w.ftp_test:
        return PlannedWorkout(day, RAMP_TEST_NAME, w.purpose, [], ftp_test=True)
    zone = min(max(w.power_zone, 1), len(ZONES))
    chosen = library.get((w.library_workout or "").strip().lower())
    if w.library_workout and chosen is None:
        log.warning("Claude picked unknown library workout %r on %s", w.library_workout, day)
    if chosen is not None and chosen.duration <= ride_minutes * 60 + FIT_TOLERANCE:
        fitted = chosen.fitted(ride_minutes)
        return PlannedWorkout(day, chosen.name, w.purpose, fitted.segments, zone=chosen.zone)
    segments = _segments(w.segments)
    if not segments:
        log.warning("Dropping workout on %s: no usable library workout or segments", day)
        return None
    return PlannedWorkout(day, w.name, w.purpose, segments, zone=zone)


def _segments(raw: list[SegmentOut]) -> list[Segment]:
    def fraction(pct: int | None) -> float | None:
        return None if pct is None else min(max(pct, 0), 300) / 100

    segments = []
    for s in raw:
        if s.duration_s <= 0:
            continue
        start, end = fraction(s.start_pct), fraction(s.end_pct)
        if start is None and end is not None:
            start = end
        segments.append(Segment(s.duration_s, start, end if start is not None else None))
    return segments
