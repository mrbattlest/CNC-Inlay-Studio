"""Qt-aware coordinator for GRBL machine control.

`MachineController` lives in the main thread; a `MachineWorker` lives in a
background QThread and owns the actual serial port. UI signals are routed to
the worker via queued connections so the main thread never blocks on I/O.

This module ships with the v1 *connection + jog + zero* feature set. The
streaming/run path lives in :mod:`cnc_inlay.control.streamer` and is wired in
during phase 5.
"""
from __future__ import annotations

import atexit
from dataclasses import replace

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from cnc_inlay.control.grbl import (
    CYCLE_START,
    FEED_HOLD,
    SOFT_RESET,
    STATUS_QUERY,
    MachineState,
    Position,
    Response,
    ResponseKind,
    Status,
    alarm_text,
    cmd_goto_zero_xy,
    cmd_goto_zero_z,
    cmd_jog,
    cmd_set_work_zero_all,
    cmd_set_work_zero_xy,
    cmd_set_work_zero_z,
    error_text,
    parse_response,
    parse_status,
)
from cnc_inlay.control.checkpoint import (
    Checkpoint,
    clear_checkpoint,
    save_checkpoint,
)
from cnc_inlay.control.serial_io import SerialIO
from cnc_inlay.control.streamer import Streamer


_SETTING_RE = __import__("re").compile(r"^\$(\d+)\s*=\s*([-+]?\d*\.?\d+)")


def _parse_setting_line(text: str) -> tuple[int, float] | None:
    """Parse a GRBL setting line of the form ``$<id>=<value>`` (or
    ``$<id>=<value> (description)`` on some forks). Returns None on miss."""
    s = text.strip()
    m = _SETTING_RE.match(s)
    if m is None:
        return None
    try:
        return int(m.group(1)), float(m.group(2))
    except ValueError:
        return None


class MachineWorker(QObject):
    """Lives in the worker QThread. Owns the SerialIO + read/poll timers."""

    # Outgoing signals — connected to MachineController slots.
    statusReceived = Signal(object)  # Status (with cached WCO injected)
    responseReceived = Signal(object)  # Response
    welcomeReceived = Signal()
    workerError = Signal(str)
    portOpened = Signal(str)
    portClosed = Signal(str)
    streamStarted = Signal(int)        # total lines
    streamProgress = Signal(int, int)  # cursor, total
    streamCompleted = Signal()
    streamAborted = Signal(str)        # reason
    toolChangeRequired = Signal(str)   # bit name about to be paused for
    settingsReceived = Signal(dict, list)  # ({id: value}, [raw_response_lines])

    def __init__(self) -> None:
        super().__init__()
        self._serial: SerialIO | None = None
        self._read_timer: QTimer | None = None
        self._status_timer: QTimer | None = None
        # GRBL only re-reports WCO every ~10 status messages, so we cache it
        # locally and inject it into reports that don't include one.
        self._cached_wco: Position | None = None
        # Streaming.
        self._streamer: Streamer | None = None
        self._stream_timer: QTimer | None = None
        self._checkpoint: Checkpoint | None = None
        self._checkpoint_timer: QTimer | None = None
        # Settings query.
        self._collecting_settings: dict[int, float] | None = None
        self._collected_other_lines: list[str] = []
        self._settings_timer: QTimer | None = None
        # Tool-change deferral. When _stream_tick sends an M0, we stash the
        # upcoming bit name here; we DON'T emit toolChangeRequired until a
        # status report shows the machine has actually entered Hold (which
        # means GRBL drained its planner queue, finished the in-flight cuts,
        # and stopped the spindle). Without this the banner appeared as
        # soon as the M0 hit the serial buffer — often many seconds before
        # the spindle actually stopped, prompting the user to reach for a
        # spinning bit.
        self._pending_tool_change_bit: str | None = None

    # ---------------------------------------------------------------- slots

    @Slot(str)
    def open_port(self, port: str) -> None:
        if self._serial is not None and self._serial.is_open:
            return
        # Open the per-session run log BEFORE the port — we want the
        # port-open attempt itself recorded so failures are diagnosable.
        from cnc_inlay.control import run_log
        log_path = run_log.open_session()
        run_log.log_user(f"Connect {port}")
        try:
            io = SerialIO(port)
            io.open()
        except Exception as exc:
            run_log.log_error(f"open_port failed: {exc}")
            self.workerError.emit(f"Failed to open {port}: {exc}")
            return
        self._serial = io
        if log_path is not None:
            run_log.log_event(f"port opened: {port}")

        # Read loop — polls the serial buffer at 30 Hz.
        self._read_timer = QTimer(self)
        self._read_timer.setInterval(33)
        self._read_timer.timeout.connect(self._tick_read)
        self._read_timer.start()

        # Status poll — sends `?` 5 times per second.
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(200)
        self._status_timer.timeout.connect(self._poll_status)
        self._status_timer.start()

        # GRBL wakes on a CR/LF.
        try:
            io.write(b"\r\n")
        except Exception:
            pass

        self.portOpened.emit(port)

    @Slot()
    def close_port(self) -> None:
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None
        self._streamer = None
        if self._read_timer is not None:
            self._read_timer.stop()
            self._read_timer = None
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        self._cached_wco = None
        from cnc_inlay.control import run_log
        run_log.log_user("Disconnect")
        run_log.close_session()
        self.portClosed.emit("user")

    @Slot(str)
    def write_line(self, line: str) -> None:
        if self._serial is None:
            return
        from cnc_inlay.control import run_log
        run_log.log_tx(line)
        try:
            self._serial.write_line(line)
        except Exception as exc:
            run_log.log_error(f"write_line failed: {exc}")
            self.workerError.emit(f"Write failed: {exc}")

    @Slot(bytes)
    def write_realtime(self, data: bytes) -> None:
        if self._serial is None:
            return
        from cnc_inlay.control import run_log
        run_log.log_tx_realtime(data)
        try:
            self._serial.write(data)
        except Exception as exc:
            run_log.log_error(f"write_realtime failed: {exc}")
            self.workerError.emit(f"Realtime write failed: {exc}")

    # --------------------------------------------------------- streaming slots

    @Slot(str, int, "QVariant")
    def start_stream(
        self, gcode: str, start_line: int = 0, checkpoint: Checkpoint | None = None
    ) -> None:
        if self._serial is None or self._streamer is not None:
            return
        self._streamer = Streamer()
        self._streamer.load(gcode, start_line=start_line)
        self._checkpoint = checkpoint
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(15)
        self._stream_timer.timeout.connect(self._stream_tick)
        self._stream_timer.start()
        # Periodically save the checkpoint to disk (every 2 s).
        if checkpoint is not None:
            self._checkpoint_timer = QTimer(self)
            self._checkpoint_timer.setInterval(2000)
            self._checkpoint_timer.timeout.connect(self._save_checkpoint_tick)
            self._checkpoint_timer.start()
        self.streamStarted.emit(self._streamer.total)

    @Slot()
    def stream_pause(self) -> None:
        """Send GRBL feed-hold (`!`). Always works when connected, even if the
        streamer state is no longer active — useful for hold/resume after a
        connection hiccup."""
        if self._serial is None:
            return
        if self._streamer is not None:
            self._streamer.pause()
        try:
            self._serial.write(FEED_HOLD)
        except Exception:
            pass

    @Slot()
    def stream_resume(self) -> None:
        """Send GRBL cycle-start (`~`). Works whether or not a streamer is
        active — clears any feed-hold state on the machine."""
        if self._serial is None:
            return
        if self._streamer is not None:
            self._streamer.resume()
        try:
            self._serial.write(CYCLE_START)
        except Exception:
            pass

    @Slot()
    def stream_abort(self) -> None:
        if self._serial is None:
            return
        if self._streamer is not None:
            self._streamer.abort()
        try:
            self._serial.write(SOFT_RESET)
        except Exception:
            pass
        self._stop_stream("user abort")

    def _stop_stream(self, reason: str) -> None:
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None
        if self._checkpoint_timer is not None:
            self._checkpoint_timer.stop()
            self._checkpoint_timer = None
        self._streamer = None
        self._pending_tool_change_bit = None
        from cnc_inlay.control import run_log
        run_log.log_event(f"stream stopped: {reason}")
        # Keep the checkpoint on disk after an abort/error so the user can
        # recover. Only clear on clean completion.
        self.streamAborted.emit(reason)

    @Slot()
    def query_settings(self) -> None:
        """Send $$ and collect setting responses, then emit settingsReceived.

        We wait 3 seconds total. If nothing parses, the UI also gets the raw
        captured lines so the user (and the developer) can see what GRBL
        actually said.
        """
        if self._serial is None:
            return
        self._collecting_settings = {}
        self._collected_other_lines = []
        try:
            self._serial.write(b"\r\n")
            self._serial.write_line("$$")
        except Exception as exc:
            self.workerError.emit(f"Settings query failed: {exc}")
            self._collecting_settings = None
            return
        if self._settings_timer is None:
            self._settings_timer = QTimer(self)
            self._settings_timer.setSingleShot(True)
            self._settings_timer.timeout.connect(self._finish_settings_collection)
        self._settings_timer.start(3000)

    def _finish_settings_collection(self) -> None:
        if self._collecting_settings is not None:
            self.settingsReceived.emit(
                dict(self._collecting_settings),
                list(self._collected_other_lines),
            )
        self._collecting_settings = None
        self._collected_other_lines = []

    def _stream_tick(self) -> None:
        if self._streamer is None or self._serial is None:
            return
        from cnc_inlay.control import run_log
        cursor_before = self._streamer.cursor

        # Wrap the write function so every streamed line lands in the
        # run log too — the streamer calls serial.write directly with
        # the raw bytes, bypassing our write_line slot, so without
        # this wrapper streamed G-code would be invisible to the log.
        def _logging_write(payload: bytes) -> None:
            try:
                run_log.log_tx(payload.decode("ascii", errors="replace"))
            except Exception:
                pass
            self._serial.write(payload)

        self._streamer.feed(_logging_write)
        cursor_after = self._streamer.cursor
        # Detect tool changes: any newly-sent line whose index is in the
        # streamer's tool_change_at map means there's an M0 in flight for
        # that bit. STASH the bit name — the actual UI prompt is deferred
        # until we see GRBL transition to Hold, so the operator isn't told
        # to swap a spinning bit while the planner queue drains.
        if self._streamer.tool_change_at:
            for idx in range(cursor_before, cursor_after):
                bit_name = self._streamer.tool_change_at.get(idx)
                if bit_name is not None:
                    self._pending_tool_change_bit = bit_name
        self.streamProgress.emit(self._streamer.cursor, self._streamer.total)
        if self._streamer.is_done:
            if self._stream_timer is not None:
                self._stream_timer.stop()
                self._stream_timer = None
            if self._checkpoint_timer is not None:
                self._checkpoint_timer.stop()
                self._checkpoint_timer = None
            self._streamer = None
            self._checkpoint = None
            self._pending_tool_change_bit = None
            clear_checkpoint()  # clean completion — no need to recover
            run_log.log_event("stream completed")
            self.streamCompleted.emit()

    def _save_checkpoint_tick(self) -> None:
        if self._checkpoint is None or self._streamer is None:
            return
        # Cursor minus pending = lines GRBL has actually acknowledged.
        acked = max(0, self._streamer.cursor - len(self._streamer.pending))
        self._checkpoint.cursor = acked
        self._checkpoint.total_lines = self._streamer.total
        save_checkpoint(self._checkpoint)

    # -------------------------------------------------------------- private

    def _tick_read(self) -> None:
        if self._serial is None:
            return
        from cnc_inlay.control import run_log
        for _ in range(64):  # cap iterations to avoid starving the event loop
            line = self._serial.read_line()
            if not line:
                break
            # Status reports (5 Hz polling) would flood the log — skip
            # them. Anything else (ok / error / ALARM / [MSG:] / setting
            # responses / welcome banners) is interesting.
            if not (line.startswith("<") and line.endswith(">")):
                run_log.log_rx(line)
            if line.startswith("<") and line.endswith(">"):
                status = parse_status(line)
                if status is not None:
                    # Cache fresh WCO when GRBL reports one; otherwise inject
                    # the last known one so effective_wpos stays consistent.
                    if status.wco is not None:
                        self._cached_wco = status.wco
                    elif self._cached_wco is not None and status.mpos is not None:
                        status = replace(status, wco=self._cached_wco)
                    self.statusReceived.emit(status)
                    # Once GRBL actually halts (Hold), the M0 has finished
                    # draining the planner queue and the spindle is stopped.
                    # Emit the deferred tool-change prompt now — it's safe
                    # to swap the bit.
                    if (
                        self._pending_tool_change_bit is not None
                        and status.state == MachineState.HOLD
                    ):
                        bit = self._pending_tool_change_bit
                        self._pending_tool_change_bit = None
                        self.toolChangeRequired.emit(bit)
                continue
            resp = parse_response(line)
            # Streaming uses character counting — every 'ok' / 'error' frees
            # one in-flight line slot in the GRBL serial buffer.
            if self._streamer is not None:
                if resp.kind == ResponseKind.OK:
                    self._streamer.on_ok()
                elif resp.kind == ResponseKind.ERROR:
                    self._streamer.on_error()
                    self._stop_stream(f"GRBL error {resp.code}: {error_text(resp.code)}")
            # Setting collector — accumulates `$N=value` while a $$ query is
            # active and captures every other line for debug surfacing.
            if self._collecting_settings is not None:
                # Cap the debug-line buffer so a chatty/garbled GRBL during
                # the 3-second collection window can't grow it without bound.
                if len(self._collected_other_lines) < 200:
                    self._collected_other_lines.append(resp.raw)
                if resp.kind == ResponseKind.SETTING:
                    parsed = _parse_setting_line(resp.text)
                    if parsed is not None:
                        setting_id, value = parsed
                        self._collecting_settings[setting_id] = value
            self.responseReceived.emit(resp)
            if resp.kind == ResponseKind.WELCOME:
                self.welcomeReceived.emit()
                # GRBL was reset — work coordinate system rebooted too.
                self._cached_wco = None

    def _poll_status(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.write(STATUS_QUERY)
        except Exception:
            pass


class MachineController(QObject):
    """Public façade for the UI."""

    # Process-level lock: only one MachineController in the whole app may hold
    # the serial port at a time. With multiple project windows open, this
    # lets the second window's connect attempt fail with a clear "another
    # window is connected" message instead of fighting over the tty handle.
    _active_connection: "MachineController | None" = None

    # UI-facing signals.
    connectionChanged = Signal(bool, str)
    statusChanged = Signal(object)
    error = Signal(str)
    alarm = Signal(int, str)
    response = Signal(object)
    streamStarted = Signal(int)
    streamProgress = Signal(int, int)
    streamCompleted = Signal()
    streamAborted = Signal(str)
    toolChangeRequired = Signal(str)
    settingsReceived = Signal(dict, list)

    # Internal signals to the worker (queued cross-thread).
    _open_requested = Signal(str)
    _close_requested = Signal()
    _send_line = Signal(str)
    _send_realtime = Signal(bytes)
    _start_stream_requested = Signal(str, int, object)  # gcode, start_line, checkpoint|None
    _pause_stream_requested = Signal()
    _resume_stream_requested = Signal()
    _abort_stream_requested = Signal()
    _query_settings_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._connected = False
        self._latest_status: Status | None = None

        self._thread = QThread(self)
        self._worker = MachineWorker()
        self._worker.moveToThread(self._thread)

        # Worker → controller.
        self._worker.statusReceived.connect(self._on_status)
        self._worker.responseReceived.connect(self._on_response)
        self._worker.welcomeReceived.connect(self._on_welcome)
        self._worker.workerError.connect(self.error.emit)
        self._worker.portOpened.connect(self._on_port_opened)
        self._worker.portClosed.connect(self._on_port_closed)

        # Controller → worker.
        self._open_requested.connect(self._worker.open_port)
        self._close_requested.connect(self._worker.close_port)
        self._send_line.connect(self._worker.write_line)
        self._send_realtime.connect(self._worker.write_realtime)
        self._start_stream_requested.connect(self._worker.start_stream)
        self._pause_stream_requested.connect(self._worker.stream_pause)
        self._resume_stream_requested.connect(self._worker.stream_resume)
        self._abort_stream_requested.connect(self._worker.stream_abort)
        self._query_settings_requested.connect(self._worker.query_settings)

        # Worker stream signals → forward to UI.
        self._worker.streamStarted.connect(self.streamStarted.emit)
        self._worker.streamProgress.connect(self.streamProgress.emit)
        self._worker.streamCompleted.connect(self.streamCompleted.emit)
        self._worker.streamAborted.connect(self.streamAborted.emit)
        self._worker.toolChangeRequired.connect(self.toolChangeRequired.emit)
        self._worker.settingsReceived.connect(self.settingsReceived.emit)

        self._thread.start()
        # Belt-and-suspenders cleanup: if the process exits via a path that
        # bypasses MainWindow.closeEvent and QApplication.aboutToQuit (e.g.
        # a script that constructs MainWindow and exits without app.exec()),
        # atexit still fires and stops the QThread before Qt's finalizer
        # would SIGABRT on a still-running thread.
        atexit.register(self.shutdown)

    # --------------------------------------------------------- introspection

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def latest_status(self) -> Status | None:
        return self._latest_status

    # ------------------------------------------------------ UI-callable slots

    def connect_to(self, port: str) -> None:
        if self._connected:
            return
        # Refuse if any other window in this process already owns the port.
        # The OS would also reject the second open, but a clear in-app message
        # is friendlier than the generic "Permission denied / device busy".
        other = MachineController._active_connection
        if other is not None and other is not self:
            self.connectionChanged.emit(
                False,
                "Another project window is already connected to the CNC. "
                "Disconnect it first."
            )
            return
        self.connectionChanged.emit(False, f"Opening {port}…")
        self._open_requested.emit(port)

    def disconnect_machine(self) -> None:
        if not self._connected:
            return
        self._close_requested.emit()

    def jog(self, axis: str, distance_mm: float, feed_mm_min: float = 1000) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user(f"Jog {axis}{distance_mm:+g} mm @ {feed_mm_min:g} mm/min")
        self._send_line.emit(cmd_jog(axis, distance_mm, feed_mm_min))

    def set_work_zero_all(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Set work zero (XYZ)")
        self._send_line.emit(cmd_set_work_zero_all())

    def set_work_zero_xy(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Set work zero (XY)")
        self._send_line.emit(cmd_set_work_zero_xy())

    def set_work_zero_z(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Set work zero (Z)")
        self._send_line.emit(cmd_set_work_zero_z())

    def goto_zero(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Goto work zero")
        # XY first (at current Z, expected to be safe), then Z = 0.
        self._send_line.emit(cmd_goto_zero_xy())
        self._send_line.emit(cmd_goto_zero_z())

    def soft_reset(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Soft reset")
        self._send_realtime.emit(SOFT_RESET)

    def feed_hold(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Feed hold")
        self._send_realtime.emit(FEED_HOLD)

    def cycle_start(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Cycle start")
        self._send_realtime.emit(CYCLE_START)

    def send_raw_line(self, line: str) -> None:
        """Escape hatch for the manual-command UI (not used by jog/zero)."""
        from cnc_inlay.control import run_log
        run_log.log_user(f"Send raw: {line.strip()}")
        self._send_line.emit(line)

    # ----------------------------------------------------------- streaming API

    def run_program(
        self,
        gcode: str,
        start_line: int = 0,
        checkpoint: Checkpoint | None = None,
    ) -> None:
        from cnc_inlay.control import run_log
        n_lines = sum(1 for _ in gcode.splitlines())
        if start_line > 0:
            run_log.log_user(
                f"Run from cursor (start_line={start_line}, total={n_lines})"
            )
        else:
            run_log.log_user(f"Run program ({n_lines} lines)")
        self._start_stream_requested.emit(gcode, start_line, checkpoint)

    def stream_pause(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Pause stream")
        self._pause_stream_requested.emit()

    def stream_resume(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Resume stream")
        self._resume_stream_requested.emit()

    def stream_abort(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Abort stream")
        self._abort_stream_requested.emit()

    def query_settings(self) -> None:
        from cnc_inlay.control import run_log
        run_log.log_user("Query GRBL settings ($$)")
        self._query_settings_requested.emit()

    def shutdown(self) -> None:
        # Idempotent — multiple shutdown paths (closeEvent, aboutToQuit,
        # atexit) may all call this; only the first does real work.
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        # Release the process-level connection lock so a future window can
        # take over (only relevant on closeEvent — at process exit nobody is
        # left to take it anyway).
        if MachineController._active_connection is self:
            MachineController._active_connection = None
        try:
            self._close_requested.emit()
        except Exception:
            pass
        try:
            self._thread.quit()
            if not self._thread.wait(2000):
                # Thread refused to quit (worker stuck in a blocking call,
                # serial read, etc.). Forcibly terminate so Qt doesn't abort
                # the whole process when the QThread destructor runs at
                # interpreter finalization.
                self._thread.terminate()
                self._thread.wait(1000)
        except Exception:
            pass

    # ------------------------------------------------------- worker callbacks

    def _on_port_opened(self, port: str) -> None:
        self._connected = True
        MachineController._active_connection = self
        self.connectionChanged.emit(True, f"Connected to {port}")

    def _on_port_closed(self, reason: str) -> None:
        self._connected = False
        self._latest_status = None
        if MachineController._active_connection is self:
            MachineController._active_connection = None
        self.connectionChanged.emit(False, reason)

    def _on_status(self, status: Status) -> None:
        self._latest_status = status
        self.statusChanged.emit(status)

    def _on_response(self, resp: Response) -> None:
        self.response.emit(resp)
        if resp.kind == ResponseKind.ALARM:
            self.alarm.emit(resp.code, alarm_text(resp.code))
        elif resp.kind == ResponseKind.ERROR:
            self.error.emit(error_text(resp.code))

    def _on_welcome(self) -> None:
        # GRBL booted/reset; nothing extra to do for v1.
        pass
