"""Floating info overlay used by both canvas types to show grid scale and
the physical dimensions the user needs to prepare wood for.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget


class InfoOverlay(QLabel):
    """Small bottom-left overlay that shows grid spacing + workpiece size."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText("")
        self.setTextFormat(Qt.RichText)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QLabel {"
            "  background: rgba(38, 42, 49, 220);"
            "  color: #e8e8ea;"
            "  border: 1px solid #3a3f47;"
            "  border-radius: 6px;"
            "  padding: 6px 10px;"
            "  font-family: 'SF Mono','Menlo','Courier New',monospace;"
            "  font-size: 11px;"
            "  line-height: 14px;"
            "}"
        )
        self.adjustSize()
        self.hide()

    def set_info(
        self,
        grid_mm: float,
        size_label: str,
        width_mm: float,
        height_mm: float,
        units=None,
    ) -> None:
        from cnc_inlay.core.units import Units, fmt_dimensions

        if width_mm <= 0 or height_mm <= 0:
            self.hide()
            return
        u = units if units is not None else Units.METRIC
        if u == Units.METRIC:
            grid_text = f"{grid_mm:g} mm/square"
        else:
            grid_text = f"{grid_mm / 25.4:.3g} in/square"
        text = (
            f"<span style='color:#9aa0a6'>Grid:</span> {grid_text}<br>"
            f"<span style='color:#9aa0a6'>{size_label}:</span> "
            f"<span style='color:#f5a623; font-weight:600'>"
            f"{fmt_dimensions(width_mm, height_mm, u)}</span>"
        )
        self.setText(text)
        self.adjustSize()
        self.show()

    def position_bottom_left(self, host_width: int, host_height: int, margin: int = 12) -> None:
        self.adjustSize()
        self.move(margin, host_height - self.height() - margin)
