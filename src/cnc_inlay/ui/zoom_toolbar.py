"""Floating zoom-toolbar overlay used by both the 2D and 3D canvas widgets.

Three buttons stacked vertically: zoom-in, zoom-out, and fit/reset. The host
canvas connects its own zoom implementation to the emitted signals.

Plus a ViewModeToggle widget — the 3D / Top-down switch that sits just
above the zoom toolbar on each pocket / plug canvas, replacing the old
nested QTabWidget design.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget


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
        # The ⌂ glyph at font-size 16 has a tall body that clips at the
        # bottom of a 32px button. A slightly smaller font centres it
        # cleanly without making the icon look out of scale.
        self._fit_btn.setStyleSheet(style.replace("font-size: 16px;", "font-size: 13px;"))

        self._in_btn.clicked.connect(self.zoomIn.emit)
        self._out_btn.clicked.connect(self.zoomOut.emit)
        self._fit_btn.clicked.connect(self.fit.emit)

        self.adjustSize()

    def position_top_right(
        self, host_width: int, host_height: int,
        margin: int = 12, top_offset: int = 0,
    ) -> None:
        """Anchor the toolbar to the host's top-right corner. ``top_offset``
        pushes it further down — used by canvases that ALSO host a
        ViewModeToggle above the zoom buttons."""
        self.adjustSize()
        self.move(host_width - self.width() - margin, margin + top_offset)


class ViewModeToggle(QWidget):
    """3D / Top-down pill switch overlaid on the pocket and plug canvases.

    Replaces the old nested QTabWidget — one click swaps the visible
    canvas instead of forcing the user to look at a stack of identical
    tab strips. Emits ``modeChanged("3d")`` or ``modeChanged("top")``.
    """
    modeChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None, initial: str = "3d") -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._3d_btn = QPushButton("3D", self)
        self._top_btn = QPushButton("Top", self)
        for btn in (self._3d_btn, self._top_btn):
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setFixedHeight(28)
            btn.setMinimumWidth(54)
            btn.setCursor(Qt.PointingHandCursor)
        # Joined-pill styling: rounded outer corners, square inner edges,
        # active button highlighted in accent amber.
        self._3d_btn.setStyleSheet(self._stylesheet(side="left"))
        self._top_btn.setStyleSheet(self._stylesheet(side="right"))
        layout.addWidget(self._3d_btn)
        layout.addWidget(self._top_btn)
        self._3d_btn.clicked.connect(lambda: self.set_mode("3d"))
        self._top_btn.clicked.connect(lambda: self.set_mode("top"))
        self.set_mode(initial, _emit=False)
        self.adjustSize()

    @staticmethod
    def _stylesheet(*, side: str) -> str:
        # Square the corner that joins the other button.
        if side == "left":
            radius = "border-top-left-radius:6px; border-bottom-left-radius:6px;"
            border = "border-right: none;"
        else:
            radius = "border-top-right-radius:6px; border-bottom-right-radius:6px;"
            border = ""
        return (
            "QPushButton {"
            "  background: rgba(38, 42, 49, 220);"
            "  color: #c9ced6;"
            "  border: 1px solid #3a3f47;"
            f"  {border}"
            f"  {radius}"
            "  font-size: 12px;"
            "  font-weight: 600;"
            "  padding: 0 12px;"
            "}"
            "QPushButton:hover { color: #f5a623; }"
            "QPushButton:checked {"
            "  background: rgba(245, 166, 35, 220);"
            "  color: #1d1f24;"
            "  border-color: #f5a623;"
            "}"
        )

    def mode(self) -> str:
        return "3d" if self._3d_btn.isChecked() else "top"

    def set_mode(self, mode: str, *, _emit: bool = True) -> None:
        if mode == "3d":
            self._3d_btn.setChecked(True)
            self._top_btn.setChecked(False)
        else:
            self._3d_btn.setChecked(False)
            self._top_btn.setChecked(True)
        if _emit:
            self.modeChanged.emit(mode)

    def position_top_right(
        self, host_width: int, host_height: int, margin: int = 12,
    ) -> None:
        """Anchor to the host's top-right corner. The zoom toolbar should
        use ``top_offset = self.height() + 6`` so they stack cleanly."""
        self.adjustSize()
        self.move(host_width - self.width() - margin, margin)
