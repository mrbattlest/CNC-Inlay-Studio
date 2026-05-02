"""'Update available' dialog + 'You're up to date' / 'Check failed' notices.

Driven by ``cnc_inlay.updater.UpdateCheckResult``. The user can:
  - Download Update — opens the .dmg URL in the system browser. (NSWorkspace
    will hand it to Safari, which downloads to ~/Downloads and surfaces the
    standard "Open with DiskImageMounter" prompt.)
  - View on GitHub — opens the release page (used when no .dmg asset present).
  - Skip This Version — persist the skip; auto-check stays quiet for this
    version, will speak up again on the next one.
  - Remind Me Later — dismisses this dialog only.
"""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from cnc_inlay.updater import (
    UpdateCheckResult,
    get_auto_check_enabled,
    set_auto_check_enabled,
    set_skipped_version,
)


def show_update_available_dialog(parent: QWidget, result: UpdateCheckResult) -> None:
    """Present the upgrade prompt. No-op if nothing to upgrade to."""
    if result.release is None or not result.is_update_available:
        return

    dlg = QDialog(parent)
    dlg.setWindowTitle("Update Available")
    dlg.setModal(True)
    dlg.setMinimumSize(560, 420)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(20, 20, 20, 16)
    layout.setSpacing(10)

    headline = QLabel(
        f"<h3 style='margin:0'>CNC Inlay Studio "
        f"<span style='color:#f5a623'>v{result.release.version}</span> is "
        f"available</h3>"
        f"<p style='margin:4px 0 0 0; color:#9aa0a6;'>"
        f"You're currently on v{result.current_version}.</p>"
    )
    headline.setTextFormat(Qt.RichText)
    layout.addWidget(headline)

    notes_label = QLabel("Release notes:")
    notes_label.setStyleSheet("color:#c9ced6; margin-top:6px;")
    layout.addWidget(notes_label)

    notes = QTextBrowser()
    notes.setOpenExternalLinks(True)
    notes.setMarkdown(result.release.notes or "_No release notes provided._")
    notes.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    layout.addWidget(notes, 1)

    auto_check = QCheckBox("Automatically check for updates")
    auto_check.setChecked(get_auto_check_enabled())
    auto_check.toggled.connect(set_auto_check_enabled)
    layout.addWidget(auto_check)

    buttons = QHBoxLayout()
    buttons.setSpacing(8)
    skip_btn = QPushButton("Skip This Version")
    later_btn = QPushButton("Remind Me Later")
    primary_label = "Download Update" if result.release.dmg_url else "View on GitHub"
    download_btn = QPushButton(primary_label)
    download_btn.setDefault(True)
    download_btn.setObjectName("primary")

    buttons.addWidget(skip_btn)
    buttons.addStretch(1)
    buttons.addWidget(later_btn)
    buttons.addWidget(download_btn)
    layout.addLayout(buttons)

    def _download() -> None:
        target_url = result.release.dmg_url or result.release.html_url
        if target_url:
            webbrowser.open(target_url)
        # Show the manual replacement hint so the user knows what to do
        # once the .dmg finishes downloading and they've mounted it.
        QMessageBox.information(
            parent,
            "Download started",
            "<p>The new <b>CNC Inlay Studio</b> installer is downloading "
            "in your browser.</p>"
            "<p>When it finishes:</p>"
            "<ol>"
            "<li>Open the <code>.dmg</code> file from Downloads.</li>"
            "<li>Drag <b>CNC Inlay Studio.app</b> onto the <b>Applications</b> "
            "shortcut in the disk-image window — replace the existing app "
            "when prompted.</li>"
            "<li>Quit this app and re-open it from Applications.</li>"
            "</ol>",
        )
        dlg.accept()

    def _skip() -> None:
        set_skipped_version(result.release.version)
        dlg.reject()

    def _later() -> None:
        dlg.reject()

    download_btn.clicked.connect(_download)
    skip_btn.clicked.connect(_skip)
    later_btn.clicked.connect(_later)

    dlg.exec()


def show_up_to_date_dialog(parent: QWidget, current_version: str) -> None:
    QMessageBox.information(
        parent,
        "You're up to date",
        f"<p>CNC Inlay Studio <b>v{current_version}</b> is the latest version.</p>",
    )


def show_check_failed_dialog(parent: QWidget) -> None:
    QMessageBox.warning(
        parent,
        "Update check failed",
        "<p>Couldn't reach the update server. Check your internet "
        "connection and try again later.</p>",
    )
