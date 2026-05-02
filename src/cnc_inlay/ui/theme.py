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
# semantic colours (warnings, boundaries, travel moves).
BIT_PALETTE = [
    QColor("#f5a623"),  # amber — same as POCKET_CLEAR for visual continuity
    QColor("#3dc8ff"),  # cyan
    QColor("#bf80ff"),  # lavender
    QColor("#9ee84a"),  # lime
    QColor("#ff79c6"),  # pink
]


def color_for_bit_index(i: int) -> QColor:
    """Return the canvas colour for the i'th bit in a multi-bit sequence.
    Wraps if there are more bits than entries in the palette."""
    return BIT_PALETTE[i % len(BIT_PALETTE)]
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
        QPushButton:hover {{ border-color: {ACCENT.name()}; }}
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
