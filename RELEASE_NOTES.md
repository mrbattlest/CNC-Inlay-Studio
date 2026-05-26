- **Fixed SIGABRT crash on shutdown / failed startup.** When the app
  exited via any path that bypassed the normal window-close (an
  unhandled Python exception during startup, the in-app updater's
  relaunch race, etc.), the worker QThread that owns the serial port
  was usually blocked inside ``serial.read()``. The signal-based
  "close the port" message couldn't reach the worker because its Qt
  event loop wasn't running. When Python finalised, Qt's QThread
  destructor saw the thread was still alive and called ``abort()`` —
  the SIGABRT crash report you got after the recent update.

  Two fixes:

  - **Synchronous serial close in ``MachineController.shutdown()``.**
    Before we ask the QThread to quit, we close the OS file
    descriptor directly from the main thread. That unblocks
    ``serial.read()`` immediately, the worker's event loop drains,
    and ``_thread.quit() + wait()`` succeeds cleanly. No more
    abort-on-destruct.

  - **Startup-failure trap in ``app.py``.** Exceptions raised during
    ``MainWindow()`` construction or ``app.exec()`` now log to
    stderr + a ``startup-crash-YYYY-MM-DD.log`` file in
    ``~/Library/Logs/CNC Inlay Studio/``, show a one-shot error
    dialog with the traceback, and return a clean exit code 1 —
    instead of propagating through ``SystemExit → Py_Exit`` and
    triggering the cascading PySide destruction that produced the
    SIGABRT in the first place.

  If a startup crash recurs, the new ``startup-crash-*.log`` will
  tell us exactly what went wrong. Paste it back and I'll patch.
