"""CNC Inlay Studio — application entry point.

Two run modes share the same executable:

- **Default GUI mode**: launches Qt and the main window.
- **Background-worker mode** (``--bg-worker`` flag): launched by
  ``ui/background_task.py`` to run a single function in an isolated process
  without touching Qt — this is what makes long SVG/pipeline operations not
  freeze the UI and not pop a duplicate GUI window in PyInstaller bundles.
"""
from __future__ import annotations

import sys


def main() -> int:
    # Worker mode MUST be detected and dispatched before any Qt import.
    # The bundled .app exe is also re-launched as the worker subprocess; if
    # we initialised Qt up front, every background task would pop a second
    # window. background_task launches us with --bg-worker via subprocess.Popen
    # and pipes a pickled (fn, args, kwargs) over stdin.
    if "--bg-worker" in sys.argv:
        from cnc_inlay.ui.background_task import _bg_worker_main
        return _bg_worker_main()

    # Lazy imports keep the worker subprocess clean of Qt.
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from cnc_inlay.ui.main_window import MainWindow
    from cnc_inlay.ui.theme import apply_theme
    # Apply the user's chosen CNC machine BEFORE any canvas is
    # constructed so the bed envelope they see matches their device.
    # Falls back silently to the LUNYEE 3018 PRO MAX preset if no
    # choice has been saved (Settings → My CNC Device sets it).
    from cnc_inlay.presets.cnc_machines import get_active_machine
    from cnc_inlay.ui.canvas import apply_active_machine
    from cnc_inlay.ui.canvas3d import apply_active_machine_3d
    _active_machine = get_active_machine()
    apply_active_machine(_active_machine)
    apply_active_machine_3d(_active_machine)

    # MUST be set before QApplication is constructed. Without this, the
    # QOpenGLWidgets in a second top-level window (File → New Project
    # Window) get isolated GL contexts on macOS and silently fail to
    # render the bed grid + screw circles. Symptom: only the GLTextItem
    # axis labels (which use Qt's painter, not raw GL) show up.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)

    app = QApplication(sys.argv)
    app.setApplicationName("CNC Inlay Studio")
    app.setOrganizationName("CNC Inlay Studio")
    apply_theme(app)
    # Trap any unhandled exception during MainWindow construction. If
    # the constructor raises (e.g. a corrupted project file from the
    # last session), the exception used to propagate through
    # raise SystemExit → Py_Exit → atexit → PySide destroys QObjects,
    # and the still-running worker QThread tripped Qt's
    # "Destroyed while thread is still running" fatal abort. The whole
    # process crashed with SIGABRT before the user could see anything.
    # Now we log to stderr, write a crash file the user can share, and
    # show a one-shot error dialog so the next launch starts clean.
    try:
        window = MainWindow()
    except Exception as exc:
        return _report_startup_failure(app, exc)
    window.show()
    try:
        return app.exec()
    except Exception as exc:
        return _report_startup_failure(app, exc)


def _report_startup_failure(app, exc: BaseException) -> int:
    """Surface a startup-time exception to the user + a crash-log file
    instead of letting Python's exit cascade SIGABRT the process."""
    import traceback
    from datetime import datetime
    tb_text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    sys.stderr.write("CNC Inlay Studio failed to start:\n" + tb_text)
    # Write a one-line-per-launch crash log the user can paste in a
    # bug report. Lives next to the run logs so it's easy to find.
    try:
        from pathlib import Path
        log_dir = Path.home() / "Library" / "Logs" / "CNC Inlay Studio"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        (log_dir / f"startup-crash-{stamp}.log").write_text(tb_text)
    except Exception:
        pass
    try:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None,
            "CNC Inlay Studio couldn't start",
            "<b>The app hit an error while starting up.</b><br><br>"
            "A crash log was saved to "
            "<code>~/Library/Logs/CNC Inlay Studio/</code>. Quit and "
            "relaunch — if it keeps happening, share the crash log "
            "with the developer.<br><br>"
            f"<pre style='font-size:10px'>{tb_text[-800:]}</pre>"
        )
    except Exception:
        pass
    # Drop reference to the QApplication so any controller / worker
    # that was constructed before the failure has its atexit shutdown
    # called (closing the serial port + stopping the QThread) BEFORE
    # the interpreter starts tearing down PySide.
    app.processEvents()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
