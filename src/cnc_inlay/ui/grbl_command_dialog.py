"""Two GRBL helper dialogs that take memorisation out of the operator's hands.

* ``SendCommandDialog`` — picks a common command from a dropdown (M3 spindle,
  M5 stop, $X unlock, $H home, etc.) and surfaces only the parameter fields
  that command needs (e.g. RPM for M3). Builds the final G-code line and
  emits it via ``commandRequested``.

* ``SetCustomZeroDialog`` — three spinboxes (X, Y, Z, 3-decimal precision)
  for setting the WCS-1 origin to arbitrary values rather than zero. Any
  axis the user leaves unchecked is omitted from the G10 L20 P1 command,
  so a Z-only re-zero doesn't disturb XY (and vice versa).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cnc_inlay.control.grbl import cmd_set_work_custom


# --- Send Command catalog ----------------------------------------------------

@dataclass
class _CommandTemplate:
    """A picker entry: human label + how to build the actual G-code line.

    ``builder`` returns the literal command string. It receives the dialog's
    parameter widgets so it can read RPM / coords / dwell time from them.
    Templates that don't need parameters set ``param_kind = "none"``.
    """
    label: str           # what shows in the dropdown
    description: str     # short help line under the dropdown
    param_kind: str      # "none", "rpm", "seconds", "xyz", "raw"
    builder: Callable[[dict], str]
    # When True, the command is destructive enough to warrant a small
    # confirmation gate before it goes to GRBL.
    confirm: bool = False


_COMMANDS: list[_CommandTemplate] = [
    # Spindle
    _CommandTemplate(
        label="M3 — Spindle ON (clockwise) at RPM…",
        description="Start the spindle in the normal cutting direction. Pick the RPM below.",
        param_kind="rpm",
        builder=lambda p: f"M3 S{p['rpm']}",
    ),
    _CommandTemplate(
        label="M4 — Spindle ON (counter-clockwise) at RPM…",
        description="Reverse spindle direction — rare, mostly for left-handed bits.",
        param_kind="rpm",
        builder=lambda p: f"M4 S{p['rpm']}",
    ),
    _CommandTemplate(
        label="M5 — Spindle OFF",
        description="Stops the spindle. Sends regardless of GRBL state.",
        param_kind="none",
        builder=lambda p: "M5",
    ),
    # Coolant (Lunyee usually has no coolant, but harmless to include)
    _CommandTemplate(
        label="M9 — Coolant OFF",
        description="Turn off any coolant outputs. Safe even when no coolant is wired.",
        param_kind="none",
        builder=lambda p: "M9",
    ),
    # GRBL state recovery
    _CommandTemplate(
        label="$X — Unlock from Alarm",
        description=(
            "Clears GRBL's Alarm state after a Soft Reset, hard limit hit, "
            "or homing failure. Required before any new motion command."
        ),
        param_kind="none",
        builder=lambda p: "$X",
    ),
    _CommandTemplate(
        label="$H — Home (run homing cycle)",
        description=(
            "Runs the homing cycle to find machine zero. Requires limit "
            "switches AND $22=1 in GRBL settings — will error otherwise."
        ),
        param_kind="none",
        builder=lambda p: "$H",
        confirm=True,
    ),
    # Inspection
    _CommandTemplate(
        label="$$ — Show GRBL settings",
        description="Dumps every $-setting to the response panel for inspection.",
        param_kind="none",
        builder=lambda p: "$$",
    ),
    _CommandTemplate(
        label="$# — Show work-coordinate offsets",
        description="Prints the active G54..G59 offsets, plus G92 / TLO state.",
        param_kind="none",
        builder=lambda p: "$#",
    ),
    _CommandTemplate(
        label="$G — Show parser modal state",
        description="Reports current motion mode, units, plane, etc.",
        param_kind="none",
        builder=lambda p: "$G",
    ),
    _CommandTemplate(
        label="$I — Show build / version info",
        description="GRBL build version + compile-time options.",
        param_kind="none",
        builder=lambda p: "$I",
    ),
    # Pause + dwell
    _CommandTemplate(
        label="M0 — Programmed Pause",
        description="Halts the program and waits for a Cycle Start (~) to resume.",
        param_kind="none",
        builder=lambda p: "M0",
    ),
    _CommandTemplate(
        label="G4 — Dwell (wait N seconds)…",
        description=(
            "Pauses motion for the specified number of seconds. Useful before "
            "the first plunge to let the spindle ramp up."
        ),
        param_kind="seconds",
        builder=lambda p: f"G4 P{p['seconds']:g}",
    ),
    # Motion
    _CommandTemplate(
        label="G0 — Rapid move to absolute X / Y / Z…",
        description=(
            "Non-cutting traverse to the given work-coordinate position. "
            "ALWAYS at the machine's max rapid feed — verify the path is clear."
        ),
        param_kind="xyz",
        builder=lambda p: "G90 G21 G0 " + _xyz_words(p),
        confirm=True,
    ),
    _CommandTemplate(
        label="G1 — Linear cut to absolute X / Y / Z at feed…",
        description=(
            "Cutting move to the given work-coordinate position at the "
            "specified feed (mm/min)."
        ),
        param_kind="xyz_feed",
        builder=lambda p: f"G90 G21 G1 {_xyz_words(p)} F{p['feed']:g}",
        confirm=True,
    ),
    # Coordinate system
    _CommandTemplate(
        label="G54 — Switch to Work Coordinate 1 (default)",
        description="Activates WCS-1 — the only coordinate system this app uses.",
        param_kind="none",
        builder=lambda p: "G54",
    ),
    # Free text — escape hatch
    _CommandTemplate(
        label="(Custom raw command…)",
        description=(
            "Type anything here. Use only if you know exactly what GRBL "
            "should do with it — no safety guards apply."
        ),
        param_kind="raw",
        builder=lambda p: p["raw"].strip(),
    ),
]


def _xyz_words(p: dict) -> str:
    """Build the X/Y/Z portion of a motion command from the param dict.

    Each axis is included only when its checkbox is ticked, so a user who
    only wants to move Z doesn't accidentally rapid X to zero.
    """
    parts: list[str] = []
    if p.get("x_use"):
        parts.append(f"X{p['x']:.3f}")
    if p.get("y_use"):
        parts.append(f"Y{p['y']:.3f}")
    if p.get("z_use"):
        parts.append(f"Z{p['z']:.3f}")
    return " ".join(parts)


class SendCommandDialog(QDialog):
    """Dropdown-driven GRBL command builder. Emits the final G-code string
    via ``commandRequested`` when the user clicks Send."""

    commandRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Send GRBL command")
        self.setMinimumWidth(520)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)

        outer.addWidget(QLabel("<b>Pick a command:</b>"))
        self._combo = QComboBox()
        for tmpl in _COMMANDS:
            self._combo.addItem(tmpl.label, tmpl)
        self._combo.currentIndexChanged.connect(self._on_command_changed)
        outer.addWidget(self._combo)

        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        outer.addWidget(self._desc_label)

        # Parameter container — children swapped per command.
        self._param_container = QWidget()
        self._param_layout = QFormLayout(self._param_container)
        self._param_layout.setContentsMargins(0, 8, 0, 4)
        outer.addWidget(self._param_container)

        # Live preview of the literal G-code that will go to GRBL.
        outer.addWidget(QLabel("<b>Will send:</b>"))
        self._preview = QLabel("")
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        self._preview.setFont(mono)
        self._preview.setStyleSheet(
            "background:#1d2129; color:#f5a623; padding:8px 10px; "
            "border:1px solid #3a3f47; border-radius:4px;"
        )
        self._preview.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        outer.addWidget(self._preview)

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Cancel
        )
        self._send_btn = btn_box.addButton("Send", QDialogButtonBox.AcceptRole)
        self._send_btn.setStyleSheet(
            "QPushButton { background:#f5a623; color:#1d1f24; "
            "font-weight:600; padding:6px 18px; border-radius:5px; }"
            "QPushButton:hover { background:#ffb945; }"
        )
        self._send_btn.clicked.connect(self._on_send_clicked)
        btn_box.rejected.connect(self.reject)
        outer.addWidget(btn_box)

        # Per-command parameter widgets — held strongly so they survive
        # being swapped out of the form layout during _on_command_changed.
        self._param_widgets: dict[str, QWidget] = {}
        self._on_command_changed(0)

    # ------------------------------------------------------- internals

    def _clear_param_layout(self) -> None:
        while self._param_layout.count():
            item = self._param_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._param_widgets = {}

    def _on_command_changed(self, _idx: int) -> None:
        tmpl: _CommandTemplate = self._combo.currentData()
        self._desc_label.setText(tmpl.description)
        self._clear_param_layout()
        if tmpl.param_kind == "rpm":
            sb = QSpinBox()
            sb.setRange(1000, 24000)
            sb.setSingleStep(500)
            sb.setValue(10000)
            sb.setSuffix(" RPM")
            sb.valueChanged.connect(self._refresh_preview)
            self._param_widgets["rpm"] = sb
            self._param_layout.addRow("Spindle speed:", sb)
        elif tmpl.param_kind == "seconds":
            sb = QDoubleSpinBox()
            sb.setRange(0.1, 60.0)
            sb.setDecimals(1)
            sb.setSingleStep(0.5)
            sb.setValue(3.0)
            sb.setSuffix(" s")
            sb.valueChanged.connect(self._refresh_preview)
            self._param_widgets["seconds"] = sb
            self._param_layout.addRow("Dwell time:", sb)
        elif tmpl.param_kind in ("xyz", "xyz_feed"):
            for axis in ("x", "y", "z"):
                row = QWidget()
                rh = QHBoxLayout(row)
                rh.setContentsMargins(0, 0, 0, 0)
                cb = QCheckBox(f"Move {axis.upper()}")
                cb.setChecked(False)
                sb = QDoubleSpinBox()
                sb.setRange(-1000.0, 1000.0)
                sb.setDecimals(3)
                sb.setSingleStep(0.5)
                sb.setSuffix(" mm")
                sb.setEnabled(False)
                cb.toggled.connect(sb.setEnabled)
                cb.toggled.connect(self._refresh_preview)
                sb.valueChanged.connect(self._refresh_preview)
                rh.addWidget(cb)
                rh.addWidget(sb, 1)
                self._param_widgets[f"{axis}_use"] = cb
                self._param_widgets[axis] = sb
                self._param_layout.addRow("", row)
            if tmpl.param_kind == "xyz_feed":
                feed = QSpinBox()
                feed.setRange(50, 5000)
                feed.setSingleStep(50)
                feed.setValue(500)
                feed.setSuffix(" mm/min")
                feed.valueChanged.connect(self._refresh_preview)
                self._param_widgets["feed"] = feed
                self._param_layout.addRow("Feed rate:", feed)
        elif tmpl.param_kind == "raw":
            edit = QLineEdit()
            edit.setPlaceholderText("e.g. $102=400  /  G0 X10  /  $RST=#")
            edit.textChanged.connect(self._refresh_preview)
            self._param_widgets["raw"] = edit
            self._param_layout.addRow("Command:", edit)
        self._refresh_preview()

    def _params(self) -> dict:
        out: dict = {}
        for key, w in self._param_widgets.items():
            if isinstance(w, (QSpinBox, QDoubleSpinBox)):
                out[key] = w.value()
            elif isinstance(w, QCheckBox):
                out[key] = w.isChecked()
            elif isinstance(w, QLineEdit):
                out[key] = w.text()
        return out

    def _refresh_preview(self) -> None:
        tmpl: _CommandTemplate = self._combo.currentData()
        try:
            cmd = tmpl.builder(self._params()).strip()
        except Exception:
            cmd = ""
        if not cmd:
            self._preview.setText("(empty — fill in a parameter)")
            self._send_btn.setEnabled(False)
            return
        self._preview.setText(cmd)
        self._send_btn.setEnabled(True)

    def _on_send_clicked(self) -> None:
        tmpl: _CommandTemplate = self._combo.currentData()
        cmd = tmpl.builder(self._params()).strip()
        if not cmd:
            return
        if tmpl.confirm:
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Confirm command")
            box.setText(f"Send <b>{cmd}</b> to GRBL?")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Cancel)
            if box.exec() != QMessageBox.Yes:
                return
        self.commandRequested.emit(cmd)
        self.accept()


# ----------------------------------------------------------- Set Custom Zero

class SetCustomZeroDialog(QDialog):
    """Set WCS-1 to arbitrary X / Y / Z values rather than zero. Each axis
    is opt-in via a checkbox — leaving an axis unchecked means GRBL keeps
    its current offset on that axis."""

    commandRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set custom work zero")
        self.setMinimumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)

        intro = QLabel(
            "<b>Tell GRBL the bit's current position is at:</b>"
            "<br><span style='color:#9aa0a6'>"
            "Tick the axes you want to set. Unticked axes keep their "
            "current work-coordinate offset. Values are in millimetres "
            "(3 decimal places).</span>"
        )
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self._x_chk = QCheckBox("Set X")
        self._x_chk.setChecked(True)
        self._x_spin = self._mm_spin()
        self._y_chk = QCheckBox("Set Y")
        self._y_chk.setChecked(True)
        self._y_spin = self._mm_spin()
        self._z_chk = QCheckBox("Set Z")
        self._z_chk.setChecked(True)
        self._z_spin = self._mm_spin()
        for chk, spin, label in [
            (self._x_chk, self._x_spin, "X (mm):"),
            (self._y_chk, self._y_spin, "Y (mm):"),
            (self._z_chk, self._z_spin, "Z (mm):"),
        ]:
            row = QWidget()
            rh = QHBoxLayout(row)
            rh.setContentsMargins(0, 0, 0, 0)
            chk.toggled.connect(spin.setEnabled)
            chk.toggled.connect(self._refresh_preview)
            spin.valueChanged.connect(self._refresh_preview)
            rh.addWidget(chk)
            rh.addWidget(spin, 1)
            form.addRow(label, row)
        outer.addLayout(form)

        outer.addWidget(QLabel("<b>Will send:</b>"))
        self._preview = QLabel("")
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        self._preview.setFont(mono)
        self._preview.setStyleSheet(
            "background:#1d2129; color:#f5a623; padding:8px 10px; "
            "border:1px solid #3a3f47; border-radius:4px;"
        )
        outer.addWidget(self._preview)

        btn_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        self._send_btn = btn_box.addButton("Set zero", QDialogButtonBox.AcceptRole)
        self._send_btn.setStyleSheet(
            "QPushButton { background:#f5a623; color:#1d1f24; "
            "font-weight:600; padding:6px 18px; border-radius:5px; }"
            "QPushButton:hover { background:#ffb945; }"
        )
        self._send_btn.clicked.connect(self._on_send_clicked)
        btn_box.rejected.connect(self.reject)
        outer.addWidget(btn_box)

        self._refresh_preview()

    @staticmethod
    def _mm_spin() -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(-1000.0, 1000.0)
        sb.setDecimals(3)
        sb.setSingleStep(0.1)
        sb.setSuffix(" mm")
        sb.setValue(0.0)
        return sb

    def _build(self) -> str:
        return cmd_set_work_custom(
            x_mm=self._x_spin.value() if self._x_chk.isChecked() else None,
            y_mm=self._y_spin.value() if self._y_chk.isChecked() else None,
            z_mm=self._z_spin.value() if self._z_chk.isChecked() else None,
        )

    def _refresh_preview(self) -> None:
        cmd = self._build()
        if not cmd:
            self._preview.setText("(no axes selected)")
            self._send_btn.setEnabled(False)
        else:
            self._preview.setText(cmd)
            self._send_btn.setEnabled(True)

    def _on_send_clicked(self) -> None:
        cmd = self._build()
        if not cmd:
            return
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Confirm custom zero")
        box.setText("<b>Override the work-zero with these values?</b>")
        box.setInformativeText(
            f"This will send: <code>{cmd}</code><br><br>"
            "Re-zeroing mid-job will misalign every subsequent move."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Yes:
            return
        self.commandRequested.emit(cmd)
        self.accept()
