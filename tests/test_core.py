import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage

from trainer.devices.ftms import parse_indoor_bike_data
from trainer.devices.hrm import parse_heart_rate
from trainer.devices.simulator import speed_for_power
from trainer.export import save_ride
from trainer.metrics import LiveMetrics, best_average, normalized_power, ramp_test_ftp, time_in_zones, training_stress, zone_index
from trainer.session import Live, Ride, RideState, SensorBuffer
from trainer.workouts import Segment, Workout, builtin_workouts, load_course_file, load_zwo, ramp_test


# --- FTMS / HR parsing ---

def test_indoor_bike_data_speed_cadence_power_hr():
    flags = 0x0004 | 0x0040 | 0x0200  # speed (implicit), cadence, power, heart rate
    data = struct.pack("<HHHhB", flags, 3250, 180, 215, 142)
    d = parse_indoor_bike_data(data)
    assert d.speed_kmh == pytest.approx(32.5)
    assert d.cadence_rpm == 90
    assert d.power_w == 215
    assert d.heart_rate == 142


def test_indoor_bike_data_more_data_flag_skips_speed_and_handles_distance():
    flags = 0x0001 | 0x0010 | 0x0040  # no speed, distance (uint24), power
    data = struct.pack("<H", flags) + (12345).to_bytes(3, "little") + struct.pack("<h", 300)
    d = parse_indoor_bike_data(data)
    assert d.speed_kmh is None
    assert d.power_w == 300


def test_indoor_bike_data_truncated_keeps_parsed_fields():
    flags = 0x0004 | 0x0040
    d = parse_indoor_bike_data(struct.pack("<HHH", flags, 3000, 170))  # power missing
    assert d.speed_kmh == 30 and d.cadence_rpm == 85 and d.power_w is None


def test_heart_rate_8_and_16_bit():
    assert parse_heart_rate(bytes([0x00, 72])) == 72
    assert parse_heart_rate(bytes([0x01, 0x2C, 0x01])) == 300


def test_simulated_speed_is_plausible():
    assert 30 < speed_for_power(200, 0) < 40
    assert speed_for_power(200, 8) < 15


# --- workouts ---

def test_workout_targets_and_ramps():
    w = Workout("t", [Segment(60, 0.5, 1.0), Segment(60, 0.8)])
    assert w.duration == 120
    assert w.target_at(0) == 0.5
    assert w.target_at(30) == pytest.approx(0.75)
    assert w.target_at(90) == 0.8
    assert w.target_at(120) is None
    assert w.max_target == 1.0


def test_builtins_are_valid():
    for w in builtin_workouts():
        assert all(s.duration > 0 for s in w.segments), w.name


def test_load_zwo(tmp_path: Path):
    path = tmp_path / "test.zwo"
    path.write_text(
        """<workout_file><name>Test ZWO</name><description>d</description><workout>
        <Warmup Duration="600" PowerLow="0.4" PowerHigh="0.75"/>
        <IntervalsT Repeat="3" OnDuration="60" OffDuration="30" OnPower="1.2" OffPower="0.5"/>
        <SteadyState Duration="300" Power="0.88"/>
        <FreeRide Duration="120"/>
        <Cooldown Duration="300" PowerLow="0.6" PowerHigh="0.4"/>
        </workout></workout_file>"""
    )
    w = load_zwo(path)
    assert w.name == "Test ZWO"
    assert len(w.segments) == 1 + 6 + 1 + 1 + 1
    assert w.duration == 600 + 3 * 90 + 300 + 120 + 300
    assert w.segments[1].start == 1.2 and w.segments[2].start == 0.5
    assert w.segments[-2].is_free
    assert w.segments[-1].start == 0.6 and w.segments[-1].end == 0.4


def test_load_mrc_and_erg(tmp_path: Path):
    mrc = tmp_path / "a.mrc"
    mrc.write_text(
        "[COURSE HEADER]\nVERSION = 2\nDESCRIPTION = Steps\nMINUTES PERCENT\n[END COURSE HEADER]\n"
        "[COURSE DATA]\n0.00\t50\n10.00\t75\n10.00\t100\n20.00\t100\n[END COURSE DATA]\n"
    )
    w = load_course_file(mrc, ftp=250)
    assert [(s.duration, s.start, s.end) for s in w.segments] == [(600, 0.5, 0.75), (600, 1.0, 1.0)]

    erg = tmp_path / "b.erg"
    erg.write_text(
        "[COURSE HEADER]\nFTP = 200\nMINUTES WATTS\n[END COURSE HEADER]\n"
        "[COURSE DATA]\n0 100\n5 100\n5 200\n10 200\n[END COURSE DATA]\n"
    )
    w = load_course_file(erg, ftp=300)
    assert w.segments[1].start == 1.0


# --- metrics ---

def test_normalized_power_steady_equals_average():
    assert normalized_power([200] * 600) == pytest.approx(200)


def test_normalized_power_variable_exceeds_average():
    powers = ([300] * 60 + [100] * 60) * 10
    assert normalized_power(powers) > 200


def test_live_metrics_match_batch():
    powers = [150 + (i * 37) % 200 for i in range(900)]
    live = LiveMetrics()
    for p in powers:
        live.add(p, 140, 90)
    assert live.normalized_power == pytest.approx(normalized_power(powers))
    assert live.tss(250) == pytest.approx(training_stress(900, normalized_power(powers), 250))


def test_one_hour_at_ftp_is_100_tss():
    assert training_stress(3600, 250, 250) == pytest.approx(100)


def test_zones():
    # Coggan limits are inclusive: 55% is zone 1, 90% zone 3, 105% zone 4, 120% zone 5
    assert [zone_index(f) for f in (0.55, 0.56, 0.90, 1.05, 1.06, 1.20, 1.50, 2.0)] == [0, 1, 2, 3, 4, 4, 5, 6]
    assert sum(time_in_zones([100, 200, 300], 200)) == 3


# --- session ---

def test_sensor_buffer_averages_and_goes_stale():
    from trainer.devices.ftms import BikeData

    buf = SensorBuffer()
    buf.add_bike_data(BikeData(power_w=200, cadence_rpm=90, speed_kmh=30), now=10.0)
    buf.add_bike_data(BikeData(power_w=220), now=10.5)
    buf.add_heart_rate(150, now=10.5)
    live = buf.snapshot(now=11.0)
    assert live.power == 210 and live.cadence == 90 and live.heart_rate == 150
    stale = buf.snapshot(now=20.0)
    assert stale.power == 0 and stale.cadence is None and stale.heart_rate is None


def test_ride_auto_start_pause_resume_and_targets():
    w = Workout("t", [Segment(10, 0.5), Segment(10, 1.0)])
    ride = Ride(w, ftp=200)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ride.update(Live(power=0), t0)
    assert ride.state == RideState.READY and ride.target_watts() == 100

    for i in range(12):
        ride.update(Live(power=150, cadence=90, speed_kmh=36), t0 + timedelta(seconds=i))
    assert ride.state == RideState.RUNNING and ride.elapsed == 12
    assert ride.target_watts() == 200
    assert ride.distance_m == pytest.approx(120)

    ride.intensity = 0.9
    assert ride.target_watts() == 180

    for i in range(3):
        ride.update(Live(power=0, cadence=0), t0 + timedelta(seconds=20 + i))
    assert ride.state == RideState.PAUSED
    ride.update(Live(power=150, cadence=90), t0 + timedelta(seconds=30))
    assert ride.state == RideState.RUNNING

    ride.pause()  # manual pause is not auto-resumed
    ride.update(Live(power=150, cadence=90), t0 + timedelta(seconds=31))
    assert ride.state == RideState.PAUSED


def test_save_ride_writes_valid_fit(tmp_path: Path):
    ride = Ride(next(w for w in builtin_workouts() if w.name == "Sweet Spot 3x10"), ftp=250)
    t0 = datetime(2026, 10, 20, 18, 0, tzinfo=timezone(timedelta(hours=2)))
    for i in range(300):
        ride.update(Live(power=200 + i % 50, cadence=90, speed_kmh=32, heart_rate=140), t0 + timedelta(seconds=i))
    ride.finish()
    fit_path, csv_path = save_ride(ride, tmp_path)
    assert fit_path.with_suffix(".json").exists()
    assert fit_path.name == "2026-10-20_1800_Sweet-Spot-3x10.fit"
    assert len(csv_path.read_text().splitlines()) == 301

    fit = FitFile.from_file(str(fit_path))
    messages = [r.message for r in fit.records]
    records = [m for m in messages if isinstance(m, RecordMessage)]
    sessions = [m for m in messages if isinstance(m, SessionMessage)]
    assert len(records) == 300
    assert records[10].power == 210 and records[10].heart_rate == 140
    assert sessions[0].avg_power == round(ride.metrics.avg_power)
    assert sessions[0].total_distance == pytest.approx(ride.distance_m, rel=1e-3)


# --- ramp test ---

def test_best_average_and_ramp_ftp():
    powers = [100] * 120 + [300] * 60 + [200] * 30
    assert best_average(powers, 60) == 300
    assert ramp_test_ftp(powers) == 225
    assert ramp_test_ftp([250] * 59) is None


def test_ramp_test_workout_steps_up_each_minute():
    w = ramp_test()
    warmup = w.segments[0].duration
    assert w.ramp_test and warmup == 300
    assert w.target_at(warmup) == pytest.approx(0.55)
    assert w.target_at(warmup + 60) == pytest.approx(0.61)
    assert w.target_at(warmup + 10 * 60) == pytest.approx(1.15)


def test_ramp_test_ends_when_rider_stops_after_warmup():
    ride = Ride(ramp_test(), ftp=250)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t = 0
    for _ in range(100):  # stopping during the warm-up only pauses
        ride.update(Live(power=150, cadence=90), t0 + timedelta(seconds=t)); t += 1
    for _ in range(3):
        ride.update(Live(power=0, cadence=0), t0 + timedelta(seconds=t)); t += 1
    assert ride.auto_paused and not ride.ramp_test_over

    for _ in range(600):
        ride.update(Live(power=300, cadence=90), t0 + timedelta(seconds=t)); t += 1
    assert not ride.ramp_test_over
    for _ in range(3):
        ride.update(Live(power=0, cadence=0), t0 + timedelta(seconds=t)); t += 1
    assert ride.ramp_test_over
    ride.finish()
    assert not ride.ramp_test_over


# --- power meter ---

def test_power_measurement_with_balance_and_crank_data():
    from trainer.devices.power_meter import parse_power_measurement

    flags = 0x0001 | 0x0002 | 0x0020  # pedal balance (left reference) + crank revolution data
    data = struct.pack("<HhB", flags, 236, 100) + struct.pack("<HH", 1234, 5000)
    d = parse_power_measurement(data)
    assert d.power_w == 236 and d.crank_revs == 1234 and d.crank_time == 5000
    assert parse_power_measurement(struct.pack("<Hh", 0, 180)).crank_revs is None
    assert parse_power_measurement(b"\x00") is None


def test_cadence_from_crank_revolutions_wraps_and_drops_to_zero():
    from trainer.devices.power_meter import CadenceFromCrank

    c = CadenceFromCrank()
    assert c.update(65535, 65000, 0) == 0  # first reading only sets the baseline
    # one revolution in 0.666 s (682/1024 s) = 90 rpm, across both counters' wraparound
    assert c.update(0, (65000 + 682) % 65536, 0.7) == pytest.approx(90, abs=0.2)
    assert c.update(0, (65000 + 682) % 65536, 1.0) == pytest.approx(90, abs=0.2)  # no new event yet: hold
    assert c.update(0, (65000 + 682) % 65536, 3.0) == 0  # stopped pedalling


def test_power_meter_is_shown_but_training_uses_trainer_power():
    from trainer.devices.ftms import BikeData
    from trainer.devices.power_meter import PowerMeterReading

    buf = SensorBuffer()
    buf.add_bike_data(BikeData(power_w=250, cadence_rpm=90), now=10.0)
    buf.add_power_meter(PowerMeterReading(240, 88), now=10.2)
    live = buf.snapshot(now=10.5)
    assert live.power == 250 and live.meter_power == 240 and live.cadence == 90
    assert buf.snapshot(now=20.0).meter_power is None

    ride = Ride(Workout("t", [Segment(60, 1.0)]), ftp=250)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ride.update(live, t0)
    assert ride.samples[0].power == 250 and ride.samples[0].meter_power == 240
    assert ride.metrics.avg_power == 250
