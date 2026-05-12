"""Coloured scrub bar — replaces the simple stream progress bar.

Each bit in a multi-bit program gets its own band of colour matching the
bit's preview-canvas tint. A draggable handle lets the operator pick any
line in the program to resume / start from; clicking close to a bit
boundary snaps to it because that's almost always what's wanted (skip
the roughing pass, start at the finishing bit, etc.).

The widget is purely a UI component — it tracks scrub state and emits
signals; the host (ControlPanel / MainWindow) is responsible for pushing
the actual stream progress in and for honouring the run-from-cursor
request.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


# Snap radius in pixels — clicking this close to a bit boundary tick
# snaps the scrub handle to that exact line index.
_SNAP_PX = 8


@dataclass
class BitSegment:
    """One bit's contiguous range of G-code line indices."""
    start_line: int
    end_line: int   # exclusive
    bit_name: str
    color: QColor


class ScrubBar(QWidget):
    """Coloured-segment scrubber for a multi-bit G-code program."""

    # Emitted continuously while the user drags. ``line`` is the current
    # scrub-handle position. Use this to update a live preview (e.g. 3D
    # canvas position marker).
    scrubMoved = Signal(int)
    # Emitted when the user releases on a position; useful for distinguishing
    # the final pick from the drag-noise feed.
    scrubCommitted = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setMouseTracking(True)
        self._total_lines: int = 0
        self._segments: list[BitSegment] = []
        # Live progress — how far the actual machine stream has gotten.
        self._progress_line: int = 0
        # Where the scrub handle currently sits.
        self._scrub_line: int = 0
        self._dragging: bool = False
        self._enabled_program: bool = False
        self.setToolTip(
            "Drag the amber handle to choose where to start a cut.\n"
            "Click near a bit-boundary tick to snap to that bit change."
        )

    # ---------------------------------------------------------- public API

    def set_program(
        self, total_lines: int, segments: list[BitSegment]
    ) -> None:
        """Install a new program. Resets scrub + progress to 0."""
        self._total_lines = max(0, total_lines)
        self._segments = list(segments)
        self._scrub_line = 0
        self._progress_line = 0
        self._enabled_program = total_lines > 0
        self.update()

    def clear(self) -> None:
        self.set_program(0, [])

    def set_progress(self, line: int) -> None:
        """Update the live machine-progress fill. Pulled forward to match
        the stream — the scrub handle never sits behind the live cursor
        because that would be a no-op.

        We deliberately do NOT emit ``scrubMoved`` from here. That signal
        feeds the scrub-preview path which overwrites the canvas's live
        machine-position cone with the *programmed* toolpath XY at the
        cursor line. Streaming fires this method ~66 times a second; the
        cursor is the latest line SENT, which is ahead of what GRBL is
        actually cutting (planner + 128-byte RX buffer lag), so the cone
        would visibly flicker between the toolpath-ahead position and
        the real WPos arriving in GRBL status reports at ~5 Hz. Progress
        is internal state — ``scrubMoved`` is for user-drag intent only.
        """
        self._progress_line = max(0, min(line, self._total_lines))
        if self._scrub_line < self._progress_line:
            self._scrub_line = self._progress_line
        self.update()

    def set_scrub_line(self, line: int) -> None:
        """Programmatic scrub move (e.g. from a 'snap to next bit' button)."""
        line = max(self._progress_line, min(line, self._total_lines))
        if line != self._scrub_line:
            self._scrub_line = line
            self.scrubMoved.emit(line)
            self.update()

    @property
    def scrub_line(self) -> int:
        return self._scrub_line

    @property
    def total_lines(self) -> int:
        return self._total_lines

    def segment_for_line(self, line: int) -> BitSegment | None:
        for seg in self._segments:
            if seg.start_line <= line < seg.end_line:
                return seg
        return self._segments[-1] if self._segments else None

    def next_bit_boundary(self, after_line: int) -> int | None:
        """Return the next bit-segment start line after ``after_line``."""
        for seg in self._segments[1:]:  # skip the very first segment
            if seg.start_line > after_line:
                return seg.start_line
        return None

    # --------------------------------------------------------------- paint

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        # Background.
        p.fillRect(self.rect(), QColor("#1d2129"))
        if not self._enabled_program:
            p.setPen(QColor("#9aa0a6"))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Click Preview Toolpaths to enable the scrub bar")
            p.end()
            return

        # Bar geometry.
        bar_top = 8
        bar_h = 14
        side_pad = 10
        bar_x = side_pad
        bar_w = self.width() - 2 * side_pad
        if bar_w <= 0 or self._total_lines <= 0:
            p.end()
            return
        scale = bar_w / self._total_lines

        # Coloured per-bit bands.
        for seg in self._segments:
            seg_x = bar_x + seg.start_line * scale
            seg_w = max(1.0, (seg.end_line - seg.start_line) * scale)
            p.fillRect(QRectF(seg_x, bar_top, seg_w, bar_h), seg.color)

        # Live-progress dim overlay over the streamed portion.
        if self._progress_line > 0:
            prog_w = self._progress_line * scale
            p.fillRect(
                QRectF(bar_x, bar_top, prog_w, bar_h),
                QColor(0, 0, 0, 110),
            )

        # Bit-boundary tick marks.
        p.setPen(QPen(QColor("#e8e8ea"), 1))
        for seg in self._segments[1:]:
            tx = bar_x + seg.start_line * scale
            p.drawLine(
                QPointF(tx, bar_top - 3),
                QPointF(tx, bar_top + bar_h + 3),
            )

        # Outline.
        p.setPen(QPen(QColor("#3a3f47"), 1))
        p.drawRect(QRectF(bar_x, bar_top, bar_w, bar_h))

        # Scrub handle — vertical amber line + arrow underneath.
        sx = bar_x + self._scrub_line * scale
        p.setPen(QPen(QColor("#f5a623"), 2))
        p.drawLine(QPointF(sx, bar_top - 5), QPointF(sx, bar_top + bar_h + 5))
        p.setBrush(QColor("#f5a623"))
        triangle = QPolygonF([
            QPointF(sx - 5, bar_top + bar_h + 5),
            QPointF(sx + 5, bar_top + bar_h + 5),
            QPointF(sx, bar_top + bar_h + 11),
        ])
        p.drawPolygon(triangle)
        p.end()

    # ---------------------------------------------------------- mouse i/o

    def _line_for_x(self, x: float) -> int:
        side_pad = 10
        bar_x = side_pad
        bar_w = self.width() - 2 * side_pad
        if bar_w <= 0 or self._total_lines <= 0:
            return 0
        scale = bar_w / self._total_lines
        # Snap to nearest bit boundary when within snap radius.
        for seg in self._segments[1:]:
            tx = bar_x + seg.start_line * scale
            if abs(tx - x) <= _SNAP_PX:
                return seg.start_line
        rel = (x - bar_x) / bar_w
        rel = max(0.0, min(1.0, rel))
        return int(round(rel * self._total_lines))

    def _set_scrub_to_x(self, x: float) -> None:
        line = self._line_for_x(x)
        # Never let the user scrub behind live progress.
        line = max(self._progress_line, line)
        if line != self._scrub_line:
            self._scrub_line = line
            self.scrubMoved.emit(line)
            self.update()

    def mousePressEvent(self, event) -> None:
        if not self._enabled_program or event.button() != Qt.LeftButton:
            return
        self._dragging = True
        self._set_scrub_to_x(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._set_scrub_to_x(event.position().x())

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False
            self.scrubCommitted.emit(self._scrub_line)
