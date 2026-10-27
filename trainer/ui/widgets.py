"""Stat tiles and the workout profile chart."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..metrics import zone_index
from ..session import Ride
from . import theme


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class StatTile(QFrame):
    def __init__(self, title: str, unit: str = "", big: bool = False):
        super().__init__()
        self.setObjectName("tile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(0)

        title_label = QLabel(title.upper())
        title_label.setObjectName("tileTitle")
        layout.addWidget(title_label)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.value = QLabel("--")
        self.value.setObjectName("tileValueBig" if big else "tileValue")  # sized in theme.STYLE
        row.addWidget(self.value)
        self.unit = QLabel(unit)
        self.unit.setObjectName("tileUnit")
        row.addWidget(self.unit, alignment=Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        layout.addLayout(row)

    def set_value(self, text: str, color: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color};" if color else "")

    def set_unit(self, unit: str) -> None:
        self.unit.setText(unit)


class WorkoutChart(QWidget):
    """Workout targets as zone-coloured blocks with actual power and heart rate drawn over them."""

    MARGIN_LEFT, MARGIN_TOP, MARGIN_RIGHT, MARGIN_BOTTOM = 52, 14, 16, 26
    HR_RANGE = (40, 210)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(220)
        self.ride: Ride | None = None

    def set_ride(self, ride: Ride) -> None:
        self.ride = ride
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BACKGROUND))
        ride = self.ride
        if ride is None:
            return

        plot = QRectF(self.rect()).adjusted(self.MARGIN_LEFT, self.MARGIN_TOP, -self.MARGIN_RIGHT, -self.MARGIN_BOTTOM)
        workout, ftp = ride.workout, ride.ftp
        scale = ftp * ride.intensity

        if workout.open_ended:
            span = max(20 * 60, ride.elapsed * 1.1)
        elif workout.ramp_test:  # the test has no fixed end; look about five steps ahead
            span = min(workout.duration, max(20 * 60, ride.elapsed + 5 * 60))
        else:
            span = max(workout.duration, ride.elapsed + 30)
        visible_target = max(
            (max(s.start, s.end) for s, t in self._segment_starts(workout) if t < span and not s.is_free), default=0.0
        )
        actual_max = max((s.power for s in ride.samples[-3600:]), default=0)
        y_max = max(ftp * 1.3, visible_target * scale * 1.15, actual_max * 1.05, 100)
        y_max = (int(y_max) // 50 + 1) * 50

        def x(t: float) -> float:
            return plot.left() + t / span * plot.width()

        def y(watts: float) -> float:
            return plot.bottom() - watts / y_max * plot.height()

        self._draw_grid(p, plot, span, y_max, ftp, x, y)
        self._draw_segments(p, plot, ride, scale, x, y)
        self._draw_heart_rate(p, plot, ride, span, x)
        self._draw_power(p, plot, ride, span, x, y)

        cursor_x = x(ride.elapsed)
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        p.drawLine(QPointF(cursor_x, plot.top()), QPointF(cursor_x, plot.bottom()))

    def _draw_grid(self, p, plot, span, y_max, ftp, x, y) -> None:
        p.setFont(QFont(theme.FONT_FAMILY, 8))
        step = 50 if y_max <= 400 else 100
        for watts in range(step, int(y_max) + 1, step):
            p.setPen(QPen(QColor("#252a33"), 1))
            p.drawLine(QPointF(plot.left(), y(watts)), QPointF(plot.right(), y(watts)))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(0, y(watts) - 8, self.MARGIN_LEFT - 8, 16), Qt.AlignmentFlag.AlignRight, f"{watts}")

        minutes = next(m for m in (1, 2, 5, 10, 15, 30, 60) if span / 60 / m <= 10)
        for t in range(0, int(span) + 1, minutes * 60):
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(x(t) - 30, plot.bottom() + 4, 60, 16), Qt.AlignmentFlag.AlignHCenter, format_duration(t))

        pen = QPen(QColor(theme.MUTED), 1, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(plot.left(), y(ftp)), QPointF(plot.right(), y(ftp)))
        p.drawText(QRectF(plot.right() - 60, y(ftp) - 18, 56, 16), Qt.AlignmentFlag.AlignRight, "FTP")

    @staticmethod
    def _segment_starts(workout):
        t = 0
        for seg in workout.segments:
            yield seg, t
            t += seg.duration

    def _draw_segments(self, p, plot, ride, scale, x, y) -> None:
        located = ride.workout.locate(ride.elapsed)
        current = located[0] if located else -1
        p.setPen(Qt.PenStyle.NoPen)
        p.save()
        p.setClipRect(plot)  # ramp test steps beyond the visible span
        for i, (seg, t) in enumerate(self._segment_starts(ride.workout)):
            x0, x1 = x(t), x(t + seg.duration)
            if seg.is_free:
                p.setBrush(QBrush(QColor("#2e3440"), Qt.BrushStyle.BDiagPattern))
                p.drawRect(QRectF(x0, plot.top(), x1 - x0, plot.height()))
            else:
                color = theme.zone_color(zone_index((seg.start + seg.end) / 2))
                color.setAlpha(235 if i == current else 150)
                p.setBrush(color)
                p.drawPolygon(QPolygonF([
                    QPointF(x0, plot.bottom()),
                    QPointF(x0, y(seg.start * scale)),
                    QPointF(x1, y(seg.end * scale)),
                    QPointF(x1, plot.bottom()),
                ]))
        p.restore()

    def _series_path(self, values: list[tuple[int, float]], span: float, plot: QRectF, to_x, to_y) -> QPainterPath:
        path = QPainterPath()
        stride = max(1, int(len(values) / max(plot.width(), 1)))  # at most ~one point per pixel
        for n, (t, v) in enumerate(values[::stride]):
            point = QPointF(to_x(t), to_y(v))
            path.moveTo(point) if n == 0 else path.lineTo(point)
        return path

    @staticmethod
    def _smoothed(points: list[tuple[int, float]]) -> list[tuple[int, float]]:
        """3 s rolling average, like the power tile."""
        out, window = [], []
        for t, watts in points:
            window.append(watts)
            if len(window) > 3:
                window.pop(0)
            out.append((t, sum(window) / len(window)))
        return out

    def _draw_power(self, p, plot, ride, span, x, y) -> None:
        samples = ride.samples
        if len(samples) < 2:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        meter = [(s.elapsed, s.meter_power) for s in samples if s.meter_power is not None]
        if len(meter) >= 2:
            p.setPen(QPen(QColor(theme.METER_LINE), 1.4))
            p.drawPath(self._series_path(self._smoothed(meter), span, plot, x, y))
        p.setPen(QPen(QColor(theme.POWER_LINE), 1.6))
        p.drawPath(self._series_path(self._smoothed([(s.elapsed, s.power) for s in samples]), span, plot, x, y))
        if meter:
            self._draw_legend(p, plot)

    def _draw_legend(self, p, plot) -> None:
        p.setFont(QFont(theme.FONT_FAMILY, 8))
        left = plot.left() + 10
        for i, (label, color) in enumerate((("Trainer", theme.POWER_LINE), ("Power meter", theme.METER_LINE))):
            top = plot.top() + 6 + i * 16
            p.setPen(QPen(QColor(color), 2))
            p.drawLine(QPointF(left, top + 8), QPointF(left + 16, top + 8))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(left + 22, top, 100, 16), Qt.AlignmentFlag.AlignVCenter, label)

    def _draw_heart_rate(self, p, plot, ride, span, x) -> None:
        values = [(s.elapsed, s.heart_rate) for s in ride.samples if s.heart_rate]
        if len(values) < 2:
            return
        lo, hi = self.HR_RANGE

        def hr_y(bpm: float) -> float:
            return plot.bottom() - (bpm - lo) / (hi - lo) * plot.height()

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(theme.HR_LINE), 1.2))
        p.drawPath(self._series_path(values, span, plot, x, hr_y))
