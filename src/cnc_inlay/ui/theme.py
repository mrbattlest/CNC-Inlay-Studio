"""Visual styling for the CNC Inlay Studio app."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Brand palette — warm, woodshop-friendly.
BG = QColor("#1d1f24")
PANEL = QColor("#262a31")
PANEL_ELEVATED = QColor("#2f343c")
TEXT = QColor("#e8e8ea")
TEXT_DIM = QColor("#9aa0a6")
ACCENT = QColor("#f5a623")  # warm amber, like cherry shavings under a lamp
POCKET = QColor("#e0524a")        # V-carve walls (red)
POCKET_CLEAR = QColor("#f5a623")  # endmill flat-bottom clearing (orange)
BOUNDARY = QColor("#ffd84d")      # plug outer freeing cut (yellow)
PLUG = QColor("#3d8bff")          # legacy / tab grouping

# Per-bit colours for multi-bit (rest-machining) toolpath visualisation.
# Ordered largest-first to match the cut order. Ten colours spread around
# the hue wheel so every pair is clearly distinguishable on the dark
# canvas, deliberately dodging the two colours that ARE used elsewhere on
# the canvas: the travel-move green (#3eea7a) and the live-machine
# magenta (#bf3aff). (Warning red / yellow live only in UI badges, not on
# the canvas, so bit colours can use those hue zones.)
#
# Hue layout (approx): amber 37° · sky 199° · hot-pink 330° · lime 83° ·
# violet 258° · teal 173° · coral 351° · periwinkle 234° · magenta 292° ·
# neutral grey. Closest pair (hot-pink vs coral, ~21° apart) is still
# easily told apart by warmth + lightness; everything else is wider.
BIT_PALETTE = [
    QColor("#f5a623"),  # 1. amber / gold     — matches POCKET_CLEAR
    QColor("#38bdf8"),  # 2. sky blue
    QColor("#f472b6"),  # 3. hot pink         — magenta-leaning
    QColor("#a3e635"),  # 4. lime green       — yellow-leaning
    QColor("#a78bfa"),  # 5. violet / lavender
    QColor("#2dd4bf"),  # 6. teal             — clearly distinct from amber
    QColor("#fb7185"),  # 7. coral / salmon   — red-leaning, vs hot-pink
    QColor("#818cf8"),  # 8. periwinkle blue  — bluer than violet
    QColor("#c026d3"),  # 9. vivid magenta    — distinct from machine #bf3aff
    QColor("#cbd5e1"),  # 10. light cool grey — neutral
]


# Travel-move + machine-indicator colours that bit colours must avoid.
# (Declared above the procedural fallback so it can dodge them; the
# canonical definitions are also re-exported below alongside the other
# semantic colours.)
_TRAVEL_HUE_DEG = 141.0   # ≈ hue of TRAVEL #3eea7a
_MACHINE_HUE_DEG = 281.0  # ≈ hue of MACHINE_BIT #bf3aff
_FORBIDDEN_HUE_GUARD_DEG = 16.0


def color_for_bit_index(i: int) -> QColor:
    """Return the canvas colour for the i'th bit in a multi-bit sequence.

    For the first 10 bits uses the hand-tuned BIT_PALETTE (all mutually
    distinct). Beyond 10 — vanishingly rare — generates more colours by
    golden-angle hue rotation, snapping away from the travel-green and
    machine-magenta hues so the generated colours never collide with
    those canvas elements (or with each other).
    """
    if i < len(BIT_PALETTE):
        return BIT_PALETTE[i]
    n = i - len(BIT_PALETTE)
    hue = (19.0 + n * 137.508) % 360.0  # degrees — golden-angle spread
    # Push the hue out of the guard band around travel/machine hues.
    for forbidden in (_TRAVEL_HUE_DEG, _MACHINE_HUE_DEG):
        delta = ((hue - forbidden + 180.0) % 360.0) - 180.0
        if abs(delta) < _FORBIDDEN_HUE_GUARD_DEG:
            shift = _FORBIDDEN_HUE_GUARD_DEG if delta >= 0 else -_FORBIDDEN_HUE_GUARD_DEG
            hue = (forbidden + shift) % 360.0
    c = QColor()
    c.setHsvF(hue / 360.0, 0.66, 0.97)
    return c
TRAVEL = QColor("#3eea7a")        # G0 rapid moves above stock — bright green
MACHINE_BIT = QColor("#bf3aff")   # live cutting-head indicator — bright purple
GRID = QColor("#3a3f47")
SHAPE_LINE = QColor("#a8b0bb")


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, BG)
    pal.setColor(QPalette.WindowText, TEXT)
    pal.setColor(QPalette.Base, PANEL)
    pal.setColor(QPalette.AlternateBase, PANEL_ELEVATED)
    pal.setColor(QPalette.Text, TEXT)
    pal.setColor(QPalette.Button, PANEL_ELEVATED)
    pal.setColor(QPalette.ButtonText, TEXT)
    pal.setColor(QPalette.Highlight, ACCENT)
    pal.setColor(QPalette.HighlightedText, BG)
    pal.setColor(QPalette.PlaceholderText, TEXT_DIM)
    pal.setColor(QPalette.ToolTipBase, PANEL_ELEVATED)
    pal.setColor(QPalette.ToolTipText, TEXT)
    app.setPalette(pal)
    app.setStyleSheet(
        f"""
        QMainWindow, QWidget {{ background-color: {BG.name()}; color: {TEXT.name()}; }}
        QLabel#sectionTitle {{ color: {TEXT_DIM.name()}; font-size: 10px; font-weight: 700;
                              letter-spacing: 1.2px; text-transform: uppercase; padding-top: 8px; }}
        QLabel#hint        {{ color: {TEXT_DIM.name()}; font-size: 11px; }}
        QLabel#summary     {{ color: {TEXT.name()}; font-family: 'SF Mono','Menlo',monospace;
                              font-size: 12px; }}
        QGroupBox {{ border: 1px solid {GRID.name()}; border-radius: 6px;
                     margin-top: 12px; padding-top: 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px;
                            color: {TEXT_DIM.name()}; }}
        QPushButton#primary {{
            background-color: {ACCENT.name()}; color: {BG.name()}; font-weight: 600;
            border: none; border-radius: 6px; padding: 10px 16px;
        }}
        QPushButton#primary:hover {{ background-color: #ffb945; }}
        QPushButton#primary:disabled {{ background-color: #5b4d2a; color: #8a8377; }}
        QPushButton {{
            background-color: {PANEL_ELEVATED.name()}; color: {TEXT.name()};
            border: 1px solid {GRID.name()}; border-radius: 6px; padding: 7px 12px;
        }}
        QPushButton:hover {{ border-color: {ACCENT.name()}; color: {ACCENT.name()}; }}
        QPushButton:disabled {{ color: {TEXT_DIM.name()}; }}
        QComboBox, QDoubleSpinBox, QSpinBox {{
            background-color: {PANEL.name()}; color: {TEXT.name()};
            border: 1px solid {GRID.name()}; border-radius: 4px; padding: 4px 8px;
            min-height: 22px;
        }}
        QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {ACCENT.name()}; }}
        QStatusBar {{ background: {PANEL.name()}; color: {TEXT_DIM.name()}; }}
        QSplitter::handle {{ background: {GRID.name()}; }}
        QScrollArea {{ border: none; }}
        /* Checkbox indicators: the default Fusion-on-dark indicators almost
           disappear into the panel background. Give them an explicit 1.5 px
           amber border and a slightly larger box so the un/checked state is
           obvious at a glance. Same treatment for QRadioButton. */
        QCheckBox::indicator, QGroupBox::indicator {{
            width: 16px; height: 16px;
            border: 1.5px solid {ACCENT.name()};
            border-radius: 3px;
            background: {PANEL.name()};
        }}
        QCheckBox::indicator:hover, QGroupBox::indicator:hover {{
            background: #3a3320;
        }}
        QCheckBox::indicator:checked, QGroupBox::indicator:checked {{
            background: {ACCENT.name()};
        }}
        QCheckBox::indicator:checked:hover, QGroupBox::indicator:checked:hover {{
            background: #ffb945;
        }}
        QCheckBox::indicator:disabled, QGroupBox::indicator:disabled {{
            border-color: #5b4d2a;
            background: {PANEL.name()};
        }}
        QCheckBox::indicator:checked:disabled, QGroupBox::indicator:checked:disabled {{
            background: #5b4d2a;
        }}
        QRadioButton::indicator {{
            width: 16px; height: 16px;
            border: 1.5px solid {ACCENT.name()};
            border-radius: 9px;
            background: {PANEL.name()};
        }}
        QRadioButton::indicator:checked {{
            background: {ACCENT.name()};
        }}
        """
    )
