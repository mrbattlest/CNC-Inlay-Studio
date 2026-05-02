"""Buy License + Enter License dialogs.

These are the two surfaces the user interacts with for licensing:
  * ``BuyLicenseDialog`` — informational; fired when a gated feature is
    used during expired trial OR when the user clicks the trial pill in
    the status bar. Has a "Buy License" button (opens Gumroad) and an
    "Enter License" button (chains into the activation dialog).
  * ``EnterLicenseDialog`` — email + key fields, "Activate" button.
    On success closes itself; on failure shows the error inline.
"""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cnc_inlay.licensing import (
    GUMROAD_PRODUCT_URL,
    LicenseStatus,
    activate_license,
    get_license_state,
)


class EnterLicenseDialog(QDialog):
    """Email + key, Activate button. Returns ``QDialog.Accepted`` only on
    successful Gumroad verification."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enter License")
        self.setModal(True)
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        intro = QLabel(
            "<p>Paste the email + license key from your Gumroad receipt.</p>"
        )
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._email = QLineEdit()
        self._email.setPlaceholderText("you@example.com")
        layout.addWidget(QLabel("Email"))
        layout.addWidget(self._email)

        self._key = QLineEdit()
        self._key.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        layout.addWidget(QLabel("License key"))
        layout.addWidget(self._key)

        self._error = QLabel("")
        self._error.setStyleSheet("color:#e0524a; font-weight:600;")
        self._error.setWordWrap(True)
        self._error.hide()
        layout.addWidget(self._error)

        buttons = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        activate_btn = QPushButton("Activate")
        activate_btn.setDefault(True)
        activate_btn.setObjectName("primary")
        cancel_btn.clicked.connect(self.reject)
        activate_btn.clicked.connect(self._on_activate)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(activate_btn)
        layout.addLayout(buttons)

    def _on_activate(self) -> None:
        email = self._email.text().strip()
        key = self._key.text().strip()
        if not email or not key:
            self._show_error("Please enter both your email and license key.")
            return
        # The verify call is a network round-trip; show a momentary "Verifying…"
        # cue so the dialog doesn't look frozen on slow connections.
        self.setEnabled(False)
        self._error.hide()
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            result = activate_license(email, key)
        finally:
            self.setEnabled(True)
        if result.valid:
            QMessageBox.information(
                self, "License activated",
                f"<p>Thanks! CNC Inlay Studio is now licensed to "
                f"<b>{email}</b>.</p>"
                "<p>All features are unlocked.</p>",
            )
            self.accept()
        else:
            self._show_error(result.error or "License key not recognised.")

    def _show_error(self, msg: str) -> None:
        self._error.setText(msg)
        self._error.show()


class BuyLicenseDialog(QDialog):
    """The 'this is a paid feature' / 'trial expired' prompt. Routes to
    Gumroad in the browser or to the EnterLicenseDialog."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        feature_name: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unlock CNC Inlay Studio")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        state = get_license_state()
        if state.status == LicenseStatus.TRIAL_EXPIRED:
            heading = "<h3 style='margin:0'>Your free trial has ended.</h3>"
        elif feature_name:
            heading = (
                f"<h3 style='margin:0'>"
                f"<span style='color:#f5a623'>{feature_name}</span> "
                f"is a paid feature.</h3>"
            )
        else:
            heading = "<h3 style='margin:0'>Unlock CNC Inlay Studio</h3>"
        title = QLabel(heading)
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        body = QLabel(
            "<p>Buy a one-time license for <b>$29.95</b> to unlock:</p>"
            "<ul>"
            "<li>Run jobs on your CNC machine</li>"
            "<li>AI-assisted SVG generation</li>"
            "<li>All future updates</li>"
            "</ul>"
            "<p>Loading designs, editing parameters, previewing toolpaths, "
            "and saving projects stay free forever.</p>"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        layout.addWidget(body)

        buttons = QHBoxLayout()
        later_btn = QPushButton("Maybe Later")
        enter_btn = QPushButton("Enter License…")
        buy_btn = QPushButton("Buy License")
        buy_btn.setDefault(True)
        buy_btn.setObjectName("primary")
        buttons.addWidget(later_btn)
        buttons.addStretch(1)
        buttons.addWidget(enter_btn)
        buttons.addWidget(buy_btn)
        layout.addLayout(buttons)

        later_btn.clicked.connect(self.reject)
        buy_btn.clicked.connect(self._on_buy)
        enter_btn.clicked.connect(self._on_enter)

    def _on_buy(self) -> None:
        webbrowser.open(GUMROAD_PRODUCT_URL)
        # Don't dismiss the dialog automatically — the user will likely
        # come back with a license key in a few minutes; leaving the
        # Enter License button visible saves them a menu hunt.

    def _on_enter(self) -> None:
        dlg = EnterLicenseDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.accept()


def show_buy_license_dialog(
    parent: QWidget | None = None, *, feature_name: str | None = None,
) -> bool:
    """Convenience wrapper. Returns True iff the user activated a license
    inside the dialog (caller can then proceed with the gated action)."""
    dlg = BuyLicenseDialog(parent, feature_name=feature_name)
    return dlg.exec() == QDialog.Accepted
