"""Floating zoom-toolbar overlay used by both the 2D and 3D canvas widgets.

Three buttons stacked vertically: zoom-in, zoom-out, and fit/reset. The host
canvas connects its own zoom implementation to the emitted signals.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget


class ZoomToolbar(QWidget):
    zoomIn = Signal()
    zoomOut = Signal()
    fit = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # Allow click-through on the empty layout margins.
        self.setAttribute(Qt.WA_NoMousePropagation, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._in_btn = QPushButton("+", self)
        self._out_btn = QPushButton("−", self)
        self._fit_btn = QPushButton("⌂", self)
        self._in_btn.setToolTip("Zoom in")
        self._out_btn.setToolTip("Zoom out")
        self._fit_btn.setToolTip("Fit to view")

        style = (
            "QPushButton {"
            "  background: rgba(38, 42, 49, 220);"
            "  color: #e8e8ea;"
            "  border: 1px solid #3a3f47;"
            "  border-radius: 6px;"
            "  font-size: 16px;"
            "  font-weight: 700;"
            "  min-width: 32px;"
            "  min-height: 32px;"
            "}"
            "QPushButton:hover { border-color: #f5a623; color: #f5a623; }"
            "QPushButton:pressed { background: rgba(60, 65, 75, 240); }"
        )
        for btn in (self._in_btn, self._out_btn, self._fit_btn):
            btn.setFixedSize(32, 32)
            btn.setStyleSheet(style)
            btn.setCursor(Qt.PointingHandCursor)
            layout.addWidget(btn)

        self._in_btn.clicked.connect(self.zoomIn.emit)
        self._out_btn.clicked.connect(self.zoomOut.emit)
        self._fit_btn.clicked.connect(self.fit.emit)

        self.adjustSize()

    def position_top_right(self, host_width: int, host_height: int, margin: int = 12) -> None:
        self.adjustSize()
        self.move(host_width - self.width() - margin, margin)
