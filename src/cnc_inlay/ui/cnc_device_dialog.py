"""Settings → My CNC Device — picker for which CNC the app should
target. Drives the bed envelope shown in the 2D + 3D canvases and
the screw-hole pattern (Lunyee 3018 PRO MAX only — others have no
documented clamp grid).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cnc_inlay.presets.cnc_machines import (
    CNCMachine,
    get_active_machine,
    load_catalog,
    set_active_machine,
)


_CUSTOM_KEY = "custom"


class MyCNCDeviceDialog(QDialog):
    """Pick from the catalog or define a custom bed size."""

    machineChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("My CNC Device")
        self.setMinimumWidth(620)
        self.setMinimumHeight(520)

        self._catalog = load_catalog()
        self._active = get_active_machine()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)

        intro = QLabel(
            "<b>Pick the CNC machine you're cutting on.</b><br>"
            "<span style='color:#9aa0a6'>"
            "The bed envelope shown in the Pocket / Plug / Surfacing 3D "
            "previews matches whatever you select here. The Lunyee "
            "3018 PRO MAX preset also includes the 24-screw clamp-hole "
            "map measured from the physical bed.</span>"
        )
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(14)

        # Left: list of machines + "Custom".
        self._list = QListWidget()
        self._list.setMinimumWidth(280)
        # Generous per-row padding so each machine name is easy to
        # click and the list reads as a vertical menu rather than a
        # tightly-packed scroll.
        self._list.setStyleSheet(
            "QListWidget { padding: 6px; outline: 0; }"
            "QListWidget::item { padding: 10px 12px; margin: 4px 2px; "
            "  border-radius: 6px; }"
            "QListWidget::item:hover { background: rgba(245, 166, 35, 30); }"
            "QListWidget::item:selected { background: #f5a623; color: #1d1f24; }"
        )
        for m in self._catalog:
            item = QListWidgetItem(m.name)
            item.setData(Qt.UserRole, m.key)
            self._list.addItem(item)
        custom_item = QListWidgetItem("Custom (specify your own bed size)…")
        custom_item.setData(Qt.UserRole, _CUSTOM_KEY)
        self._list.addItem(custom_item)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        body.addWidget(self._list, 1)

        # Right: details panel — either readonly catalog info or
        # editable Custom size form.
        self._details = QStackedWidget()

        # Catalog details page.
        self._catalog_panel = QWidget()
        cat_layout = QVBoxLayout(self._catalog_panel)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        self._cat_name_label = QLabel("")
        self._cat_name_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        cat_layout.addWidget(self._cat_name_label)
        self._cat_specs_label = QLabel("")
        self._cat_specs_label.setTextFormat(Qt.RichText)
        self._cat_specs_label.setWordWrap(True)
        cat_layout.addWidget(self._cat_specs_label)
        cat_layout.addStretch(1)

        # Custom size page.
        self._custom_panel = QWidget()
        cu_layout = QVBoxLayout(self._custom_panel)
        cu_layout.setContentsMargins(0, 0, 0, 0)
        cu_label = QLabel("<b>Custom CNC machine</b>")
        cu_label.setTextFormat(Qt.RichText)
        cu_layout.addWidget(cu_label)
        cu_form = QFormLayout()
        self._custom_name = QLineEdit("My CNC")
        cu_form.addRow("Name:", self._custom_name)
        # Specify in cm per the user's request — internal storage is mm.
        self._custom_x_cm = self._cm_spin(30.0)
        self._custom_y_cm = self._cm_spin(20.0)
        cu_form.addRow("Work area X:", self._custom_x_cm)
        cu_form.addRow("Work area Y:", self._custom_y_cm)
        cu_layout.addLayout(cu_form)
        cu_layout.addWidget(QLabel(
            "<span style='color:#9aa0a6;font-size:11px;'>"
            "X = left/right travel, Y = front/back travel. The 3D bed "
            "and screw-hole grid will resize to match.</span>"
        ))
        cu_layout.addStretch(1)

        self._details.addWidget(self._catalog_panel)  # index 0
        self._details.addWidget(self._custom_panel)   # index 1
        body.addWidget(self._details, 2)
        outer.addLayout(body)

        # Restore current selection.
        self._select_active()

        # Footer.
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    @staticmethod
    def _cm_spin(value_cm: float) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(5.0, 500.0)
        sb.setDecimals(1)
        sb.setSingleStep(1.0)
        sb.setSuffix(" cm")
        sb.setValue(value_cm)
        sb.setKeyboardTracking(False)
        sb.setMinimumHeight(28)
        return sb

    def _select_active(self) -> None:
        target_key = self._active.key
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.UserRole) == target_key:
                self._list.setCurrentRow(i)
                break
        if target_key == _CUSTOM_KEY:
            self._custom_name.setText(self._active.name)
            self._custom_x_cm.setValue(self._active.bed_width_mm / 10.0)
            self._custom_y_cm.setValue(self._active.bed_height_mm / 10.0)

    def _on_selection_changed(self, item: QListWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        key = item.data(Qt.UserRole)
        if key == _CUSTOM_KEY:
            self._details.setCurrentIndex(1)
            return
        machine = next((m for m in self._catalog if m.key == key), None)
        if machine is None:
            return
        self._cat_name_label.setText(machine.name)
        screw_count = len(machine.screw_hole_label_positions)
        screw_text = (
            f"{screw_count} clamp-hole positions documented"
            if screw_count else "<i>no clamp-hole map</i>"
        )
        self._cat_specs_label.setText(
            f"<table style='line-height:170%;'>"
            f"<tr><td style='color:#9aa0a6; padding-right:14px;'>Bed width (X):</td>"
            f"<td><b>{machine.bed_width_mm:.1f} mm</b> "
            f"({machine.bed_width_mm / 10:.1f} cm)</td></tr>"
            f"<tr><td style='color:#9aa0a6; padding-right:14px;'>Bed height (Y):</td>"
            f"<td><b>{machine.bed_height_mm:.1f} mm</b> "
            f"({machine.bed_height_mm / 10:.1f} cm)</td></tr>"
            f"<tr><td style='color:#9aa0a6; padding-right:14px;'>Clamp-hole map:</td>"
            f"<td>{screw_text}</td></tr>"
            f"</table>"
        )
        self._details.setCurrentIndex(0)

    def _save_and_accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            self.reject()
            return
        key = item.data(Qt.UserRole)
        if key == _CUSTOM_KEY:
            set_active_machine("custom", custom={
                "name": self._custom_name.text().strip() or "Custom CNC",
                "bed_width_mm": float(self._custom_x_cm.value()) * 10.0,
                "bed_height_mm": float(self._custom_y_cm.value()) * 10.0,
            })
        else:
            set_active_machine(key)
        self.machineChanged.emit()
        self.accept()
