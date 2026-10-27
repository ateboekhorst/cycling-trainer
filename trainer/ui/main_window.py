"""The main training screen."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings, rides_dir, workouts_dir
from ..devices.ftms import BikeData
from ..devices.power_meter import PowerMeterReading
from ..devices.manager import DeviceManager
from ..export import save_ride
from ..metrics import HR_ZONES, ZONES, zone_index, zone_label, zone_range
from ..session import Ride, RideState, SensorBuffer
from ..workouts import Workout, load_library
from . import theme
from .dialogs import DeviceDialog, SummaryDialog
from .plan_view import PlanView
from .widgets import StatTile, WorkoutChart, format_duration

log = logging.getLogger(__name__)

INTENSITY_STEP = 0.01
GRADE_STEP = 0.5
MIN_SAVED_RIDE = 60  # seconds


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, devices: DeviceManager):
        super().__init__()
        self.settings = settings
        self.devices = devices
        self.sensors = SensorBuffer()
        self.workouts: list[Workout] = []
        self.trainer_state = "disconnected"
        self.hr_state = "disconnected"
        self.power_meter_state = "disconnected"

        self.setWindowTitle("Cycling Trainer")
        self.resize(1280, 800)
        self._build_ui()
        self._reload_workouts(select=settings.last_workout)
        self.ride = self._new_ride()

        devices.trainer_data.connect(self._on_trainer_data)
        devices.heart_rate.connect(self._on_heart_rate)
        devices.trainer_state.connect(self._on_trainer_state)
        devices.hr_state.connect(self._on_hr_state)
        devices.power_meter_data.connect(self._on_power_meter_data)
        devices.power_meter_state.connect(self._on_power_meter_state)
        # Bound methods (not lambdas) so Qt delivers these on the UI thread.
        devices.error.connect(self._on_device_error)

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, lambda: self._on_ride_tab() and self._toggle_ride())
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self._on_ride_tab() and self._adjust(+1))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self._on_ride_tab() and self._adjust(-1))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        # Refresh the live power tile more often than the 1 Hz ride clock.
        self.fast_timer = QTimer(self)
        self.fast_timer.timeout.connect(self._refresh_power)
        self.fast_timer.start(250)

        if settings.trainer_address:
            devices.connect_trainer(settings.trainer_address)
        if settings.hr_address:
            devices.connect_hrm(settings.hr_address)
        if settings.power_meter_address:
            devices.connect_power_meter(settings.power_meter_address)
        self._refresh()

    # --- layout ---

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        content = QVBoxLayout()
        content.setContentsMargins(16, 16, 16, 8)
        content.setSpacing(10)

        big = QGridLayout()
        big.setSpacing(10)
        self.power_tile = StatTile("Power (3s)", "W", big=True)
        self.meter_tile = StatTile("Power meter (3s)", "W", big=True)
        self.meter_tile.hide()  # shown while a power meter sends data
        self.target_tile = StatTile("Target", "W", big=True)
        self.interval_tile = StatTile("Interval left", big=True)
        self.hr_tile = StatTile("Heart rate", "bpm", big=True)
        self.cadence_tile = StatTile("Cadence", "rpm", big=True)
        tiles = [self.power_tile, self.meter_tile, self.target_tile, self.interval_tile, self.hr_tile, self.cadence_tile]
        for i, tile in enumerate(tiles):
            big.addWidget(tile, 0, i)
        content.addLayout(big)

        small = QGridLayout()
        small.setSpacing(10)
        self.elapsed_tile = StatTile("Elapsed")
        self.remaining_tile = StatTile("Remaining")
        self.speed_tile = StatTile("Speed", "km/h")
        self.distance_tile = StatTile("Distance", "km")
        self.avg_tile = StatTile("Avg power", "W")
        self.np_tile = StatTile("NP", "W")
        self.if_tile = StatTile("IF")
        self.tss_tile = StatTile("TSS")
        self.kj_tile = StatTile("Work", "kJ")
        smalls = [self.elapsed_tile, self.remaining_tile, self.speed_tile, self.distance_tile,
                  self.avg_tile, self.np_tile, self.if_tile, self.tss_tile, self.kj_tile]
        for i, tile in enumerate(smalls):
            small.addWidget(tile, 0, i)
        content.addLayout(small)

        info = QHBoxLayout()
        self.interval_info = QLabel()
        self.interval_info.setObjectName("intervalInfo")
        self.next_info = QLabel()
        self.next_info.setObjectName("nextInfo")
        info.addWidget(self.interval_info)
        info.addStretch()
        info.addWidget(self.next_info)
        content.addLayout(info)

        self.chart = WorkoutChart()
        content.addWidget(self.chart, 1)
        outer.addLayout(content, 1)
        outer.addWidget(self._build_controls())

        self.tabs = QTabWidget()
        self.tabs.addTab(root, "Ride")
        self.plan_view = PlanView(self.settings, self._ride_planned)
        self.tabs.addTab(self.plan_view, "Plan")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)
        self.setStyleSheet(theme.STYLE)

    def _build_controls(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("controls")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(8)

        self.trainer_button = self._button("Connect trainer", self._choose_trainer)
        self.hr_button = self._button("Connect HR", self._choose_hrm)
        self.power_meter_button = self._button("Connect power meter", self._choose_power_meter)
        self.power_meter_button.setToolTip("Optional: shows your bike's power meter as a second line in the chart. "
                                           "Training and ERG always use the trainer's power.")
        row.addWidget(self.trainer_button)
        row.addWidget(self.hr_button)
        row.addWidget(self.power_meter_button)
        row.addSpacing(16)

        self.zone_combo = QComboBox()
        self.zone_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zone_combo.addItem("All workouts", "all")
        for zone in range(1, len(ZONES) + 1):
            self.zone_combo.addItem(zone_label(zone), zone)
            self.zone_combo.setItemData(self.zone_combo.count() - 1, f"Main set at {zone_range(zone)}",
                                        Qt.ItemDataRole.ToolTipRole)
        self.zone_combo.addItem("Tests, free ride & imported", "other")
        self.zone_combo.currentIndexChanged.connect(lambda _: self._populate_workouts())
        row.addWidget(self.zone_combo)
        self.workout_combo = QComboBox()
        self.workout_combo.setMinimumWidth(300)
        self.workout_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.workout_combo.currentIndexChanged.connect(self._on_workout_selected)
        row.addWidget(self.workout_combo)
        self.import_button = self._button("Import…", self._import_workouts)
        row.addWidget(self.import_button)
        row.addSpacing(16)

        row.addWidget(QLabel("FTP"))
        self.ftp_spin = QSpinBox()
        self.ftp_spin.setRange(50, 600)
        self.ftp_spin.setSuffix(" W")
        self.ftp_spin.setValue(self.settings.ftp)
        self.ftp_spin.valueChanged.connect(self._on_ftp_changed)
        row.addWidget(self.ftp_spin)
        row.addWidget(QLabel("LTHR"))
        self.lthr_spin = QSpinBox()
        self.lthr_spin.setRange(0, 230)
        self.lthr_spin.setSpecialValueText("not set")
        self.lthr_spin.setSuffix(" bpm")
        self.lthr_spin.setValue(self.settings.lthr)
        self.lthr_spin.setToolTip(
            "Lactate-threshold heart rate: your average heart rate over the last 20 minutes of a hard 30-minute "
            "solo effort. Used to show your heart-rate zone; workouts are targeted on power."
        )
        self.lthr_spin.valueChanged.connect(self._on_lthr_changed)
        row.addWidget(self.lthr_spin)
        row.addSpacing(16)

        self.erg_check = QCheckBox("ERG")
        self.erg_check.setChecked(True)
        self.erg_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.erg_check.toggled.connect(self._on_erg_toggled)
        row.addWidget(self.erg_check)
        row.addWidget(self._button("−", lambda: self._adjust(-1), "step"))
        self.adjust_label = QLabel()
        self.adjust_label.setMinimumWidth(110)
        self.adjust_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.adjust_label)
        row.addWidget(self._button("+", lambda: self._adjust(+1), "step"))

        row.addStretch()
        self.start_button = self._button("Start", self._toggle_ride, "primary")
        self.start_button.setMinimumWidth(100)
        self.finish_button = self._button("Finish", self._finish_ride, "danger")
        row.addWidget(self.start_button)
        row.addWidget(self.finish_button)
        return bar

    def _button(self, text: str, slot, name: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep Space for start/pause
        if name:
            button.setObjectName(name)
        button.clicked.connect(slot)
        return button

    # --- workouts ---

    def _reload_workouts(self, select: str = "") -> None:
        self.workouts = load_library(workouts_dir(), self.settings.ftp)
        self._planned_workout: Workout | None = None  # the plan's workout, shown at the top of the list
        selected = next((w for w in self.workouts if w.name == select), None)
        self._populate_workouts(selected)

    def _populate_workouts(self, select: Workout | None = None) -> None:
        """Fill the workout list for the chosen zone filter, keeping the selection where possible."""
        current = select or self.workout_combo.currentData()
        zone = self.zone_combo.currentData()
        shown = [w for w in self.workouts if zone == "all" or w.zone == zone or (zone == "other" and w.zone is None)]
        if self._planned_workout is not None:
            shown.insert(0, self._planned_workout)

        self.workout_combo.blockSignals(True)
        self.workout_combo.clear()
        for w in shown:
            prefix = "Plan: " if w is self._planned_workout else ""
            self.workout_combo.addItem(prefix + self._workout_label(w), w)
            if w.description:
                self.workout_combo.setItemData(self.workout_combo.count() - 1, w.description, Qt.ItemDataRole.ToolTipRole)
        index = next((i for i, w in enumerate(shown) if w is current), 0)
        self.workout_combo.setCurrentIndex(index)
        self.workout_combo.blockSignals(False)
        if hasattr(self, "ride") and self.ride.workout is not self._current_workout():
            self._on_workout_selected(index)

    @staticmethod
    def _workout_label(w: Workout) -> str:
        zone = f"Z{w.zone} · " if w.zone else ""
        if w.open_ended or w.ramp_test:
            return zone + w.name
        return f"{zone}{w.name}   ({format_duration(w.duration)} · {w.estimated_tss:.0f} TSS)"

    def _current_workout(self) -> Workout:
        return self.workout_combo.currentData() or self.workouts[0]

    def _on_workout_selected(self, _index: int) -> None:
        if self.ride.state == RideState.READY:
            workout = self._current_workout()
            if workout is not self._planned_workout:
                self.settings.last_workout = workout.name
                self.settings.save()
            self.ride = self._new_ride()
            self._refresh()

    def _import_workouts(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Import workouts", "", "Workouts (*.zwo *.mrc *.erg)")
        if not paths:
            return
        folder = workouts_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for path in paths:
            shutil.copy2(path, folder / Path(path).name)
        imported = {Path(p).name for p in paths}
        self._reload_workouts()
        first = next((w for w in self.workouts if w.source is not None and w.source.name in imported), None)
        self.zone_combo.setCurrentIndex(0)  # "All workouts", so the import is visible
        self._populate_workouts(first)
        self.statusBar().showMessage(f"Imported {len(paths)} workout(s) into {folder}", 8000)

    # --- ride ---

    def _new_ride(self) -> Ride:
        ride = Ride(self._current_workout(), self.settings.ftp, self.settings.auto_pause)
        ride.erg = self.erg_check.isChecked()
        self.chart.set_ride(ride)
        return ride

    def _tick(self) -> None:
        self.ride.update(self.sensors.snapshot())
        if self.ride.ramp_test_over:
            self._save_and_reset()
            return
        self._apply_trainer_control()
        self._refresh()

    def _toggle_ride(self) -> None:
        self.ride.toggle()
        self._refresh()

    def _finish_ride(self) -> None:
        if self.ride.elapsed == 0:
            return
        answer = QMessageBox.question(self, "Finish ride", "Finish this ride and save it?")
        if answer == QMessageBox.StandardButton.Yes:
            self._save_and_reset()

    def _save_and_reset(self) -> None:
        ride = self.ride
        ride.finish()
        files = []
        if ride.elapsed >= MIN_SAVED_RIDE:
            try:
                files = save_ride(ride, rides_dir())
            except Exception as exc:
                log.exception("Saving ride failed")
                QMessageBox.warning(self, "Save failed", f"Could not save the ride: {exc}")
        self._apply_trainer_control()  # release ERG load while the summary is open
        summary = SummaryDialog(ride, files, self.settings.lthr, self)
        summary.exec()
        if summary.new_ftp:
            self.ftp_spin.setValue(summary.new_ftp)
            self.statusBar().showMessage(f"FTP set to {summary.new_ftp} W", 8000)
        self.ride = self._new_ride()
        self._refresh()
        if files:
            self.plan_view.after_ride()  # Claude adjusts the plan to the actual TSS

    def _on_ride_tab(self) -> bool:
        return self.tabs.currentIndex() == 0

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self.plan_view.refresh()

    def _ride_planned(self, workout: Workout) -> None:
        """Load a workout from the plan into the Ride tab."""
        if self.ride.state != RideState.READY:
            QMessageBox.information(self, "Ride in progress", "Finish the current ride before starting another workout.")
            return
        self._planned_workout = workout
        self._populate_workouts(workout)
        self.tabs.setCurrentIndex(0)

    def _apply_trainer_control(self) -> None:
        if self.ride.state == RideState.FINISHED:
            self.devices.set_grade(0.0)
            return
        target = self.ride.erg_target()
        if target is not None:
            self.devices.set_target_power(target)
        else:
            self.devices.set_grade(self.ride.grade)

    def _adjusts_intensity(self) -> bool:
        return self.ride.erg and self.ride.target_fraction() is not None

    def _adjust(self, direction: int) -> None:
        if self._adjusts_intensity():
            self.ride.intensity = round(min(max(self.ride.intensity + direction * INTENSITY_STEP, 0.5), 1.5), 2)
        else:
            self.ride.grade = min(max(self.ride.grade + direction * GRADE_STEP, -10.0), 20.0)
        self._apply_trainer_control()
        self._refresh()

    def _on_erg_toggled(self, checked: bool) -> None:
        self.ride.erg = checked
        self._apply_trainer_control()
        self._refresh()

    def _on_lthr_changed(self, value: int) -> None:
        self.settings.lthr = value
        self.settings.save()
        self._refresh()

    def _on_ftp_changed(self, value: int) -> None:
        self.settings.ftp = value
        self.settings.save()
        self.ride.ftp = value
        self._apply_trainer_control()
        self._refresh()

    # --- devices ---

    def _on_trainer_data(self, data: BikeData) -> None:
        self.sensors.add_bike_data(data)

    def _on_heart_rate(self, bpm: int) -> None:
        self.sensors.add_heart_rate(bpm)

    def _on_device_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Bluetooth: {message}", 10000)

    def _choose_trainer(self) -> None:
        current = self.settings.trainer_name if self.trainer_state != "disconnected" else ""
        dialog = DeviceDialog(self.devices, "trainer", current, self)
        if dialog.exec():
            if dialog.disconnect_requested:
                self.settings.trainer_address = self.settings.trainer_name = ""
                self.devices.disconnect_trainer()
            elif dialog.selected:
                self.settings.trainer_address, self.settings.trainer_name = dialog.selected
                self.devices.connect_trainer(dialog.selected[0])
            self.settings.save()

    def _choose_hrm(self) -> None:
        current = self.settings.hr_name if self.hr_state != "disconnected" else ""
        dialog = DeviceDialog(self.devices, "hr", current, self)
        if dialog.exec():
            if dialog.disconnect_requested:
                self.settings.hr_address = self.settings.hr_name = ""
                self.devices.disconnect_hrm()
            elif dialog.selected:
                self.settings.hr_address, self.settings.hr_name = dialog.selected
                self.devices.connect_hrm(dialog.selected[0])
            self.settings.save()

    def _on_trainer_state(self, state: str) -> None:
        self.trainer_state = state
        self._style_device_button(self.trainer_button, state, self.settings.trainer_name or "trainer", "Connect trainer")
        if state == "connected":
            self._apply_trainer_control()

    def _on_hr_state(self, state: str) -> None:
        self.hr_state = state
        self._style_device_button(self.hr_button, state, self.settings.hr_name or "HR", "Connect HR")

    def _choose_power_meter(self) -> None:
        current = self.settings.power_meter_name if self.power_meter_state != "disconnected" else ""
        dialog = DeviceDialog(self.devices, "power", current, self)
        if dialog.exec():
            if dialog.disconnect_requested:
                self.settings.power_meter_address = self.settings.power_meter_name = ""
                self.devices.disconnect_power_meter()
            elif dialog.selected:
                self.settings.power_meter_address, self.settings.power_meter_name = dialog.selected
                self.devices.connect_power_meter(dialog.selected[0])
            self.settings.save()

    def _on_power_meter_state(self, state: str) -> None:
        self.power_meter_state = state
        self._style_device_button(self.power_meter_button, state, self.settings.power_meter_name or "power meter",
                                  "Connect power meter")

    def _on_power_meter_data(self, reading: PowerMeterReading) -> None:
        self.sensors.add_power_meter(reading)

    def _style_device_button(self, button: QPushButton, state: str, name: str, idle_text: str) -> None:
        text, color = {
            "connected": (f"● {name}", theme.GOOD),
            "connecting": (f"Connecting {name}…", theme.WARN),
            "reconnecting": (f"Reconnecting {name}…", theme.WARN),
        }.get(state, (idle_text, ""))
        button.setText(text)
        button.setStyleSheet(f"color: {color};" if color else "")

    # --- display ---

    def _refresh_power(self) -> None:
        power = self.sensors.power_avg(3)
        color = theme.ZONE_COLORS[zone_index(power / self.ride.ftp)] if power > 0 else None
        self.power_tile.set_value(f"{power:.0f}", color)

        meter = self.sensors.meter_power_avg(3)
        self.meter_tile.setVisible(meter is not None)
        if meter is not None:
            self.meter_tile.set_value(f"{meter:.0f}", theme.METER_LINE)
            difference = f"{(meter - power) / power:+.0%} vs trainer" if power >= 50 else ""
            self.meter_tile.set_unit(f"W {difference}".strip())

    def _refresh(self) -> None:
        ride, m, ftp = self.ride, self.ride.metrics, self.ride.ftp
        live = self.sensors.snapshot()
        self._refresh_power()

        target = ride.target_watts()
        if ride.erg and target is not None:
            self.target_tile.set_value(f"{target}")
            self.target_tile.set_unit("W")
        else:
            self.target_tile.set_value(f"{ride.grade:+.1f}")
            self.target_tile.set_unit("% slope")
        self._refresh_heart_rate(live.heart_rate)
        self.cadence_tile.set_value(f"{live.cadence:.0f}" if live.cadence is not None else "--")

        located = ride.workout.locate(ride.elapsed)
        if located:
            index, segment, start = located
            self.interval_tile.set_value(format_duration(start + segment.duration - ride.elapsed))
        else:
            self.interval_tile.set_value("--")

        self.elapsed_tile.set_value(format_duration(ride.elapsed))
        self.remaining_tile.set_value(
            "--" if ride.workout.open_ended or ride.workout.ramp_test
            else format_duration(ride.workout.duration - ride.elapsed)
        )
        self.speed_tile.set_value(f"{live.speed_kmh:.1f}" if live.speed_kmh is not None else "--")
        self.distance_tile.set_value(f"{ride.distance_m / 1000:.2f}")
        self.avg_tile.set_value(f"{m.avg_power:.0f}")
        self.np_tile.set_value(f"{m.normalized_power:.0f}")
        self.if_tile.set_value(f"{m.intensity_factor(ftp):.2f}")
        self.tss_tile.set_value(f"{m.tss(ftp):.0f}")
        self.kj_tile.set_value(f"{m.kilojoules:.0f}")

        self._refresh_interval_info(located)
        self.adjust_label.setText(
            f"Intensity {ride.intensity:.0%}" if self._adjusts_intensity() else f"Slope {ride.grade:+.1f}%"
        )
        self.start_button.setText({RideState.RUNNING: "Pause", RideState.PAUSED: "Resume"}.get(ride.state, "Start"))
        self.finish_button.setEnabled(ride.elapsed > 0)
        self.workout_combo.setEnabled(ride.state == RideState.READY)
        self.zone_combo.setEnabled(ride.state == RideState.READY)
        self.chart.update()

    def _refresh_heart_rate(self, bpm: int | None) -> None:
        lthr = self.settings.lthr
        if not bpm:
            self.hr_tile.set_value("--")
            self.hr_tile.set_unit("bpm")
        elif lthr:
            zone = zone_index(bpm / lthr, HR_ZONES)
            self.hr_tile.set_value(f"{bpm}", theme.HR_ZONE_COLORS[zone])
            self.hr_tile.set_unit(f"bpm · {HR_ZONES[zone][0]}")
        else:
            self.hr_tile.set_value(f"{bpm}")
            self.hr_tile.set_unit("bpm")

    def _describe(self, segment) -> str:
        if segment.is_free:
            return "Free ride"
        scale = self.ride.ftp * self.ride.intensity
        if abs(segment.start - segment.end) > 0.005:
            return f"Ramp {segment.start:.0%} → {segment.end:.0%} FTP ({segment.start * scale:.0f}–{segment.end * scale:.0f} W)"
        return f"{segment.start:.0%} FTP ({segment.start * scale:.0f} W)"

    def _refresh_interval_info(self, located) -> None:
        ride = self.ride
        if located:
            index, segment, _ = located
            if ride.workout.ramp_test:
                step = f"Step {index}" if index else "Warm-up"
                self.interval_info.setText(f"{step} · {self._describe(segment)} · ride until you can't hold it")
            else:
                self.interval_info.setText(f"Interval {index + 1} of {len(ride.workout.segments)} · {self._describe(segment)}")
            following = ride.workout.segments[index + 1] if index + 1 < len(ride.workout.segments) else None
            upcoming = f"Next: {self._describe(following)} for {format_duration(following.duration)}" if following else "Last interval"
        elif ride.workout.open_ended:
            self.interval_info.setText("Free ride · Up/Down changes the slope")
            upcoming = ""
        else:
            self.interval_info.setText("Workout complete! Keep riding or press Finish.")
            upcoming = ""

        if ride.state == RideState.READY:
            upcoming = "Start pedalling to begin"
        elif ride.state == RideState.PAUSED:
            upcoming = "Paused · pedal or press Space to resume"
        self.next_info.setText(upcoming)

    # --- shutdown ---

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.ride.elapsed > 0 and self.ride.state != RideState.FINISHED:
            answer = QMessageBox.question(
                self,
                "Ride in progress",
                "Save this ride before closing?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                self._save_and_reset()
        self.timer.stop()
        self.fast_timer.stop()
        self.settings.save()
        self.devices.shutdown()
        event.accept()
