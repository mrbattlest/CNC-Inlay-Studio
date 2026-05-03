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
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
