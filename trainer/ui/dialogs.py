"""Device picker and ride summary dialogs."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..devices.manager import DeviceManager, ScanResult
from ..devices.simulator import SIMULATOR_ADDRESS
from ..metrics import HR_ZONES, ZONES, best_average, ramp_test_ftp, time_in_hr_zones, time_in_zones
from ..session import Ride
from . import theme
from .widgets import format_duration


class DeviceDialog(QDialog):
    """Scan for and pick a trainer ("trainer"), heart rate strap ("hr") or power meter ("power")."""

    def __init__(self, devices: DeviceManager, kind: str, current_name: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.devices = devices
        self.selected: tuple[str, str] | None = None  # (address, name)
        self.disconnect_requested = False
        titles = {"trainer": "Connect trainer", "hr": "Connect heart rate monitor", "power": "Connect power meter"}
        self.setWindowTitle(titles[kind])
        self.resize(420, 380)

        layout = QVBoxLayout(self)
        status = f"Connected: {current_name}" if current_name else "Not connected"
        layout.addWidget(QLabel(status))
        hints = {
            "trainer": "Wake the trainer by pedalling and close Zwift, TrainerRoad or the Elite app — "
                       "a trainer only accepts one Bluetooth connection.",
            "hr": "Put the strap on (it only advertises when it detects a heartbeat).",
            "power": "Turn the cranks a few times to wake the power meter, and close Garmin Connect or other apps "
                     "that may be connected to it. Its power is shown as a second line in the chart; training "
                     "uses the trainer's power.",
        }
        hint = QLabel(hints[kind])
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.MUTED};")
        layout.addWidget(hint)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._accept_item)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        self.scan_button = QPushButton("Scan again")
        self.scan_button.clicked.connect(self._scan)
        buttons.addWidget(self.scan_button)
        if current_name:
            disconnect = QPushButton("Disconnect")
            disconnect.setObjectName("danger")
            disconnect.clicked.connect(self._disconnect)
            buttons.addWidget(disconnect)
        buttons.addStretch()
        connect = QPushButton("Connect")
        connect.setObjectName("primary")
        connect.clicked.connect(lambda: self._accept_item(self.list.currentItem()))
        buttons.addWidget(connect)
        layout.addLayout(buttons)

        devices.scan_finished.connect(self._show_results)
        self._show_results([])
        self._scan()

    def _scan(self) -> None:
        self.scan_button.setEnabled(False)
        self.scan_button.setText("Scanning…")
        self.devices.scan()

    def _show_results(self, results: list[ScanResult]) -> None:
        self.scan_button.setEnabled(True)
        self.scan_button.setText("Scan again")
        self.list.clear()
        for r in results:
            if self.kind in r.kinds:
                item = QListWidgetItem(f"{r.name}    ({r.rssi} dBm)")
                item.setData(Qt.ItemDataRole.UserRole, (r.address, r.name))
                self.list.addItem(item)
        if self.kind in ("trainer", "power"):
            item = QListWidgetItem("Simulator (no hardware)")
            item.setData(Qt.ItemDataRole.UserRole, (SIMULATOR_ADDRESS, "Simulator"))
            self.list.addItem(item)
        self.list.setCurrentRow(0)

    def _accept_item(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self.selected = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def _disconnect(self) -> None:
        self.disconnect_requested = True
        self.accept()

    def done(self, result: int) -> None:
        self.devices.scan_finished.disconnect(self._show_results)
        super().done(result)


class SummaryDialog(QDialog):
    def __init__(self, ride: Ride, files: list[Path], lthr: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ride summary")
        self.new_ftp: int | None = None  # set when the rider accepts a ramp test result
        m, ftp = ride.metrics, ride.ftp
        layout = QVBoxLayout(self)

        title = QLabel(ride.workout.name)
        title.setStyleSheet("font-size: 16pt; font-weight: 600;")
        layout.addWidget(title)

        if ride.workout.ramp_test:
            layout.addWidget(self._ramp_test_result(ride))

        grid = QGridLayout()
        stats = [
            ("Time", format_duration(ride.elapsed)),
            ("Distance", f"{ride.distance_m / 1000:.1f} km"),
            ("Avg power", f"{m.avg_power:.0f} W"),
            ("Normalized power", f"{m.normalized_power:.0f} W"),
            ("Max power", f"{m.max_power} W"),
            ("Intensity factor", f"{m.intensity_factor(ftp):.2f}"),
            ("TSS", f"{m.tss(ftp):.0f}"),
            ("Work", f"{m.kilojoules:.0f} kJ"),
            ("Avg heart rate", f"{m.avg_heart_rate:.0f} bpm" if m.avg_heart_rate else "--"),
            ("Max heart rate", f"{m.max_heart_rate} bpm" if m.max_heart_rate else "--"),
            ("Avg cadence", f"{m.avg_cadence:.0f} rpm" if m.avg_cadence else "--"),
        ]
        for i, (label, value) in enumerate(stats):
            name = QLabel(label)
            name.setStyleSheet(f"color: {theme.MUTED};")
            grid.addWidget(name, i // 2, (i % 2) * 2)
            grid.addWidget(QLabel(value), i // 2, (i % 2) * 2 + 1)
        layout.addLayout(grid)

        zones_row = QHBoxLayout()
        zones_row.setSpacing(24)
        power_zones = time_in_zones([s.power for s in ride.samples], ftp)
        zones_row.addLayout(self._zone_table("Time in power zones", ZONES, power_zones, theme.ZONE_COLORS))
        heart_rates = [s.heart_rate for s in ride.samples]
        if lthr and any(heart_rates):
            hr_zones = time_in_hr_zones(heart_rates, lthr)
            zones_row.addLayout(self._zone_table(f"Time in heart-rate zones (LTHR {lthr})", HR_ZONES, hr_zones,
                                                 theme.HR_ZONE_COLORS))
        layout.addLayout(zones_row)

        if files:
            links = "<br>".join(
                f'<a style="color: {theme.ACCENT};" href="{f.parent.as_uri()}">{f.name}</a>' for f in files
            )
            saved = QLabel("Saved:<br>" + links)
            saved.setOpenExternalLinks(True)
            saved.setStyleSheet(f"color: {theme.MUTED};")
            layout.addWidget(saved)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _zone_table(title: str, zones, seconds: list[int], colors: list[str]) -> QVBoxLayout:
        column = QVBoxLayout()
        column.addWidget(QLabel(title))
        grid = QGridLayout()
        total = max(sum(seconds), 1)
        for i, ((name, _), secs) in enumerate(zip(zones, seconds)):
            label = QLabel(name)
            label.setStyleSheet(f"color: {colors[i]};")
            grid.addWidget(label, i, 0)
            grid.addWidget(QLabel(format_duration(secs)), i, 1)
            grid.addWidget(QLabel(f"{secs / total:.0%}"), i, 2)
        column.addLayout(grid)
        column.addStretch()
        return column

    def _ramp_test_result(self, ride: Ride) -> QFrame:
        box = QFrame()
        box.setObjectName("tile")
        layout = QVBoxLayout(box)
        powers = [s.power for s in ride.samples]
        estimate = ramp_test_ftp(powers)
        if estimate is None:
            layout.addWidget(QLabel("The test was too short to estimate your FTP."))
            return box

        heading = QLabel("ESTIMATED FTP")
        heading.setObjectName("tileTitle")
        layout.addWidget(heading)
        value = QLabel(f"{estimate} W")
        value.setObjectName("tileValueBig")
        value.setStyleSheet(f"color: {theme.ACCENT};")
        layout.addWidget(value)
        change = estimate - ride.ftp
        detail = QLabel(
            f"75% of your best 1-minute power ({best_average(powers, 60):.0f} W). "
            f"Previous FTP {ride.ftp} W ({change:+d} W)."
        )
        detail.setWordWrap(True)
        detail.setStyleSheet(f"color: {theme.MUTED};")
        layout.addWidget(detail)

        use = QPushButton(f"Use {estimate} W as my FTP")
        use.setObjectName("primary")
        use.clicked.connect(lambda: self._accept_ftp(estimate))
        layout.addWidget(use)
        return box

    def _accept_ftp(self, ftp: int) -> None:
        self.new_ftp = ftp
        self.accept()
