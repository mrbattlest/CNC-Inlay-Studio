"""Modeless tool-change dialog that appears when the streaming program hits
an ``M0`` programmed-pause for a bit swap.

Stays on top so the operator can read the steps without hunting for the
status banner, but is non-modal so the bottom-dock jog controls remain
usable for navigating to uncut wood and re-zeroing Z.

The dialog provides three direct shortcuts:
  - **Set Z = 0** — proxies to the same confirmation flow as
    Settings → Work zero → Set Z = 0…
  - **Resume** — sends the GRBL cycle-start byte to leave the M0 hold and
    continue with the next bit's spindle-on / plunge.
  - **Open Setup Guide** — for users who want the full pre-cut walkthrough.

Closing the dialog without resuming is a no-op (the cut stays paused).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class ToolChangeDialog(QDialog):
    """Floating instructions panel shown when the cut pauses for a bit swap."""

    resumeRequested = Signal()
    setZeroZRequested = Signal()
    openSetupGuideRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tool change")
        # Tool window — stays on top of the main window but doesn't grab focus.
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        self.setModal(False)
        self.setMinimumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        # Big yellow header — bit name appears here, set via .show_for_bit().
        self._header = QLabel("Install: —")
        head_font = QFont()
        head_font.setPointSize(15)
        head_font.setBold(True)
        self._header.setFont(head_font)
        self._header.setStyleSheet("color: #f5a623;")
        outer.addWidget(self._header)

        intro = QLabel(
            "<span style='color:#9aa0a6'>The previous bit has finished. "
            "Swap bits and re-zero <b>Z only</b> before resuming. The bit "
            "has been parked over uncut wood so you can re-zero against a "
            "real surface.</span>"
        )
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # Numbered steps. Plain HTML — keeps it readable + easy to update.
        steps_html = (
            "<ol style='margin-left:14px; line-height:160%; "
            "color:#d8dce2; font-size:12px;'>"
            "<li>Loosen the spindle collet, remove the previous bit, install "
            "the new one. Push it fully into the collet and tighten firmly.</li>"
            "<li>Use the <b>Jog</b> controls in the Machine panel below "
            "(now re-enabled while paused):"
            "<ul style='margin-left:14px; margin-top:2px;'>"
            "<li>Click <b>Z+</b> a few times to lift the bit clear of any "
            "obstacle.</li>"
            "<li>If the parked spot looks fine for re-zeroing, skip jogging "
            "X/Y. Otherwise click X+/X−/Y+/Y− to navigate to UNCUT wood.</li>"
            "<li>Click <b>Z−</b> in 1 mm steps to approach the surface, then "
            "switch to 0.1 mm steps. Slide a sheet of paper between the bit "
            "and the wood; jog down until the paper just barely drags.</li>"
            "</ul></li>"
            "<li>Click <b>Set Z = 0</b> below — this resets <b>only Z</b>. "
            "<b>Do not</b> change XY zero — those references are still "
            "correct from the start of the job.</li>"
            "<li>Click <b>Z+</b> a few times to lift the bit before resume.</li>"
            "<li>Click <b>▶ Resume cut</b> below.</li>"
            "</ol>"
        )
        steps = QLabel(steps_html)
        steps.setTextFormat(Qt.RichText)
        steps.setWordWrap(True)
        outer.addWidget(steps)

        # Action buttons — directly trigger the same flows as the menu/toolbar.
        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        bb = QDialogButtonBox()
        self._z_btn = bb.addButton("Set Z = 0", QDialogButtonBox.ActionRole)
        self._z_btn.setToolTip("Re-zero Z to the current bit-tip position")
        self._z_btn.clicked.connect(self.setZeroZRequested.emit)

        self._guide_btn = bb.addButton("Open Setup Guide", QDialogButtonBox.HelpRole)
        self._guide_btn.clicked.connect(self.openSetupGuideRequested.emit)

        self._resume_btn = bb.addButton("▶ Resume cut", QDialogButtonBox.AcceptRole)
        self._resume_btn.setStyleSheet(
            "QPushButton { background:#3eea7a; color:#15171b; "
            "font-weight:700; padding:6px 14px; }"
        )
        self._resume_btn.clicked.connect(self._on_resume_clicked)
        button_row.addWidget(bb, 1)
        outer.addLayout(button_row)

    def show_for_bit(self, bit_name: str) -> None:
        """Update the header for the bit about to be installed and pop the
        dialog into view (raised to top of the window stack)."""
        self._header.setText(f"Install: {bit_name}")
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_resume_clicked(self) -> None:
        self.resumeRequested.emit()
        # Close ourselves on resume so the dialog doesn't linger across runs.
        self.hide()
