"""Run blocking work in a subprocess behind a progress dialog so the main Qt
thread stays responsive — even when the work is pure-Python and otherwise
holds the GIL.

Why subprocess.Popen instead of QThread or multiprocessing.Process:

- **QThread**: Python's GIL serialises bytecode so a thread doing tight
  pure-Python loops (svgelements bezier flattening, shapely XOR loops) starves
  the main thread, beachballing the app even though we ran the work "off
  thread."

- **multiprocessing.Process**: works in dev mode but on macOS PyInstaller
  bundles, ``spawn`` re-launches the bundle's main exe and Python's
  ``freeze_support()`` does NOT intercept it the way it does on Windows.
  Result: every background task spawned a second (and the resource-tracker, a
  third) GUI window of the app.

The chosen mechanism: use ``subprocess.Popen([sys.executable, "--bg-worker"]``.
Inside the worker, the entry point sees the flag and skips Qt initialisation
entirely — it runs ``_bg_worker_main`` which reads a pickled (fn, args,
kwargs) from stdin, runs the function (with optional ``progress_cb``
injected), and streams tagged messages back over stdout: progress updates,
then a final result or error.

A short-lived QThread reads stdout messages and re-emits them as Qt signals
to a main-thread QObject handler, which updates the dialog and fires the
caller's callbacks. The blocking ``stdout.read`` releases the GIL, so the
main thread keeps painting the busy-bar (or the percent bar, once progress
events start arriving).

The function passed to ``run_with_progress`` must be importable at module
level (not a closure or lambda) and its args/kwargs/return must be
pickleable — they cross a process boundary.
"""
from __future__ import annotations

import os
import pickle
import struct
import subprocess
import sys
import traceback
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget


# A 4-byte big-endian length prefix delimits each pickled message in both
# directions. Keeping it that simple avoids needing a real framing library.
_LEN_FMT = ">I"


def _read_msg(stream) -> tuple | None:
    """Pull one length-prefixed pickled tuple off a binary stream. Returns
    None on clean EOF (worker exited)."""
    head = stream.read(4)
    if not head or len(head) < 4:
        return None
    (length,) = struct.unpack(_LEN_FMT, head)
    body = stream.read(length)
    if len(body) < length:
        return None
    return pickle.loads(body)


def _write_msg(stream, *payload: Any) -> None:
    """Length-prefix and pickle ``payload`` onto a binary stream."""
    data = pickle.dumps(payload)
    stream.write(struct.pack(_LEN_FMT, len(data)))
    stream.write(data)
    stream.flush()


# ---------------------------------------------------------------- worker mode

def _bg_worker_main() -> int:
    """Entry point when this exe is launched with ``--bg-worker``.

    Reads one (fn, args, kwargs) tuple from stdin, calls fn, streams progress
    updates + final outcome to stdout. Never imports Qt or initialises any
    GUI state, so a frozen-bundle re-launch never pops a second window.
    """
    try:
        spec = _read_msg(sys.stdin.buffer)
        if spec is None:
            _write_msg(sys.stdout.buffer, "err", "Worker received no work spec")
            return 1
        fn, args, kwargs = spec

        def progress_cb(pct: float, msg: str = "") -> None:
            try:
                _write_msg(sys.stdout.buffer, "progress", float(pct), str(msg))
            except Exception:
                pass

        # Inject progress_cb only if fn declares it; otherwise the dialog
        # stays in indeterminate-busy mode for that task.
        try:
            import inspect
            params = inspect.signature(fn).parameters
            if "progress_cb" in params and "progress_cb" not in kwargs:
                kwargs = {**kwargs, "progress_cb": progress_cb}
        except (TypeError, ValueError):
            pass

        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            _write_msg(
                sys.stdout.buffer, "err",
                f"{exc}\n\n{traceback.format_exc()}",
            )
            return 1
        _write_msg(sys.stdout.buffer, "ok", result)
        return 0
    except Exception as exc:
        try:
            _write_msg(
                sys.stdout.buffer, "err",
                f"Worker crashed: {exc}\n\n{traceback.format_exc()}",
            )
        except Exception:
            pass
        return 1


# ---------------------------------------------------------------- waiter thread

class _StdoutWaiter(QObject):
    """Lives in a short-lived QThread; reads tagged messages from a worker
    subprocess's stdout and re-emits them as Qt signals. The blocking reads
    release the GIL, so the main thread keeps animating the dialog."""
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, proc: subprocess.Popen) -> None:
        super().__init__()
        self._proc = proc

    def run(self) -> None:
        stdout = self._proc.stdout
        try:
            while True:
                msg = _read_msg(stdout)
                if msg is None:
                    # EOF: worker died without sending a final message.
                    rc = self._proc.poll()
                    stderr = ""
                    try:
                        if self._proc.stderr is not None:
                            stderr = self._proc.stderr.read().decode(
                                "utf-8", errors="replace"
                            )
                    except Exception:
                        pass
                    detail = stderr.strip() or f"return code {rc}"
                    self.failed.emit(
                        f"Worker process exited unexpectedly ({detail})."
                    )
                    return
                tag, *rest = msg
                if tag == "progress":
                    pct, status = rest
                    self.progress.emit(float(pct), str(status))
                elif tag == "ok":
                    self.finished.emit(rest[0])
                    return
                elif tag == "err":
                    self.failed.emit(str(rest[0]))
                    return
                else:
                    # Unknown tag — log via failed but keep going? Safer to fail.
                    self.failed.emit(f"Unknown worker message: {tag!r}")
                    return
        except Exception as exc:
            self.failed.emit(f"Worker IPC failed: {exc}\n\n{traceback.format_exc()}")
        finally:
            try:
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


# ---------------------------------------------------------------- main-thread handler

class _MainThreadHandler(QObject):
    """Bridge so worker signals run their slots in the main thread.

    Parented to the main-thread parent widget; PySide6 then auto-queues
    cross-thread signal deliveries through Qt's event system, guaranteeing
    the slots execute in the main thread's event loop.
    """
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_progress: Callable[[float, str], None] | None = None
        self._on_finished: Callable[[Any], None] | None = None
        self._on_failed: Callable[[str], None] | None = None

    def set_callbacks(
        self,
        on_progress: Callable[[float, str], None],
        on_finished: Callable[[Any], None],
        on_failed: Callable[[str], None],
    ) -> None:
        self._on_progress = on_progress
        self._on_finished = on_finished
        self._on_failed = on_failed

    # The @Slot decorators are required for PySide6 to register these as Qt
    # meta-methods, which is what makes cross-thread queued connections route
    # correctly to the handler's thread (the main thread). Without @Slot,
    # Qt may treat the connection as direct, invoking the slot in the
    # emitting (worker) thread — which then deadlocks when the slot calls
    # thread.wait()/dlg.close().
    @Slot(float, str)
    def handle_progress(self, pct: float, status: str) -> None:
        if self._on_progress is not None:
            self._on_progress(pct, status)

    @Slot(object)
    def handle_finished(self, result: Any) -> None:
        if self._on_finished is not None:
            self._on_finished(result)

    @Slot(str)
    def handle_failed(self, msg: str) -> None:
        if self._on_failed is not None:
            self._on_failed(msg)


# ---------------------------------------------------------------- subprocess launch

def _build_worker_command() -> list[str]:
    """Pick the right command line for re-launching ourselves as a worker.

    Bundled (PyInstaller .app): ``sys.executable`` is the bundle exe, so just
    pass ``--bg-worker``. Dev mode: ``sys.executable`` is the python
    interpreter, which doesn't know that flag, so launch the package as a
    module (``python -m cnc_inlay --bg-worker``).
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--bg-worker"]
    return [sys.executable, "-m", "cnc_inlay", "--bg-worker"]


# ---------------------------------------------------------------- public API

def run_with_progress(
    parent: QWidget,
    title: str,
    message: str,
    fn: Callable,
    *args: Any,
    on_success: Callable[[Any], None] | None = None,
    on_failure: Callable[[str], None] | None = None,
    failure_title: str | None = None,
    **kwargs: Any,
) -> None:
    """Run ``fn(*args, **kwargs)`` in a worker subprocess, showing a modal
    progress dialog over ``parent`` for the duration. The dialog starts in
    indeterminate-busy mode and switches to a real percent bar as soon as
    ``fn`` calls its injected ``progress_cb``.

    On completion, dismisses the dialog and calls ``on_success(result)``.
    On exception, surfaces via ``on_failure(msg)`` if provided, else a
    critical error dialog.
    """
    # Refuse work that obviously won't pickle. Better to surface the bug
    # immediately than have the subprocess die with an opaque error.
    try:
        pickle.dumps((fn, args, kwargs))
    except Exception as exc:
        if on_failure is not None:
            on_failure(
                f"Internal error: cannot dispatch work to subprocess "
                f"({exc}). The arguments aren't pickleable."
            )
        else:
            QMessageBox.critical(
                parent,
                failure_title or f"{title} failed",
                f"Internal error: cannot dispatch work to subprocess.\n\n{exc}",
            )
        return

    dlg = QProgressDialog(message, "", 0, 0, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.ApplicationModal)
    # No cancel — terminating mid-flight could leave shapely's C state in
    # an undefined place. The user can wait or force-quit the app.
    dlg.setCancelButton(None)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setMinimumWidth(380)
    dlg.show()
    # Force the dialog to paint NOW, before the subprocess spawn cost.
    QCoreApplication.processEvents()

    # Launch the worker subprocess. It re-uses our own executable; in worker
    # mode (--bg-worker flag) the entry point in app.py skips Qt and runs
    # _bg_worker_main, so no second GUI window can ever appear.
    cmd = _build_worker_command()
    env = os.environ.copy()
    # Avoid the worker subprocess picking up an unwanted Qt platform plugin.
    env.pop("QT_QPA_PLATFORM", None)
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            close_fds=True,
        )
    except Exception as exc:
        dlg.close()
        msg = f"Could not launch background worker:\n{exc}"
        if on_failure is not None:
            on_failure(msg)
        else:
            QMessageBox.critical(parent, failure_title or f"{title} failed", msg)
        return

    # Send the work spec on stdin and close it so the worker doesn't block
    # on a second read.
    try:
        _write_msg(proc.stdin, fn, args, kwargs)
        proc.stdin.close()
    except Exception as exc:
        try:
            proc.kill()
        except Exception:
            pass
        dlg.close()
        msg = f"Failed to dispatch work to subprocess:\n{exc}"
        if on_failure is not None:
            on_failure(msg)
        else:
            QMessageBox.critical(parent, failure_title or f"{title} failed", msg)
        return

    thread = QThread()
    waiter = _StdoutWaiter(proc)
    waiter.moveToThread(thread)
    handler = _MainThreadHandler(parent)
    # Force the handler's thread affinity to QApplication's main thread,
    # which is the only thread allowed to touch widgets. Parenting alone
    # isn't enough — PySide6 sometimes leaves the handler in whatever thread
    # the constructor ran in, which for a slot fired from a worker thread
    # would be wrong and re-introduce the dlg.close() / thread.wait()
    # deadlock observed in earlier crash reports.
    qapp = QCoreApplication.instance()
    if qapp is not None:
        handler.moveToThread(qapp.thread())

    # Keep references on the dialog so nothing gets garbage-collected
    # while the work is still running.
    dlg._bg_thread = thread          # type: ignore[attr-defined]
    dlg._bg_waiter = waiter          # type: ignore[attr-defined]
    dlg._bg_proc = proc              # type: ignore[attr-defined]
    dlg._bg_handler = handler        # type: ignore[attr-defined]

    def _cleanup() -> None:
        try:
            thread.quit()
            thread.wait(2000)
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass
        waiter.deleteLater()
        thread.deleteLater()
        dlg.close()
        dlg.deleteLater()
        handler.deleteLater()

    def _on_progress(pct: float, status: str) -> None:
        # Switch from busy mode to determinate when we get a real percentage.
        if dlg.maximum() == 0:
            dlg.setRange(0, 100)
        dlg.setValue(max(0, min(100, int(round(pct)))))
        if status:
            dlg.setLabelText(status)

    def _on_finished(result: Any) -> None:
        _cleanup()
        if on_success is not None:
            on_success(result)

    def _on_failed(msg: str) -> None:
        _cleanup()
        if on_failure is not None:
            on_failure(msg)
        else:
            QMessageBox.critical(
                parent,
                failure_title or f"{title} failed",
                _format_error_for_dialog(msg),
            )

    handler.set_callbacks(_on_progress, _on_finished, _on_failed)

    # Connect to handler's bound-method slots (NOT plain lambdas). The handler
    # is a QObject whose thread is the main thread, so PySide6 auto-queues the
    # cross-thread call into the main-thread event loop. A plain Python
    # callable as the slot has no thread affinity → PySide6 may queue it back
    # to the sender's thread, which causes the dialog-close deadlock we hit
    # in the previous design.
    waiter.progress.connect(handler.handle_progress)
    waiter.finished.connect(handler.handle_finished)
    waiter.failed.connect(handler.handle_failed)
    thread.started.connect(waiter.run)
    thread.start()


def _format_error_for_dialog(raw: str) -> str:
    """Pull the most useful first line out of the worker's traceback dump."""
    lines = raw.strip().splitlines()
    headline = lines[0] if lines else "Unknown error"
    if len(lines) > 1:
        return f"{headline}\n\nDetails:\n{raw}"
    return headline
