"""Surfacing dialog — plan and run a surfacing pass to remove plug backing.

Opens from Tools → Surface plug backing… The dialog gathers the bit
choice, panel dimensions, and depth-to-remove (auto-derived from the
project's glue gap with a safety margin), shows a summary of what
will happen, and either streams the resulting G-code to the connected
machine or saves it to a file for an external sender.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cnc_inlay.core.job import Bit
from cnc_inlay.core.pipeline import SurfacingPlan, compute_surfacing_plan


class SurfacingDialog(QDialog):
    """Pick bit + parameters, preview the plan, run / save."""

    runRequested = Signal(object, float)   # SurfacingPlan, combined_z_mm
    saveRequested = Signal(object)         # SurfacingPlan

    def __init__(
        self,
        parent: QWidget | None,
        *,
        surfacing_bits: list[Bit],
        default_panel_width_mm: float,
        default_panel_height_mm: float,
        default_glue_gap_mm: float,
        default_pocket_depth_mm: float,
        default_plug_thickness_mm: float,
        default_pocket_thickness_mm: float,
        machine_connected: bool,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Surface plug backing")
        self.setMinimumWidth(560)
        self.setMinimumHeight(680)

        self._surfacing_bits = list(surfacing_bits)
        self._machine_connected = machine_connected
        self._glue_gap_mm = default_glue_gap_mm
        self._pocket_depth_mm = default_pocket_depth_mm
        self._plug_thickness_mm = default_plug_thickness_mm
        self._pocket_thickness_mm = default_pocket_thickness_mm

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)

        intro = QLabel(
            "<b>Surfacing pass — plane the back of the assembled inlay.</b>"
            "<br><span style='color:#9aa0a6'>"
            "Run this AFTER the pocket and plug are glued together. The "
            "plug stands proud of the pocket-side wood by <i>glue gap</i> "
            "after assembly; surfacing removes that plus a small safety "
            "margin so the inlay sits flush.</span>"
        )
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        # --- Bit ---
        self._bit_combo = QComboBox()
        if not self._surfacing_bits:
            self._bit_combo.addItem("(none — open Settings → My Bits)", None)
            self._bit_combo.setEnabled(False)
        else:
            for b in self._surfacing_bits:
                self._bit_combo.addItem(b.name, b)
        self._bit_combo.currentIndexChanged.connect(self._refresh_summary)
        form.addRow("Surfacing bit:", self._bit_combo)

        # --- Panel dimensions ---
        # Width and height on separate labelled rows. Side-by-side in a
        # QFormLayout was getting clipped on macOS because the wrapping
        # QWidget didn't always respect the spinboxes' native height,
        # cutting off the up/down arrows. Two clean rows side-step that
        # and read more clearly anyway.
        self._w_spin = self._mm_spin(default_panel_width_mm, 10.0, 1000.0)
        self._w_spin.setMinimumHeight(28)
        self._w_spin.valueChanged.connect(self._refresh_summary)
        self._h_spin = self._mm_spin(default_panel_height_mm, 10.0, 1000.0)
        self._h_spin.setMinimumHeight(28)
        self._h_spin.valueChanged.connect(self._refresh_summary)
        form.addRow("Panel width:", self._w_spin)
        form.addRow("Panel height:", self._h_spin)

        # Combined Z-axis assembly thickness — auto-calculated as
        # pocket_thickness + glue_gap + plug_thickness. The 3D view
        # in the Surfacing tab uses this same value to size the wood
        # block. User can override (e.g. measured-on-the-bench) if
        # the calculated value disagrees with reality.
        combined = max(
            0.5,
            default_pocket_thickness_mm + default_glue_gap_mm + default_plug_thickness_mm,
        )
        self._z_spin = self._mm_spin(combined, 1.0, 200.0)
        self._z_spin.valueChanged.connect(self._refresh_summary)
        form.addRow("Combined Z thickness:", self._z_spin)
        z_hint = QLabel(
            f"Auto: pocket {default_pocket_thickness_mm:.2f} + glue gap "
            f"{default_glue_gap_mm:.2f} + plug {default_plug_thickness_mm:.2f} = "
            f"<b>{combined:.2f} mm</b>"
        )
        z_hint.setTextFormat(Qt.RichText)
        z_hint.setStyleSheet("color:#9aa0a6;")
        form.addRow("", z_hint)

        # --- Depth to remove (auto from glue_gap + safety margin) ---
        self._auto_depth_label = QLabel(
            f"Auto-calculated from glue gap "
            f"({default_glue_gap_mm:.2f} mm) + 0.20 mm Z safety margin"
        )
        self._auto_depth_label.setStyleSheet("color:#9aa0a6;")
        self._depth_spin = self._mm_spin(
            default_glue_gap_mm + 0.20, 0.05, 5.0
        )
        self._depth_spin.valueChanged.connect(self._refresh_summary)
        form.addRow("Depth to remove (mm):", self._depth_spin)
        form.addRow("", self._auto_depth_label)

        # --- Speeds (sane defaults; user can override) ---
        self._rpm_spin = QSpinBox()
        self._rpm_spin.setRange(5000, 24000)
        self._rpm_spin.setSingleStep(500)
        self._rpm_spin.setValue(10000)
        self._rpm_spin.setSuffix(" RPM")
        self._rpm_spin.valueChanged.connect(self._refresh_summary)
        form.addRow("Spindle:", self._rpm_spin)

        self._feed_spin = QSpinBox()
        self._feed_spin.setRange(100, 3000)
        self._feed_spin.setSingleStep(100)
        self._feed_spin.setValue(1000)
        self._feed_spin.setSuffix(" mm/min")
        self._feed_spin.valueChanged.connect(self._refresh_summary)
        form.addRow("Feed rate:", self._feed_spin)

        self._pass_depth_spin = self._mm_spin(0.4, 0.1, 1.0)
        self._pass_depth_spin.valueChanged.connect(self._refresh_summary)
        form.addRow("Pass depth (mm):", self._pass_depth_spin)

        outer.addLayout(form)

        # --- Summary ---
        self._summary = QLabel("")
        self._summary.setTextFormat(Qt.RichText)
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(
            "background:#1d2129; color:#e8e8ea; padding:10px; "
            "border:1px solid #3a3f47; border-radius:6px; font-size:12px;"
        )
        outer.addWidget(self._summary)

        # --- Safety reminder ---
        safety = QLabel(
            "<b style='color:#f5a623;'>⚠ Before running:</b><br>"
            "&nbsp;&nbsp;1. The glued panel is <b>clamped face-down</b> "
            "with the plug-backing side facing UP<br>"
            "&nbsp;&nbsp;2. The surfacing bit is installed and tightened<br>"
            "&nbsp;&nbsp;3. Z-zero is set on the <b>highest point</b> of the "
            "plug backing (use a paper feeler at the tallest spot)<br>"
            "&nbsp;&nbsp;4. The dust shroud is removed or set high — "
            "wide bits throw chips harder than 3 mm endmills<br>"
            "&nbsp;&nbsp;5. You can reach the e-stop"
        )
        safety.setTextFormat(Qt.RichText)
        safety.setWordWrap(True)
        outer.addWidget(safety)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._run_btn = QPushButton("▶ Run on Machine")
        self._run_btn.setStyleSheet(
            "QPushButton { background: #f5a623; color: #1d1f24; "
            "font-weight: 700; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background: #ffb945; }"
            "QPushButton:disabled { background: #5b4d2a; color: #8a8377; }"
        )
        self._run_btn.setEnabled(machine_connected and bool(self._surfacing_bits))
        if not machine_connected:
            self._run_btn.setToolTip("Connect to the CNC machine first")
        elif not self._surfacing_bits:
            self._run_btn.setToolTip(
                "Add a surfacing bit via Settings → My Bits"
            )
        self._run_btn.clicked.connect(self._on_run_clicked)
        btn_row.addWidget(self._run_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        outer.addLayout(btn_row)

        self._refresh_summary()

    @staticmethod
    def _mm_spin(value: float, lo: float, hi: float) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(2)
        sb.setSingleStep(0.1)
        sb.setSuffix(" mm")
        sb.setValue(value)
        sb.setKeyboardTracking(False)
        sb.setMinimumHeight(28)
        return sb

    def _build_plan(self) -> SurfacingPlan | None:
        bit = self._bit_combo.currentData()
        if bit is None:
            return None
        return compute_surfacing_plan(
            bit,
            stock_width_mm=self._w_spin.value(),
            stock_height_mm=self._h_spin.value(),
            pocket_depth_mm=self._pocket_depth_mm,
            glue_gap_mm=self._glue_gap_mm,
            plug_thickness_mm=self._plug_thickness_mm,
            feed_mm_min=float(self._feed_spin.value()),
            rpm=int(self._rpm_spin.value()),
            pass_depth_mm=self._pass_depth_spin.value(),
            extra_depth_mm=self._depth_spin.value() - self._glue_gap_mm,
        )

    def _refresh_summary(self) -> None:
        plan = self._build_plan()
        if plan is None or not plan.chains:
            self._summary.setText(
                "<i>Select a surfacing bit + valid panel dimensions to "
                "see the plan.</i>"
            )
            return
        # Estimate time = cut length / feed.
        cut_minutes = plan.cut_length_mm / max(1.0, float(self._feed_spin.value()))
        n_passes = max(
            1,
            int((plan.total_depth_mm + 0.001) // self._pass_depth_spin.value()) + (
                1 if plan.total_depth_mm % self._pass_depth_spin.value() > 0 else 0
            ),
        )
        self._summary.setText(
            f"<b>Plan</b><br>"
            f"&nbsp;&nbsp;Bit: <b>{plan.bit_name}</b> ({plan.bit_diameter_mm:.1f} mm)<br>"
            f"&nbsp;&nbsp;Total depth to remove: <b>{plan.total_depth_mm:.2f} mm</b><br>"
            f"&nbsp;&nbsp;Pass depth: {self._pass_depth_spin.value():.2f} mm<br>"
            f"&nbsp;&nbsp;Number of layers: <b>{n_passes}</b><br>"
            f"&nbsp;&nbsp;Cut length: {plan.cut_length_mm:,.0f} mm "
            f"(~{cut_minutes:.1f} min at {self._feed_spin.value()} mm/min)"
        )

    def _on_save_clicked(self) -> None:
        plan = self._build_plan()
        if plan is None or not plan.gcode:
            QMessageBox.warning(
                self, "Nothing to save",
                "Select a surfacing bit + valid panel dimensions first."
            )
            return
        self.saveRequested.emit(plan)
        self.accept()

    def _on_run_clicked(self) -> None:
        plan = self._build_plan()
        if plan is None or not plan.gcode:
            return
        self.runRequested.emit(plan, float(self._z_spin.value()))
        self.accept()
