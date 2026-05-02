"""Preferences dialog — Gemini API key + model choice + License section."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cnc_inlay.ai.secrets_store import (
    DEFAULT_MODEL,
    get_api_key,
    get_model,
    set_api_key,
    set_model,
)
from cnc_inlay.licensing import (
    LicenseStatus,
    clear_license,
    get_license_state,
)


_MODEL_CHOICES = [
    ("Gemini 2.5 Flash Image — Nano Banana (recommended)", "gemini-2.5-flash-image"),
    ("Gemini 3.1 Flash Image — Nano Banana 2 (newer, preview)", "gemini-3.1-flash-image-preview"),
    ("Gemini 3 Pro Image — Nano Banana Pro (highest quality)", "gemini-3-pro-image-preview"),
]


class PreferencesDialog(QDialog):
    """Edit the Gemini API key (Keychain) + model preference."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(
            "<b>AI design generation</b><br>"
            "Provide your Google Gemini API key to enable the ✨ Generate from "
            "prompt feature. Get a key at "
            "<a href='https://aistudio.google.com/apikey'>aistudio.google.com/apikey</a>. "
            "Image-generation models require a paid billing plan on the API key."
            "<br><br>"
            "The key is stored locally in <code>~/Library/Application Support/"
            "CNC Inlay Studio/secrets.json</code> with owner-only file "
            "permissions (0600). It is not encrypted — anyone with disk "
            "access to your Mac can read it."
        )
        intro.setOpenExternalLinks(True)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("AIza…")
        existing = get_api_key()
        if existing:
            self.key_edit.setText(existing)
        form.addRow("API key:", self.key_edit)

        self.model_combo = QComboBox()
        for label, model_id in _MODEL_CHOICES:
            self.model_combo.addItem(label, model_id)
        self._select_model(get_model())
        form.addRow("Model:", self.model_combo)

        layout.addLayout(form)

        hint = QLabel(
            "<span style='color:#9aa0a6'>Leave the key blank and click OK to "
            "remove it.</span>"
        )
        hint.setTextFormat(Qt.RichText)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ---- License section ---------------------------------------------
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#3a3f47;")
        layout.addWidget(sep)

        lic_title = QLabel("<b>License</b>")
        lic_title.setTextFormat(Qt.RichText)
        layout.addWidget(lic_title)

        self._lic_status_label = QLabel("")
        self._lic_status_label.setTextFormat(Qt.RichText)
        self._lic_status_label.setWordWrap(True)
        layout.addWidget(self._lic_status_label)

        lic_btns = QHBoxLayout()
        self._buy_btn = QPushButton("Buy License…")
        self._enter_btn = QPushButton("Enter License…")
        self._deactivate_btn = QPushButton("Deactivate")
        self._deactivate_btn.setToolTip(
            "Remove the license from this Mac (e.g. to move it to a different machine)."
        )
        lic_btns.addWidget(self._buy_btn)
        lic_btns.addWidget(self._enter_btn)
        lic_btns.addStretch(1)
        lic_btns.addWidget(self._deactivate_btn)
        layout.addLayout(lic_btns)

        self._buy_btn.clicked.connect(self._open_buy_dialog)
        self._enter_btn.clicked.connect(self._open_enter_dialog)
        self._deactivate_btn.clicked.connect(self._deactivate)
        self._refresh_license_section()

        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ---- license-section helpers ---------------------------------------

    def _refresh_license_section(self) -> None:
        state = get_license_state()
        if state.status == LicenseStatus.LICENSED:
            self._lic_status_label.setText(
                "<span style='color:#9ee84a; font-weight:600;'>"
                "✓ Licensed</span>"
                f"<br><span style='color:#c9ced6;'>"
                f"Registered to <b>{state.licensed_to_email}</b></span>"
            )
            self._buy_btn.hide()
            self._enter_btn.hide()
            self._deactivate_btn.show()
        elif state.status == LicenseStatus.TRIAL_ACTIVE:
            d = state.days_remaining_in_trial
            self._lic_status_label.setText(
                f"<span style='color:#f5a623; font-weight:600;'>"
                f"Trial — {d} day{'s' if d != 1 else ''} remaining</span>"
                "<br><span style='color:#c9ced6;'>"
                "All features are unlocked during the trial.</span>"
            )
            self._buy_btn.show()
            self._enter_btn.show()
            self._deactivate_btn.hide()
        else:  # TRIAL_EXPIRED
            self._lic_status_label.setText(
                "<span style='color:#e0524a; font-weight:600;'>"
                "Trial expired</span>"
                "<br><span style='color:#c9ced6;'>"
                "Buy a license to unlock Run on Machine and AI Generate.</span>"
            )
            self._buy_btn.show()
            self._enter_btn.show()
            self._deactivate_btn.hide()

    def _open_buy_dialog(self) -> None:
        from cnc_inlay.ui.license_dialog import show_buy_license_dialog
        if show_buy_license_dialog(self):
            self._refresh_license_section()
            self._refresh_parent_pill()

    def _open_enter_dialog(self) -> None:
        from cnc_inlay.ui.license_dialog import EnterLicenseDialog
        dlg = EnterLicenseDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._refresh_license_section()
            self._refresh_parent_pill()

    def _deactivate(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        confirm = QMessageBox.question(
            self,
            "Deactivate license?",
            "<p>Remove this license from this Mac? You can re-enter it any "
            "time using the same email + key.</p>",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        clear_license()
        self._refresh_license_section()
        self._refresh_parent_pill()

    def _refresh_parent_pill(self) -> None:
        """Tell the parent MainWindow (if there is one) to update its
        trial-status pill so the change shows immediately."""
        parent = self.parent()
        refresher = getattr(parent, "_refresh_trial_pill", None)
        if callable(refresher):
            refresher()

    def _select_model(self, model_id: str) -> None:
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == model_id:
                self.model_combo.setCurrentIndex(i)
                return
        # Unknown / stored model — show it as a custom entry.
        self.model_combo.insertItem(0, f"(custom) {model_id}", model_id)
        self.model_combo.setCurrentIndex(0)

    def _save_and_accept(self) -> None:
        set_api_key(self.key_edit.text())
        chosen = self.model_combo.currentData() or DEFAULT_MODEL
        set_model(chosen)
        self.accept()
