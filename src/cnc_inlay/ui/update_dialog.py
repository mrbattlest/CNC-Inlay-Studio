"""'Update available' dialog with in-app download + auto-install.

Flow:
  1. ``show_update_available_dialog`` — read-only release notes + buttons.
  2. User clicks **Install Update** → dialog switches to a progress bar and
     downloads the .dmg in a worker thread (UI stays responsive).
  3. On download success → spawn a detached bash helper that waits for the
     app to exit, replaces /Applications/CNC Inlay Studio.app with the
     fresh bundle, and ``open``s the new version. The app then quits.
  4. Helper hands control back to the new app — feels like a single restart
     to the user.

Skip This Version persists per-version so the auto-check stops nagging
about a release the user explicitly declined. Manual Help → Check for
Updates… ignores Skip (the user clicked, they want to see the result).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from cnc_inlay.updater import (
    UpdateCheckResult,
    download_update_async,
    get_auto_check_enabled,
    get_installed_bundle_path,
    install_update_and_relaunch,
    set_auto_check_enabled,
    set_skipped_version,
)


# ------------------------------------------------------------ thread bridge

class _DownloadBridge(QObject):
    """Marshalls download-progress callbacks (which fire on the worker
    thread) back to the main Qt thread via signals — same pattern the
    update-check uses to avoid the dlg-touch-from-worker-thread crash."""
    progress = Signal(float, str)
    done = Signal(object)         # Path of the downloaded .dmg
    error = Signal(str)


# --------------------------------------------------------- main update dialog

def show_update_available_dialog(parent: QWidget, result: UpdateCheckResult) -> None:
    """Present the upgrade prompt. No-op if nothing to upgrade to."""
    if result.release is None or not result.is_update_available:
        return

    dlg = QDialog(parent)
    dlg.setWindowTitle("Update Available")
    dlg.setModal(True)
    dlg.setMinimumSize(580, 460)
    dlg._is_installing = False  # type: ignore[attr-defined] - guard against double-click

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

    # Hidden until the install starts: a progress bar + status line.
    progress_label = QLabel("")
    progress_label.setStyleSheet("color:#c9ced6;")
    progress_label.hide()
    layout.addWidget(progress_label)

    progress_bar = QProgressBar()
    progress_bar.setRange(0, 100)
    progress_bar.setTextVisible(True)
    progress_bar.hide()
    layout.addWidget(progress_bar)

    auto_check = QCheckBox("Automatically check for updates")
    auto_check.setChecked(get_auto_check_enabled())
    auto_check.toggled.connect(set_auto_check_enabled)
    layout.addWidget(auto_check)

    buttons = QHBoxLayout()
    buttons.setSpacing(8)
    skip_btn = QPushButton("Skip This Version")
    later_btn = QPushButton("Remind Me Later")
    install_btn = QPushButton("Install Update")
    install_btn.setDefault(True)
    install_btn.setObjectName("primary")
    buttons.addWidget(skip_btn)
    buttons.addStretch(1)
    buttons.addWidget(later_btn)
    buttons.addWidget(install_btn)
    layout.addLayout(buttons)

    bridge = _DownloadBridge(dlg)

    def _disable_dismiss_buttons() -> None:
        skip_btn.setEnabled(False)
        later_btn.setEnabled(False)
        install_btn.setEnabled(False)

    def _on_progress(pct: float, status: str) -> None:
        progress_label.setText(status)
        if pct < 0:
            # Server didn't send Content-Length — show indeterminate bar.
            progress_bar.setRange(0, 0)
        else:
            progress_bar.setRange(0, 100)
            progress_bar.setValue(max(0, min(100, int(round(pct)))))

    def _on_done(dmg_path) -> None:
        progress_label.setText("Installing… the app will restart momentarily.")
        progress_bar.setRange(0, 0)  # busy spinner while we hand off
        try:
            install_update_and_relaunch(dmg_path)
        except Exception as exc:
            QMessageBox.critical(
                parent,
                "Update install failed",
                f"<p>Could not install the update:</p><p><code>{exc}</code></p>"
                "<p>Your existing version is unchanged. Try again later, or "
                "download the .dmg manually.</p>",
            )
            dlg.reject()
            return
        # Quit the app — the helper will relaunch the new bundle.
        QApplication.instance().quit()

    def _on_error(msg: str) -> None:
        QMessageBox.critical(
            parent,
            "Download failed",
            f"<p>Couldn't download the update:</p><p><code>{msg}</code></p>"
            "<p>Check your internet connection and try again later.</p>",
        )
        dlg.reject()

    bridge.progress.connect(_on_progress)
    bridge.done.connect(_on_done)
    bridge.error.connect(_on_error)

    def _start_install() -> None:
        if dlg._is_installing:  # type: ignore[attr-defined]
            return
        # Dev mode (running from source) can't auto-install — there's no .app
        # to swap. Fall back to opening the .dmg URL in the browser.
        if get_installed_bundle_path() is None:
            import webbrowser
            webbrowser.open(result.release.dmg_url or result.release.html_url)
            QMessageBox.information(
                parent,
                "Download started",
                "<p>Auto-install only works in the bundled .app. The .dmg is "
                "downloading in your browser — drop it into "
                "<code>/Applications</code> when it finishes.</p>",
            )
            dlg.accept()
            return
        # Save the open project (if any) and record its path so the new
        # build reopens it on relaunch. If the user cancels (untitled
        # project + Save As cancelled), abort the install entirely.
        prep = getattr(parent, "prepare_for_update_install", None)
        if callable(prep) and not prep():
            return
        dlg._is_installing = True  # type: ignore[attr-defined]
        _disable_dismiss_buttons()
        progress_label.setText("Starting download…")
        progress_label.show()
        progress_bar.setRange(0, 0)
        progress_bar.setValue(0)
        progress_bar.show()
        url = result.release.dmg_url
        if not url:
            QMessageBox.critical(
                parent,
                "Update unavailable",
                "<p>The release manifest doesn't include a .dmg URL. "
                "Contact the developer.</p>",
            )
            dlg.reject()
            return
        download_update_async(
            url,
            on_progress=bridge.progress.emit,
            on_done=bridge.done.emit,
            on_error=bridge.error.emit,
        )

    def _skip() -> None:
        set_skipped_version(result.release.version)
        dlg.reject()

    install_btn.clicked.connect(_start_install)
    skip_btn.clicked.connect(_skip)
    later_btn.clicked.connect(dlg.reject)

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
