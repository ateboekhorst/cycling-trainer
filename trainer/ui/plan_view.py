"""Plan tab: Claude's 4-week FTP plan as a calendar, with planned versus actual TSS."""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..coach import CoachError, CoachRequest, backend_name, request_plan, store_api_key
from ..config import Settings, plan_path, rides_dir
from ..fitness import fitness_on, monday
from ..history import RideRecord, load_history
from ..metrics import zone_label, zone_range
from ..plan import PlannedWorkout, TrainingPlan, load_plan, plan_window, save_plan
from ..workouts import Workout
from . import theme
from .widgets import StatTile, format_duration

log = logging.getLogger(__name__)

WEEKS_BACK = 1  # past weeks shown above the current one
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class _PlanWorker(QObject):
    """Runs the Claude request off the UI thread."""

    finished = Signal(object, object)  # TrainingPlan, first planned day
    failed = Signal(str)

    def start(self, request: CoachRequest) -> None:
        threading.Thread(target=self._run, args=(request,), name="coach", daemon=True).start()

    def _run(self, request: CoachRequest) -> None:
        try:
            self.finished.emit(request_plan(request), request.first_day)
        except CoachError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            log.exception("Plan generation failed")
            self.failed.emit(f"Plan generation failed: {exc}")


class PlanView(QWidget):
    def __init__(self, settings: Settings, on_ride: Callable[[Workout], None]):
        super().__init__()
        self.settings = settings
        self.on_ride = on_ride
        self.records: list[RideRecord] = []
        self.plan: TrainingPlan | None = None
        self.generating = False
        self.worker = _PlanWorker()
        self.worker.finished.connect(self._on_plan_ready)
        self.worker.failed.connect(self._on_plan_failed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(self._build_controls())

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.ctl_tile = StatTile("Fitness (CTL)")
        self.atl_tile = StatTile("Fatigue (ATL)")
        self.tsb_tile = StatTile("Form (TSB)")
        self.week_tile = StatTile("This week", "TSS")
        self.ftp_tile = StatTile("FTP", "W")
        for tile in (self.ctl_tile, self.atl_tile, self.tsb_tile, self.week_tile, self.ftp_tile):
            tiles.addWidget(tile)
        layout.addLayout(tiles)

        self.note = QLabel()
        self.note.setObjectName("coachNote")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll, 1)
        self.refresh()

    def _build_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Rides per week"))
        self.rides_spin = QSpinBox()
        self.rides_spin.setRange(1, 7)
        self.rides_spin.setValue(self.settings.plan_rides_per_week)
        self.rides_spin.valueChanged.connect(self._on_settings_changed)
        row.addWidget(self.rides_spin)
        row.addSpacing(8)
        row.addWidget(QLabel("Ride duration"))
        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(20, 240)
        self.minutes_spin.setSingleStep(5)
        self.minutes_spin.setSuffix(" min")
        self.minutes_spin.setValue(self.settings.plan_ride_minutes)
        self.minutes_spin.valueChanged.connect(self._on_settings_changed)
        row.addWidget(self.minutes_spin)
        row.addSpacing(8)
        self.notes_edit = QLineEdit(self.settings.plan_notes)
        self.notes_edit.setPlaceholderText("Notes for the coach, e.g. no rides on Wednesday, long ride on Saturday")
        self.notes_edit.editingFinished.connect(self._on_settings_changed)
        row.addWidget(self.notes_edit, 1)

        self.status = QLabel()
        self.status.setStyleSheet(f"color: {theme.MUTED};")
        row.addWidget(self.status)
        key_button = QPushButton("API key…")
        key_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        key_button.clicked.connect(self._set_api_key)
        row.addWidget(key_button)
        self.update_button = QPushButton("Create plan with Claude")
        self.update_button.setObjectName("primary")
        self.update_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.update_button.clicked.connect(lambda: self.update_plan())
        row.addWidget(self.update_button)
        return row

    # --- data ---

    def refresh(self) -> None:
        self.records = load_history(rides_dir())
        self.plan = load_plan(plan_path())
        today = date.today()
        self.update_button.setText("Update plan with Claude" if self.plan else "Create plan with Claude")
        self.update_button.setEnabled(not self.generating)

        fitness = fitness_on(self.records, today + timedelta(days=1))  # includes today's rides
        self.ctl_tile.set_value(f"{fitness.ctl:.0f}")
        self.atl_tile.set_value(f"{fitness.atl:.0f}")
        tsb_color = theme.WARN if fitness.tsb < -25 else theme.GOOD if fitness.tsb > 5 else None
        self.tsb_tile.set_value(f"{fitness.tsb:+.0f}", tsb_color)
        week_done = self._actual_tss(monday(today), monday(today) + timedelta(days=6))
        week_plan = self.plan.week(monday(today)) if self.plan else None
        planned = self._planned_tss(monday(today))
        self.week_tile.set_value(f"{week_done:.0f} / {planned:.0f}" if week_plan or planned else f"{week_done:.0f}")
        self.ftp_tile.set_value(f"{self.settings.ftp}")

        if self.plan:
            self.note.setText(f"<b>Coach</b> · {self.plan.created:%a %d %b %H:%M}<br>{self.plan.coach_note}")
        else:
            self.note.setText(
                "<b>No plan yet.</b> Set how many rides per week you can do and how long each ride is, then click "
                "<b>Create plan with Claude</b>. Claude plans the next 4 weeks to raise your FTP and adjusts the "
                "plan after every ride, based on the TSS you actually rode."
            )
        self.scroll.setWidget(self._build_calendar(today))

    def _actual_tss(self, first: date, last: date) -> float:
        return sum(r.tss for r in self.records if first <= r.day <= last)

    def _planned_tss(self, week_monday: date) -> float:
        if not self.plan:
            return 0.0
        return sum(w.estimated_tss for w in self.plan.workouts if monday(w.day) == week_monday)

    # --- calendar ---

    def _build_calendar(self, today: date) -> QWidget:
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)
        for col, name in enumerate(WEEKDAYS + ["Week"]):
            header = QLabel(name.upper())
            header.setObjectName("tileTitle")
            grid.addWidget(header, 0, col)
            grid.setColumnStretch(col, 1)

        rides_by_day: dict[date, list[RideRecord]] = {}
        for r in self.records:
            rides_by_day.setdefault(r.day, []).append(r)

        first_monday = monday(today) - timedelta(weeks=WEEKS_BACK)
        last_day = plan_window(today, False)[1]
        week_count = (last_day - first_monday).days // 7 + 1
        for row in range(week_count):
            week_monday = first_monday + timedelta(weeks=row)
            for col in range(7):
                day = week_monday + timedelta(days=col)
                planned = self.plan.workout_on(day) if self.plan else None
                grid.addWidget(self._day_cell(day, today, planned, rides_by_day.get(day, [])), row + 1, col)
            grid.addWidget(self._week_cell(week_monday, today), row + 1, 7)
        grid.setRowStretch(week_count + 1, 1)
        return holder

    def _day_cell(self, day: date, today: date, planned: PlannedWorkout | None, rides: list[RideRecord]) -> QFrame:
        cell = QFrame()
        cell.setObjectName("dayCellToday" if day == today else "dayCell")
        cell.setMinimumHeight(118)
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        heading = QLabel(f"Today · {day:%d %b}" if day == today else f"{day:%d %b}")
        heading.setStyleSheet(f"color: {theme.ACCENT if day == today else theme.MUTED}; font-size: 8pt;")
        layout.addWidget(heading)

        if planned and planned.zone:
            color = theme.ZONE_COLORS[planned.zone - 1]
            cell.setStyleSheet(f"QFrame#{cell.objectName()} {{ border-left: 4px solid {color}; }}")

        if rides:
            for r in rides:
                color = theme.TEXT
                if planned:
                    ratio = r.tss / max(planned.estimated_tss, 1)
                    color = theme.GOOD if 0.85 <= ratio <= 1.15 else theme.WARN
                done = QLabel(f"✓ {r.workout}")
                done.setWordWrap(True)
                done.setStyleSheet("font-weight: 600;")
                layout.addWidget(done)
                stats = QLabel(f"{r.tss:.0f} TSS · {format_duration(r.duration_s)}")
                stats.setStyleSheet(f"color: {color};")
                layout.addWidget(stats)
            if planned:
                target = QLabel(f"planned ~{planned.estimated_tss:.0f} TSS")
                target.setStyleSheet(f"color: {theme.MUTED}; font-size: 8pt;")
                layout.addWidget(target)
        elif planned and day < today:
            missed = QLabel(f"✗ Missed: {planned.name}")
            missed.setWordWrap(True)
            missed.setStyleSheet(f"color: {theme.WARN};")
            layout.addWidget(missed)
        elif planned:
            name = QLabel(planned.name)
            name.setWordWrap(True)
            name.setStyleSheet("font-weight: 600;")
            name.setToolTip(planned.purpose)
            layout.addWidget(name)
            length = "until failure" if planned.ftp_test else format_duration(planned.to_workout().duration)
            zone = f"Z{planned.zone} · " if planned.zone else ""
            info = QLabel(f"{zone}{length} · ~{planned.estimated_tss:.0f} TSS")
            if planned.zone:
                info.setToolTip(f"{zone_label(planned.zone)} ({zone_range(planned.zone)})")
            info.setStyleSheet(f"color: {theme.MUTED};")
            layout.addWidget(info)
            ride = QPushButton("Ride ▶")
            ride.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ride.clicked.connect(lambda _=False, p=planned: self.on_ride(p.to_workout()))
            layout.addWidget(ride)
        elif day >= today and self.plan:
            rest = QLabel("Rest")
            rest.setStyleSheet(f"color: {theme.MUTED};")
            layout.addWidget(rest)
        layout.addStretch()
        return cell

    def _week_cell(self, week_monday: date, today: date) -> QFrame:
        cell = QFrame()
        cell.setObjectName("weekCell")
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        week = self.plan.week(week_monday) if self.plan else None
        if week:
            focus = QLabel(week.focus)
            focus.setWordWrap(True)
            focus.setStyleSheet("font-weight: 600;")
            layout.addWidget(focus)
        planned = self._planned_tss(week_monday)
        if planned:
            layout.addWidget(QLabel(f"Planned {planned:.0f} TSS"))
        if week_monday <= today:
            done = self._actual_tss(week_monday, week_monday + timedelta(days=6))
            label = QLabel(f"Done {done:.0f} TSS")
            label.setStyleSheet(f"color: {theme.GOOD if planned and done >= 0.85 * planned else theme.MUTED};")
            layout.addWidget(label)
        layout.addStretch()
        return cell

    # --- Claude ---

    def update_plan(self, automatic: bool = False) -> None:
        if self.generating:
            return
        backend = backend_name()
        if automatic and not (self.plan and backend):
            return
        today = date.today()
        self.records = load_history(rides_dir())
        rode_today = any(r.day == today for r in self.records)
        first, last = plan_window(today, rode_today)
        request = CoachRequest(
            today=today,
            ftp=self.settings.ftp,
            rides_per_week=self.settings.plan_rides_per_week,
            ride_minutes=self.settings.plan_ride_minutes,
            notes=self.settings.plan_notes,
            records=self.records,
            previous=self.plan,
            first_day=first,
            last_day=last,
            lthr=self.settings.lthr,
        )
        self.generating = True
        self.update_button.setEnabled(False)
        via = f" via {backend}" if backend else ""
        self._set_status(f"Claude is working on your plan{via}… this can take a minute or two.")
        self.worker.start(request)

    def after_ride(self) -> None:
        """A ride was saved: show it, and let Claude adjust the plan to the actual TSS."""
        self.refresh()
        self.update_plan(automatic=True)

    def _on_plan_ready(self, plan: TrainingPlan, first_day: date) -> None:
        self.generating = False
        merged = plan.merged_into(load_plan(plan_path()), first_day)
        save_plan(merged, plan_path())
        self._set_status(f"Plan updated at {merged.created:%H:%M}")
        self.refresh()

    def _on_plan_failed(self, message: str) -> None:
        self.generating = False
        self.update_button.setEnabled(True)
        self._set_status(message, error=True)

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {theme.WARN if error else theme.MUTED};")

    def _on_settings_changed(self) -> None:
        changed = (
            self.rides_spin.value() != self.settings.plan_rides_per_week
            or self.minutes_spin.value() != self.settings.plan_ride_minutes
            or self.notes_edit.text() != self.settings.plan_notes
        )
        if not changed:
            return
        self.settings.plan_rides_per_week = self.rides_spin.value()
        self.settings.plan_ride_minutes = self.minutes_spin.value()
        self.settings.plan_notes = self.notes_edit.text()
        self.settings.save()
        if self.plan and not self.generating:
            self._set_status("Settings changed: click Update plan to apply them.")

    def _set_api_key(self) -> None:
        key, ok = QInputDialog.getText(
            self,
            "Anthropic API key",
            "Paste your Anthropic API key (stored in Windows Credential Manager).\n"
            "Leave empty to remove it: the app then uses Claude Code with your Claude subscription.",
            QLineEdit.EchoMode.Password,
        )
        if ok:
            try:
                store_api_key(key.strip())
                self._set_status(f"Using {backend_name() or 'no Claude connection'}.")
            except ValueError as exc:
                QMessageBox.warning(self, "API key", str(exc))
            except Exception as exc:
                QMessageBox.warning(self, "API key", f"Could not store the key: {exc}")
