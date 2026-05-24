"""Recovery wizard for re-establishing X/Y/Z work zero after a USB
drop, e-stop, or power blip.

Three approaches the operator can pick between, depending on what their
machine + stock setup supports:

1. **Homing + restore WCO** — for machines with limit switches (best
   accuracy). Soft-resets, unlocks, runs ``$H``, then re-applies the
   saved work-coordinate offset from the checkpoint via G10 L20.
   Sub-0.05 mm reproducibility, no manual jogging needed.

2. **Witness-mark divot** — uses the small recovery divot the program
   cut at start (when ``witness_mark_enabled`` is on). Operator jogs
   the bit-tip into the divot, the wizard applies the divot's known
   work coords via G10 L20 P1 to restore work zero.

3. **Design feature** — operator picks a known design point (e.g. the
   centre, a bbox corner) by typing its coordinates, jogs the bit-tip
   onto that physical spot on the work, and the wizard sets the work
   zero so the current physical position maps to that design point.

All three end with a single ``G10 L20 P1 X<x> Y<y> [Z<z>]`` line that
sets the GRBL work-coordinate system back to the original frame.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class RecoveryDialog(QDialog):
    """A tabbed wizard with the three recovery approaches.

    Emits the GRBL command line(s) needed for the chosen recovery via
    ``sendGrblCommand`` so MainWindow can route them through the live
    machine controller. Stays modeless-ish (modal QDialog) so the user
    can also jog from the main window between wizard steps if needed.
    """

    # Each emit is a single raw G-code line that goes straight to GRBL.
    sendGrblCommand = Signal(str)

    def __init__(
        self,
        *,
        checkpoint_wco: tuple[float, float, float] | None,
        witness_xy_mm: tuple[float, float] | None,
        witness_depth_mm: float,
        pocket_witness_xy_mm: tuple[float, float] | None,
        plug_witness_xy_mm: tuple[float, float] | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cp_wco = checkpoint_wco
        self._pocket_witness = pocket_witness_xy_mm
        self._plug_witness = plug_witness_xy_mm
        self._witness_depth = witness_depth_mm

        self.setWindowTitle("Recover work zero")
        self.setMinimumWidth(560)
        self.setMinimumHeight(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        intro = QLabel(
            "<span style='color:#9aa0a6'>USB drops, e-stops, and power "
            "blips happen. Each tab below is a different way to put the "
            "machine's work-coordinate zero back exactly where it was so "
            "you can resume the cut without ruining the piece. Pick the "
            "one that fits your machine setup.</span>"
        )
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_homing_tab(), "1. Homing ($H)")
        self._tabs.addTab(
            self._build_witness_tab(), "2. Witness mark"
        )
        self._tabs.addTab(
            self._build_feature_tab(), "3. Design feature"
        )
        outer.addWidget(self._tabs, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        outer.addLayout(row)

    # -------------------------------------------------------- homing tab
    def _build_homing_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        info = QLabel(
            "<b>For machines with limit switches.</b><br>"
            "<span style='color:#9aa0a6'>Best accuracy — fully automatic, "
            "sub-0.05 mm repeatable. Requires homing enabled in GRBL "
            "(<code>$22=1</code>) and limit switches on X, Y, Z. The "
            "wizard soft-resets, unlocks, homes, then re-applies the "
            "work-coordinate offset saved during the cut.</span>"
        )
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        layout.addWidget(info)

        if self._cp_wco is None:
            warn = QLabel(
                "<span style='color:#f5a623'>⚠ No saved work-coordinate "
                "offset was found from the most recent cut — homing will "
                "leave you with the GRBL default zero. Start a Preview-"
                "based cut so the next failure can recover via this path.</span>"
            )
            warn.setTextFormat(Qt.RichText)
            warn.setWordWrap(True)
            layout.addWidget(warn)
        else:
            x, y, z = self._cp_wco
            saved = QLabel(
                f"<span style='color:#3eea7a'>✓ Saved WCO from the most "
                f"recent run: <b>X {x:+.3f}  Y {y:+.3f}  Z {z:+.3f}</b> mm."
                f"</span>"
            )
            saved.setTextFormat(Qt.RichText)
            layout.addWidget(saved)

        plan = QLabel(
            "<ol style='line-height:150%'>"
            "<li>Send <code>\\x18</code> — soft reset.</li>"
            "<li>Send <code>$X</code> — unlock the alarm.</li>"
            "<li>Send <code>$H</code> — home all axes (Lunyee will run "
            "each axis to its limit switch).</li>"
            "<li>Send <code>G10 L20 P1 X&lt;mx-wco_x&gt; Y&lt;…&gt; Z&lt;…&gt;"
            "</code> — re-apply the saved WCO from the checkpoint.</li>"
            "</ol>"
        )
        plan.setTextFormat(Qt.RichText)
        plan.setWordWrap(True)
        layout.addWidget(plan)
        layout.addStretch(1)

        btn_row = QHBoxLayout()
        run_btn = QPushButton("▶ Run homing + restore WCO")
        run_btn.setStyleSheet(
            "QPushButton { background:#f5a623; color:#15171b; "
            "font-weight:700; padding:8px 16px; }"
        )
        run_btn.setEnabled(self._cp_wco is not None)
        run_btn.clicked.connect(self._on_run_homing)
        btn_row.addStretch(1)
        btn_row.addWidget(run_btn)
        layout.addLayout(btn_row)
        return w

    def _on_run_homing(self) -> None:
        # Soft reset → unlock → home. The G10 follows in _restore_wco_after_home
        # which we fire by emitting the queued commands; here we just blast
        # the sequence — GRBL queues and acks them in order. Note: $H is
        # synchronous on GRBL and takes several seconds; subsequent G10 will
        # apply AFTER homing completes.
        self.sendGrblCommand.emit("\x18")
        self.sendGrblCommand.emit("$X")
        self.sendGrblCommand.emit("$H")
        if self._cp_wco is not None:
            # After homing, MPos is at the homed position (Lunyee homes to
            # the +X/+Y/+Z corner, MPos roughly (0, 0, 0) or (max, max, 0)
            # depending on $23). The most robust restore is to set the
            # work coords directly: G10 L20 P1 says "current MPos IS this
            # WPos", so by setting WPos to (current_mpos - saved_wco) we
            # restore WCO = saved_wco. We don't know current_mpos here
            # without a fresh status report — so instead emit the simpler
            # form that GRBL accepts: G10 L2 P1 sets the WCO directly.
            wx, wy, wz = self._cp_wco
            self.sendGrblCommand.emit(
                f"G10 L2 P1 X{wx:.4f} Y{wy:.4f} Z{wz:.4f}"
            )

    # ------------------------------------------------------ witness tab
    def _build_witness_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        info = QLabel(
            "<b>Use the witness-mark divot cut at the start of the program.</b>"
            "<br><span style='color:#9aa0a6'>The program cuts a small "
            "divot in stock waste BEFORE the design so the divot's "
            "work-coordinate location is known. Jog the bit-tip into "
            "the centre of that divot (Z down to just touch the bottom), "
            "then click Restore — the wizard applies G10 L20 to make "
            "the current position equal the divot's known XYZ. Sub-0.1 mm "
            "with care.</span>"
        )
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self._wit_side = QComboBox()
        if self._pocket_witness is not None:
            x, y = self._pocket_witness
            self._wit_side.addItem(
                f"Pocket — divot at  X {x:+.3f}, Y {y:+.3f} mm",
                ("pocket", self._pocket_witness),
            )
        if self._plug_witness is not None:
            x, y = self._plug_witness
            self._wit_side.addItem(
                f"Plug — divot at  X {x:+.3f}, Y {y:+.3f} mm",
                ("plug", self._plug_witness),
            )
        if self._wit_side.count() == 0:
            self._wit_side.addItem("(no witness-mark coordinates available)", None)
        form.addRow("Which side were you cutting?", self._wit_side)

        self._wit_zero_z = QCheckBox(
            "Also zero Z (the divot floor is the witness depth below the surface)"
        )
        self._wit_zero_z.setChecked(True)
        form.addRow(self._wit_zero_z)
        layout.addLayout(form)
        layout.addStretch(1)

        btn_row = QHBoxLayout()
        run_btn = QPushButton("▶ Restore from witness divot")
        run_btn.setStyleSheet(
            "QPushButton { background:#f5a623; color:#15171b; "
            "font-weight:700; padding:8px 16px; }"
        )
        run_btn.setEnabled(
            self._pocket_witness is not None or self._plug_witness is not None
        )
        run_btn.clicked.connect(self._on_run_witness)
        btn_row.addStretch(1)
        btn_row.addWidget(run_btn)
        layout.addLayout(btn_row)
        return w

    def _on_run_witness(self) -> None:
        data = self._wit_side.currentData()
        if data is None:
            return
        _side, (wx, wy) = data
        if self._wit_zero_z.isChecked():
            # Bit-tip is in the divot floor, which is witness_depth below the
            # work surface (Z=0). So the bit's current physical Z = -depth in
            # work coords. G10 L20 P1 Z<-depth> tells GRBL "current MPos is
            # Z = -depth in work coords" → restores Z work zero to the
            # original surface.
            wz = -self._witness_depth
            self.sendGrblCommand.emit(
                f"G21 G10 L20 P1 X{wx:.4f} Y{wy:.4f} Z{wz:.4f}"
            )
        else:
            self.sendGrblCommand.emit(
                f"G21 G10 L20 P1 X{wx:.4f} Y{wy:.4f}"
            )

    # ------------------------------------------------------ feature tab
    def _build_feature_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        info = QLabel(
            "<b>For when there's no witness mark and you don't have "
            "limit switches.</b><br>"
            "<span style='color:#9aa0a6'>Pick a point in the design "
            "whose physical location on the partly-cut workpiece you can "
            "see clearly (a corner of the pocket, the design centre, a "
            "letter intersection). Type that point's design coordinates "
            "below, jog the bit-tip onto that exact spot, then click "
            "Restore. The wizard sets work zero so this position maps "
            "to those coords. ~0.1–0.2 mm depending on how accurately "
            "you can pin the feature visually.</span>"
        )
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()

        def _spin(default: float) -> QDoubleSpinBox:
            sp = QDoubleSpinBox()
            sp.setRange(-1000.0, 1000.0)
            sp.setDecimals(3)
            sp.setSingleStep(0.1)
            sp.setSuffix(" mm")
            sp.setValue(default)
            return sp

        self._feat_x = _spin(0.0)
        self._feat_y = _spin(0.0)
        form.addRow("Design X of the bit-tip:", self._feat_x)
        form.addRow("Design Y of the bit-tip:", self._feat_y)

        self._feat_zero_z = QCheckBox(
            "Also set Z = 0 here (bit-tip is at the work surface)"
        )
        self._feat_zero_z.setChecked(False)
        form.addRow(self._feat_zero_z)
        layout.addLayout(form)

        hint = QLabel(
            "<span style='color:#9aa0a6'>Common picks:<br>"
            "<b>(0, 0)</b> — the centre of the design (always the work "
            "origin in the centred-origin mode).<br>"
            "<b>Bounding-box corner</b> — e.g. for a 200×73 mm design "
            "the top-right corner is (+100, +36.5).</span>"
        )
        hint.setTextFormat(Qt.RichText)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)

        btn_row = QHBoxLayout()
        run_btn = QPushButton("▶ Set zero from this design point")
        run_btn.setStyleSheet(
            "QPushButton { background:#f5a623; color:#15171b; "
            "font-weight:700; padding:8px 16px; }"
        )
        run_btn.clicked.connect(self._on_run_feature)
        btn_row.addStretch(1)
        btn_row.addWidget(run_btn)
        layout.addLayout(btn_row)
        return w

    def _on_run_feature(self) -> None:
        x = float(self._feat_x.value())
        y = float(self._feat_y.value())
        if self._feat_zero_z.isChecked():
            self.sendGrblCommand.emit(
                f"G21 G10 L20 P1 X{x:.4f} Y{y:.4f} Z0.0000"
            )
        else:
            self.sendGrblCommand.emit(
                f"G21 G10 L20 P1 X{x:.4f} Y{y:.4f}"
            )
