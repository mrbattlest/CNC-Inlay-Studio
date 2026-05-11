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
# Ordered largest-first to match the cut order. Picked to be distinguishable
# on the dark canvas background and to avoid the existing red/yellow/green
# semantic colours (warnings, boundaries, travel moves) and the bright-magenta
# live machine indicator.
BIT_PALETTE = [
    QColor("#f5a623"),  # amber — same as POCKET_CLEAR for visual continuity
    QColor("#3dc8ff"),  # sky blue
    QColor("#bf80ff"),  # lavender
    QColor("#9ee84a"),  # lime
    QColor("#ff79c6"),  # pink
    QColor("#fb923c"),  # tangerine — distinct from amber (redder)
    QColor("#2dd4bf"),  # teal — distinct from sky + lime
    QColor("#818cf8"),  # indigo — distinct from lavender (bluer)
    QColor("#e879f9"),  # fuchsia — distinct from pink (more violet)
    QColor("#d4d4d8"),  # light cool grey — neutral, reads on dark
]


def color_for_bit_index(i: int) -> QColor:
    """Return the canvas colour for the i'th bit in a multi-bit sequence.

    For the first 10 bits, uses the hand-tuned BIT_PALETTE. Beyond that
    (rare — almost no inlay uses 11+ bits), generates additional colours
    by rotating the hue with the golden angle (≈137.5°) at a fixed
    saturation/value tuned for the dark canvas — so colours stay
    perceptually spread out and NEVER repeat, no matter how many bits.
    """
    if i < len(BIT_PALETTE):
        return BIT_PALETTE[i]
    # Procedural fallback. Offset the starting hue so the generated
    # colours don't collide with the static palette's hues, then step
    # by the golden angle for maximal spread.
    n = i - len(BIT_PALETTE)
    hue = (47.0 + n * 137.508) % 360.0  # degrees
    c = QColor()
    c.setHsvF(hue / 360.0, 0.62, 0.96)
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
        """
    )
