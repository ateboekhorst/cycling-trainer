"""Colours and the application style sheet."""
from PySide6.QtGui import QColor

BACKGROUND = "#14171c"
PANEL = "#1d2128"
PANEL_BORDER = "#2a2f38"
TEXT = "#e8eaed"
MUTED = "#8b929c"
ACCENT = "#ffc93c"
POWER_LINE = "#f4f6f8"
HR_LINE = "#ff5c6c"
METER_LINE = "#4dd0e1"  # the bike's power meter, next to the trainer's POWER_LINE
GOOD = "#34c38f"
WARN = "#f59f3a"

# One colour per power zone (see metrics.ZONES).
ZONE_COLORS = ["#7d8796", "#3d8bfd", "#34c38f", "#f7c948", "#f59f3a", "#ef5350", "#b05cf5"]


# One colour per heart-rate zone 1-5 (see metrics.HR_ZONES); workouts use the power ZONE_COLORS.
HR_ZONE_COLORS = ["#7d8796", "#3d8bfd", "#34c38f", "#f7c948", "#ef5350"]


def zone_color(index: int) -> QColor:
    return QColor(ZONE_COLORS[index])


STYLE = f"""
QWidget {{ background: {BACKGROUND}; color: {TEXT}; font-family: "Segoe UI"; font-size: 10pt; }}
QFrame#tile {{ background: {PANEL}; border: 1px solid {PANEL_BORDER}; border-radius: 8px; }}
QLabel {{ background: transparent; }}
QLabel#tileTitle {{ color: {MUTED}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#tileValueBig {{ font-size: 40pt; font-weight: 600; }}
QLabel#tileValue {{ font-size: 18pt; font-weight: 600; }}
QLabel#tileUnit {{ color: {MUTED}; padding-bottom: 4px; }}
QLabel#intervalInfo {{ color: {TEXT}; font-size: 12pt; }}
QLabel#nextInfo {{ color: {MUTED}; font-size: 11pt; }}
QFrame#dayCell, QFrame#dayCellToday, QFrame#weekCell {{
    background: {PANEL}; border: 1px solid {PANEL_BORDER}; border-radius: 6px;
}}
QFrame#dayCellToday {{ border: 1px solid {ACCENT}; }}
QFrame#weekCell {{ background: #191c22; }}
QLabel#coachNote {{
    background: {PANEL}; border: 1px solid {PANEL_BORDER}; border-radius: 8px; padding: 10px 14px;
}}
QLineEdit {{ background: #2a2f38; border: 1px solid #3a404b; border-radius: 6px; padding: 5px 8px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: {BACKGROUND}; color: {MUTED}; padding: 8px 20px; border: none;
    border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QFrame#controls {{ background: {PANEL}; border-top: 1px solid {PANEL_BORDER}; }}
QPushButton {{
    background: #2a2f38; border: 1px solid #3a404b; border-radius: 6px; padding: 6px 12px;
}}
QPushButton:hover {{ background: #343a45; }}
QPushButton:disabled {{ color: #5c636e; }}
QPushButton#primary {{ background: {ACCENT}; color: #14171c; font-weight: 700; border: none; }}
QPushButton#primary:hover {{ background: #ffd65f; }}
QPushButton#danger {{ background: #4a2328; border-color: #6b2f36; }}
QPushButton#step {{ padding: 6px 10px; font-weight: 700; }}
QComboBox, QSpinBox {{
    background: #2a2f38; border: 1px solid #3a404b; border-radius: 6px; padding: 5px 8px;
}}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: #343a45; }}
QListWidget {{ background: {PANEL}; border: 1px solid {PANEL_BORDER}; border-radius: 6px; }}
QListWidget::item {{ padding: 8px; }}
QListWidget::item:selected {{ background: #343a45; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
"""
