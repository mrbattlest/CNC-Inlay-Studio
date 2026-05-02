"""Preferences dialog — currently holds the Google Gemini API key + model choice."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
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

        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
