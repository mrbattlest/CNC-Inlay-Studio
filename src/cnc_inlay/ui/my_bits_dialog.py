"""My Bits — application-level setup dialog for the user's owned bit library.

Lives in the Settings menu, not per-project, because users typically buy
a set of bits once and then reuse them across every project. The dialog
pre-populates from a comprehensive catalog (1/8\" and 1/4\" shanks,
flat / ball-nose / V-bit / specialty); the user ticks which ones they
own. The selection persists to ``~/Library/Application Support/CNC
Inlay Studio/bits.json`` and the per-project Bits dropdowns are
populated from it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cnc_inlay.core.job import Bit
from cnc_inlay.presets.bits import load_bits, load_catalog, save_bits


_TYPE_LABELS = {
    "endmill": "Flat Endmill",
    "vbit": "V-Bit",
    "engraving": "Engraving",
    "surfacing": "Surfacing / Spoilboard",
}


@dataclass
class _CatalogRow:
    bit: Bit
    checkbox: QCheckBox
    section_key: tuple[str, float]


class MyBitsDialog(QDialog):
    """Pre-populated catalog with a checkbox per bit. Save writes the
    user's selection to bits.json and emits ``bitsChanged`` so the rest
    of the app can refresh its dropdowns."""

    bitsChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("My Bits")
        self.resize(640, 720)

        self._catalog: list[Bit] = load_catalog()
        owned = {b.name for b in load_bits()}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        intro = QLabel(
            "<b>Tell CNC Inlay Studio which bits you own.</b><br>"
            "<span style='color:#9aa0a6;'>"
            "The Pocket and Plug bit dropdowns in each project will only "
            "show the bits you check here. Add or remove later via "
            "Settings → My Bits.</span>"
        )
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # Search filter — narrows visible rows by substring.
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        search_row.addWidget(QLabel("Filter:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Type to filter (e.g. \"3 mm\", \"1/4\", \"v-bit\")")
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search, 1)
        outer.addLayout(search_row)

        # Scrollable section list.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(2)

        # Group by (type, shank_mm). Order: Flat Endmills first, then
        # Ball-Nose, V-Bit, Engraving. Within each, 1/8" before 1/4".
        type_order = ["endmill", "vbit", "engraving", "surfacing"]
        sections: dict[tuple[str, float], list[Bit]] = defaultdict(list)
        for bit in self._catalog:
            sections[(bit.type, bit.shank_mm)].append(bit)

        self._rows: list[_CatalogRow] = []
        self._section_widgets: dict[tuple[str, float], list[QWidget]] = {}
        for type_key in type_order:
            for shank in sorted({s for t, s in sections.keys() if t == type_key}):
                key = (type_key, shank)
                bits = sections[key]
                hdr = self._make_section_header(type_key, shank, key)
                body_layout.addWidget(hdr)
                row_widgets: list[QWidget] = [hdr]
                for bit in bits:
                    cb = QCheckBox(self._format_bit_label(bit))
                    cb.setChecked(bit.name in owned)
                    cb.stateChanged.connect(self._refresh_count)
                    body_layout.addWidget(cb)
                    row_widgets.append(cb)
                    self._rows.append(_CatalogRow(bit=bit, checkbox=cb, section_key=key))
                self._section_widgets[key] = row_widgets

        # Bottom padding so the last section's checkboxes aren't pinned
        # right against the scroll-area edge / footer buttons.
        body_layout.addSpacing(28)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # Footer counter + buttons.
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color:#9aa0a6;")
        outer.addWidget(self._count_label)
        self._refresh_count()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _format_bit_label(bit: Bit) -> str:
        flutes = f"{bit.flutes}-flute"
        return f"{bit.name}  ({flutes}, max {bit.max_rpm:,} RPM)"

    def _make_section_header(
        self, type_key: str, shank_mm: float, key: tuple[str, float]
    ) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 10, 0, 4)
        h.setSpacing(8)
        shank_label = (
            "1/8\" shank" if abs(shank_mm - 3.175) < 0.01
            else "1/4\" shank" if abs(shank_mm - 6.35) < 0.01
            else f"{shank_mm:.2f} mm shank"
        )
        title = QLabel(f"{_TYPE_LABELS.get(type_key, type_key.title())} — {shank_label}")
        f = QFont()
        f.setBold(True)
        f.setPointSize(11)
        title.setFont(f)
        title.setStyleSheet("color:#f5a623;")
        h.addWidget(title)
        h.addStretch(1)
        all_btn = QPushButton("Select all in section")
        all_btn.setMaximumWidth(180)
        all_btn.clicked.connect(lambda: self._set_section(key, True))
        none_btn = QPushButton("Clear")
        none_btn.setMaximumWidth(60)
        none_btn.clicked.connect(lambda: self._set_section(key, False))
        h.addWidget(all_btn)
        h.addWidget(none_btn)
        return wrap

    def _set_section(self, key: tuple[str, float], checked: bool) -> None:
        for r in self._rows:
            if r.section_key == key and r.checkbox.isVisible():
                r.checkbox.setChecked(checked)

    def _refresh_count(self) -> None:
        n = sum(1 for r in self._rows if r.checkbox.isChecked())
        self._count_label.setText(
            f"{n} of {len(self._rows)} bits selected"
        )

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        # Show / hide rows. Section headers stay visible iff any bit
        # in their section is visible.
        section_visible: dict[tuple[str, float], bool] = defaultdict(bool)
        for r in self._rows:
            visible = (not needle) or (needle in r.checkbox.text().lower())
            r.checkbox.setVisible(visible)
            if visible:
                section_visible[r.section_key] = True
        for key, widgets in self._section_widgets.items():
            # First widget in each section's list is the header.
            widgets[0].setVisible(section_visible.get(key, False))

    def _save_and_accept(self) -> None:
        chosen = [r.bit for r in self._rows if r.checkbox.isChecked()]
        if not chosen:
            from PySide6.QtWidgets import QMessageBox
            confirm = QMessageBox.question(
                self,
                "No bits selected",
                "<b>You haven't selected any bits.</b><br><br>"
                "Without at least one bit checked, the project's bit "
                "dropdown will be empty and you won't be able to run a cut. "
                "Continue anyway?",
                QMessageBox.Save | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if confirm != QMessageBox.Save:
                return
        save_bits(chosen)
        self.bitsChanged.emit()
        self.accept()
