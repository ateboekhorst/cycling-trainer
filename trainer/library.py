"""Built-in workout library, grouped by power zone (Coggan 7-zone model, % of FTP).

A workout's zone is the power zone of its main set. Zones 1-5 have 10 workouts each; the anaerobic and
sprint zones have a few. Most workouts are 30-60 minutes and are extended with zone 2 riding to fit
longer rides (Workout.fitted).
"""
from __future__ import annotations

from .metrics import zone_label, zone_range
from .workouts import Segment, Workout


def m(minutes: float, pct: float, end: float | None = None) -> Segment:
    """Segment of `minutes` at `pct` % FTP (ramping to `end` % if given)."""
    return Segment(round(minutes * 60), pct / 100, (pct if end is None else end) / 100)


def s(seconds: int, pct: float, end: float | None = None) -> Segment:
    return Segment(seconds, pct / 100, (pct if end is None else end) / 100)


def rep(times: int, *segments) -> list[Segment]:
    return _flatten(segments) * times


def _flatten(parts) -> list[Segment]:
    out: list[Segment] = []
    for part in parts:
        out.extend(_flatten(part) if isinstance(part, (list, tuple)) else [part])
    return out


def _workout(zone: int, name: str, description: str, *parts) -> Workout:
    return Workout(name, _flatten(parts), f"{zone_label(zone)} ({zone_range(zone)}): {description}", zone=zone)


EASY_IN = m(5, 40, 50)
EASY_OUT = m(5, 50, 40)
WARMUP = m(10, 45, 75)
WARMUP_OPENER = [m(10, 45, 75), m(3, 55)]
COOLDOWN = m(5, 55, 40)


def library_workouts() -> list[Workout]:
    return [
        # --- Zone 1: recovery (up to 55% FTP) ---
        _workout(1, "Recovery 30", "very easy spinning to promote recovery.", EASY_IN, m(20, 50), EASY_OUT),
        _workout(1, "Recovery 45", "a longer easy spin for the day after a hard session.", EASY_IN, m(35, 50), EASY_OUT),
        _workout(1, "Easy Spin 40", "steady, relaxed pedalling just below zone 2.", m(5, 40, 52), m(30, 52), m(5, 52, 40)),
        _workout(1, "Recovery Cadence Drills", "spin 100+ rpm in the 2-minute blocks; power stays easy.",
                 EASY_IN, rep(6, m(2, 50), m(3, 48)), m(5, 48, 40)),
        _workout(1, "Flush Ride 35", "alternating very easy and easy to flush the legs.",
                 m(5, 40, 48), rep(3, m(4, 45), m(4, 55)), m(6, 50, 40)),
        _workout(1, "Gentle Waves 40", "slow ramps up and down within recovery power.",
                 m(5, 40, 45), rep(3, m(5, 45, 55), m(5, 55, 45)), m(5, 45, 40)),
        _workout(1, "Easy Spin-Ups 35", "short, light cadence spin-ups (120 rpm) to keep the legs awake.",
                 EASY_IN, rep(5, s(20, 65), s(280, 48)), m(5, 48, 40)),
        _workout(1, "Steady Easy 50", "a longer steady recovery ride.", m(5, 40, 52), m(40, 53), m(5, 52, 40)),
        _workout(1, "Shakeout 30", "easy riding with three short openers, ideal before a test or event.",
                 m(8, 40, 55), rep(3, s(30, 75), s(150, 50)), m(8, 52), EASY_OUT),
        _workout(1, "Smooth Pedalling 40", "2-minute single-leg-focus blocks (switch legs every 30 s), then smooth.",
                 EASY_IN, rep(5, m(2, 47), m(4, 53)), m(5, 50, 40)),
        # --- Zone 2: endurance (56-75% FTP) ---
        _workout(2, "Endurance 60", "steady zone 2 aerobic endurance.", m(10, 45, 65), m(45, 68), m(5, 60, 40)),
        _workout(2, "Endurance 90", "long steady aerobic ride.", m(10, 45, 65), m(75, 68), m(5, 60, 40)),
        _workout(2, "Aerobic Base 75", "solid aerobic volume at mid zone 2.", m(10, 45, 68), m(60, 70), m(5, 60, 40)),
        _workout(2, "Endurance Pyramids", "slow ramps through zone 2 keep a long ride engaging.",
                 m(10, 45, 62), rep(4, m(5, 62, 72), m(5, 72, 62)), m(5, 65), m(5, 60, 40)),
        _workout(2, "Endurance Cadence Builds", "build cadence to 100+ rpm in each 3-minute block.",
                 m(10, 45, 65), rep(4, m(8, 66), m(3, 70)), m(6, 60, 40)),
        _workout(2, "Long Steady 120", "two hours of steady endurance for aerobic durability.",
                 m(10, 45, 65), m(105, 67), m(5, 60, 40)),
        _workout(2, "Rolling Hills Endurance", "short tempo rises like rolling terrain; heart rate stays in zone 2.",
                 m(10, 45, 65), rep(7, m(4, 65), m(2, 78)), m(3, 65), m(5, 60, 40)),
        _workout(2, "Low Zone 2 Steady", "easy-paced aerobic ride in low zone 2.", m(10, 45, 60), m(45, 63), m(5, 58, 40)),
        _workout(2, "Upper Zone 2 2x20", "two long blocks at the top of zone 2.",
                 m(10, 45, 68), m(20, 74), m(5, 60), m(20, 74), m(5, 60, 40)),
        _workout(2, "Endurance Low Cadence", "5-minute blocks at 60-70 rpm, seated, for muscular endurance.",
                 m(10, 45, 65), rep(5, m(5, 72), m(4, 62)), m(5, 60, 40)),
        # --- Zone 3: tempo and sweet spot (76-90% FTP) ---
        _workout(3, "Tempo 3x10", "three tempo blocks to build aerobic power.",
                 WARMUP, rep(3, m(10, 82), m(4, 60)), m(8, 60, 40)),
        _workout(3, "Tempo 2x20", "two long tempo blocks.", WARMUP, m(20, 80), m(5, 60), m(20, 80), m(5, 60, 40)),
        _workout(3, "Steady Tempo 40", "one long, unbroken tempo effort.", WARMUP, m(40, 78), m(10, 62, 40)),
        _workout(3, "Tempo with Bursts", "tempo with a 30-second surge every 3 minutes.",
                 WARMUP, rep(3, rep(4, s(150, 80), s(30, 105)), m(4, 60)), m(2, 55, 40)),
        _workout(3, "Sweet Spot 3x10", "three 10-minute intervals at 90% FTP.",
                 WARMUP, m(3, 55), rep(3, m(10, 90), m(5, 55)), m(5, 55, 40)),
        _workout(3, "Sweet Spot 2x20", "the classic sweet spot session.",
                 WARMUP, m(20, 89), m(5, 60), m(20, 89), m(5, 60, 40)),
        _workout(3, "Sweet Spot 4x8", "shorter sweet spot intervals, recovering briefly between them.",
                 WARMUP, rep(4, m(8, 90), m(3, 60)), m(6, 60, 40)),
        _workout(3, "Sweet Spot Progression", "each interval ramps from tempo into upper sweet spot.",
                 WARMUP, rep(3, m(12, 85, 93), m(3, 60)), COOLDOWN),
        _workout(3, "Tempo Pyramid", "5-10-10-10-5 minutes climbing to the top of tempo and back.",
                 WARMUP, m(5, 80), m(2, 60), m(10, 84), m(2, 60), m(10, 87), m(2, 60), m(10, 84), m(2, 60), m(5, 80),
                 COOLDOWN),
        _workout(3, "Tempo Cadence Changes", "in each block ride 4 min at 85 rpm, 4 min at 70 rpm, 4 min at 100 rpm.",
                 WARMUP, rep(3, m(12, 82), m(3, 60)), COOLDOWN),
        # --- Zone 4: threshold (91-105% FTP) ---
        _workout(4, "Threshold 2x15", "two 15-minute intervals at FTP.",
                 WARMUP_OPENER, rep(2, m(15, 100), m(5, 55)), COOLDOWN),
        _workout(4, "Threshold 3x10", "three 10-minute intervals just under FTP.",
                 WARMUP_OPENER, rep(3, m(10, 98), m(4, 55)), COOLDOWN),
        _workout(4, "Threshold 4x8", "four 8-minute intervals at FTP.",
                 WARMUP_OPENER, rep(4, m(8, 100), m(3, 55)), COOLDOWN),
        _workout(4, "Threshold 2x20", "two long intervals at the bottom of threshold.",
                 WARMUP_OPENER, m(20, 95), m(5, 55), m(20, 95), COOLDOWN),
        _workout(4, "Over-Unders 3x9", "alternate 2 minutes at 95% and 1 minute at 105% FTP.",
                 WARMUP_OPENER, rep(3, rep(3, m(2, 95), m(1, 105)), m(5, 55)), COOLDOWN),
        _workout(4, "Over-Unders 4x6", "1 minute under, 1 minute over FTP, three times per set.",
                 WARMUP_OPENER, rep(4, rep(3, m(1, 95), m(1, 105)), m(3, 55)), COOLDOWN),
        _workout(4, "Threshold Ladder", "4-6-8-6-4 minutes at FTP.",
                 WARMUP_OPENER, m(4, 100), m(2, 55), m(6, 100), m(3, 55), m(8, 100), m(3, 55), m(6, 100), m(2, 55),
                 m(4, 100), m(6, 55, 40)),
        _workout(4, "Threshold Surges", "threshold with a 20-second surge every 2 minutes.",
                 WARMUP_OPENER, rep(3, rep(5, s(100, 95), s(20, 125)), m(4, 55)), COOLDOWN),
        _workout(4, "Threshold Progression", "each interval ramps from 92% to 104% FTP.",
                 WARMUP_OPENER, rep(3, m(10, 92, 104), m(4, 55)), COOLDOWN),
        _workout(4, "Threshold 5x6", "five 6-minute intervals just above FTP.",
                 WARMUP_OPENER, rep(5, m(6, 102), m(3, 55)), m(4, 55, 40)),
        # --- Zone 5: VO2max (106-120% FTP) ---
        _workout(5, "VO2max 5x3", "five 3-minute intervals at 115% FTP.",
                 WARMUP_OPENER, rep(5, m(3, 115), m(3, 50)), m(7, 50, 40)),
        _workout(5, "VO2max 4x4", "four 4-minute intervals at 115% FTP.",
                 WARMUP_OPENER, rep(4, m(4, 115), m(4, 50)), m(5, 65), COOLDOWN),
        _workout(5, "VO2max 6x2", "six 2-minute intervals at 120% FTP.",
                 WARMUP_OPENER, rep(6, m(2, 120), m(2, 50)), m(10, 65), COOLDOWN),
        _workout(5, "40/20 Intervals", "three sets of 8 x 40 s hard / 20 s easy.",
                 WARMUP_OPENER, rep(3, rep(8, s(40, 120), s(20, 50)), m(5, 50)), COOLDOWN),
        _workout(5, "VO2max 5x5", "five longer VO2max intervals at 110% FTP.",
                 WARMUP_OPENER, rep(5, m(5, 110), m(4, 50)), COOLDOWN),
        _workout(5, "VO2max Pyramid", "1-2-3-4-3-2-1 minutes at 118% FTP with equal recovery.",
                 WARMUP_OPENER, [[m(n, 118), m(n, 50)] for n in (1, 2, 3, 4, 3, 2, 1)], m(5, 65), COOLDOWN),
        _workout(5, "VO2max 3x6", "three long VO2max intervals at 108% FTP.",
                 WARMUP_OPENER, rep(3, m(6, 108), m(5, 50)), m(5, 65), COOLDOWN),
        _workout(5, "VO2max 8x90s", "eight 90-second intervals at 120% FTP with equal recovery.",
                 WARMUP_OPENER, rep(8, s(90, 120), s(90, 50)), m(10, 65), COOLDOWN),
        _workout(5, "30/15 Intervals", "three sets of 13 x 30 s at 118% / 15 s easy (Ronnestad intervals).",
                 WARMUP_OPENER, rep(3, rep(13, s(30, 118), s(15, 50)), m(3, 50)), COOLDOWN),
        _workout(5, "VO2max Builds", "four 5-minute builds from 105% to 120% FTP.",
                 WARMUP_OPENER, rep(4, m(5, 105, 120), m(4, 50)), COOLDOWN),
        # --- Zone 6: anaerobic (121-150% FTP) ---
        _workout(6, "30/30 Intervals", "three sets of 8 x 30 s hard / 30 s easy.",
                 WARMUP_OPENER, rep(3, rep(8, s(30, 130), s(30, 50)), m(5, 50)), COOLDOWN),
        _workout(6, "15/15 Micro-Bursts", "three sets of 10 x 15 s at 140% / 15 s easy.",
                 WARMUP_OPENER, rep(3, rep(10, s(15, 140), s(15, 50)), m(5, 50)), m(5, 65), COOLDOWN),
        _workout(6, "Anaerobic 8x1", "eight 1-minute efforts at 140% FTP.",
                 WARMUP_OPENER, rep(8, m(1, 140), m(3, 50)), m(10, 65), COOLDOWN),
        # --- Zone 7: sprint (above 150% FTP) ---
        _workout(7, "Sprint Intervals 8x20s", "eight 20-second sprints. ERG can lag on sprints: switch ERG off for them "
                 "if you prefer.", WARMUP_OPENER, rep(8, s(20, 180), s(220, 50)), m(10, 65), COOLDOWN),
    ]
