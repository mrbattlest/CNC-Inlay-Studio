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
    # Fired when GRBL stops responding mid-stream — no [rx] for the
    # configured silence threshold. Carries (silence_seconds,
    # last_known_wpos_or_None) so the UI can offer to restore the
    # work-zero offsets after the user power-cycles the controller.
    grblSilenceDetected = Signal(float, object)

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
        # Silence detector: timestamp (monotonic seconds) of the last byte
        # we read from GRBL. Refreshed on every status report and ack.
        # A separate timer fires periodically and declares the connection
        # lost when this gets stale during a stream.
        import time as _time
        self._last_rx_monotonic: float = _time.monotonic()
        self._silence_timer: QTimer | None = None
        self._silence_threshold_s: float = 5.0
        self._silence_already_fired: bool = False
        # Last-known work-coordinate position (from a recent status
        # report). Persisted into the checkpoint every save tick so we
        # can offer to restore work-zero on next reconnect after a
        # silence event.
        self._last_wpos: Position | None = None

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
        import time as _time
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
        # Silence detector: armed only while streaming. Reset the
        # last-rx clock now (so the threshold isn't immediately tripped
        # if we just connected) and start the check timer at 1 s.
        self._last_rx_monotonic = _time.monotonic()
        self._silence_already_fired = False
        self._silence_timer = QTimer(self)
        self._silence_timer.setInterval(1000)
        self._silence_timer.timeout.connect(self._check_grbl_silence)
        self._silence_timer.start()
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
        """Resume a paused stream.

        Two cases:
          * **M0 tool-change halt** — the streamer is parked just before a
            programmed M0 that was never sent to GRBL (GRBL is Idle). Skip
            the M0 and resume feeding from the next bit's spindle-on. No
            cycle-start needed; GRBL was never in Hold.
          * **Manual feed-hold / recovery** — send GRBL cycle-start (`~`)
            to clear the hold, and un-pause the streamer.

        Works whether or not a streamer is active, so the user can still
        clear a stale Hold after a connection hiccup.
        """
        if self._serial is None:
            return
        from cnc_inlay.control import run_log
        if self._streamer is not None and self._streamer.at_tool_change:
            self._pending_tool_change_bit = None
            self._streamer.resume_past_tool_change()
            run_log.log_event(
                "tool-change resume — skipping M0, continuing stream"
            )
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
        if self._silence_timer is not None:
            self._silence_timer.stop()
            self._silence_timer = None
        self._streamer = None
        self._pending_tool_change_bit = None
        from cnc_inlay.control import run_log
        run_log.log_event(f"stream stopped: {reason}")
        # Keep the checkpoint on disk after an abort/error so the user can
        # recover. Only clear on clean completion.
        self.streamAborted.emit(reason)

    def _check_grbl_silence(self) -> None:
        """Fired by the silence-detector timer (1 Hz). When the time
        since the last byte we read from GRBL exceeds the silence
        threshold during an active stream, we declare the connection
        lost. The user typically can't recover without power-cycling
        the controller, and any further G-code we ship would just be
        written into a black hole — better to halt the streamer
        immediately, log the event, and surface a banner with recovery
        instructions.

        We snapshot the most recent WPos into the checkpoint at the
        same time so the user can restore the work-zero offsets to
        match the bit's current physical position after the controller
        comes back. That avoids the manual re-zero step that's hard to
        do with high enough precision to keep a multi-bit cut aligned.
        """
        if self._streamer is None or self._silence_timer is None:
            return
        if self._silence_already_fired:
            return
        import time as _time
        gap = _time.monotonic() - self._last_rx_monotonic
        if gap < self._silence_threshold_s:
            return
        # GRBL has gone silent. Capture the last known position into
        # the checkpoint and persist it before any state cleanup, so
        # even an immediate process death wouldn't lose it.
        self._silence_already_fired = True
        from cnc_inlay.control import run_log
        wpos = self._last_wpos
        wpos_str = (
            f"({wpos.x:.3f}, {wpos.y:.3f}, {wpos.z:.3f})"
            if wpos is not None else "(unknown)"
        )
        run_log.log_event(
            f"GRBL silence detected after {gap:.1f}s — "
            f"last known WPos {wpos_str}"
        )
        if self._checkpoint is not None:
            self._checkpoint.grbl_silenced = True
            if wpos is not None:
                self._checkpoint.last_wpos_x_mm = float(wpos.x)
                self._checkpoint.last_wpos_y_mm = float(wpos.y)
                self._checkpoint.last_wpos_z_mm = float(wpos.z)
            if self._cached_wco is not None:
                self._checkpoint.last_wco_x_mm = float(self._cached_wco.x)
                self._checkpoint.last_wco_y_mm = float(self._cached_wco.y)
                self._checkpoint.last_wco_z_mm = float(self._cached_wco.z)
            try:
                save_checkpoint(self._checkpoint)
            except Exception:
                pass
        self.grblSilenceDetected.emit(gap, wpos)
        self._stop_stream(f"GRBL silent for {gap:.1f}s — connection lost")

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
        # Detect a programmed-M0 tool-change halt. The streamer parks
        # itself *before* the M0 and never sends it — sending M0 would
        # put GRBL into Hold, where jog ($J=) and re-zero (G10) are both
        # rejected, which is exactly what the operator needs during a
        # bit swap. With the M0 withheld, GRBL drains its buffer and
        # settles to Idle. We stash the bit name now and defer the UI
        # prompt until GRBL actually reaches Idle (planner drained), so
        # the operator isn't told to swap a still-spinning bit.
        if (
            self._streamer.at_tool_change
            and self._pending_tool_change_bit is None
        ):
            self._pending_tool_change_bit = self._streamer.tool_change_at[
                self._streamer.cursor
            ]
            run_log.log_event(
                f"M0 tool-change halt — streamer parked before M0 "
                f"for {self._pending_tool_change_bit} "
                f"(GRBL stays Idle so jog + Set Z=0 work)"
            )
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
        # Persist the most recent WPos every tick so a sudden GRBL
        # silence event has a fresh fallback to restore from on next
        # reconnect.
        if self._last_wpos is not None:
            self._checkpoint.last_wpos_x_mm = float(self._last_wpos.x)
            self._checkpoint.last_wpos_y_mm = float(self._last_wpos.y)
            self._checkpoint.last_wpos_z_mm = float(self._last_wpos.z)
        if self._cached_wco is not None:
            self._checkpoint.last_wco_x_mm = float(self._cached_wco.x)
            self._checkpoint.last_wco_y_mm = float(self._cached_wco.y)
            self._checkpoint.last_wco_z_mm = float(self._cached_wco.z)
        save_checkpoint(self._checkpoint)

    # -------------------------------------------------------------- private

    def _tick_read(self) -> None:
        if self._serial is None:
            return
        from cnc_inlay.control import run_log
        import time as _time
        for _ in range(64):  # cap iterations to avoid starving the event loop
            line = self._serial.read_line()
            if not line:
                break
            # Any byte we receive — status, ok, error, message, anything —
            # counts as proof GRBL is alive. Refresh the silence timer.
            self._last_rx_monotonic = _time.monotonic()
            if line.startswith("<") and line.endswith(">"):
                # Status reports get their own [status] tag so users can
                # share them for position-tracking debugging without us
                # having to enable a debug mode. They're frequent (5 Hz)
                # but each is one short line — readable.
                run_log.log_status(line)
                status = parse_status(line)
                if status is not None:
                    # Cache fresh WCO when GRBL reports one; otherwise inject
                    # the last known one so effective_wpos stays consistent.
                    if status.wco is not None:
                        self._cached_wco = status.wco
                    elif self._cached_wco is not None and status.mpos is not None:
                        status = replace(status, wco=self._cached_wco)
                    # Track the most recent WPos for the silence-detector
                    # checkpoint. Pulled from effective_wpos so we get
                    # the same value the UI's position cone uses.
                    eff = status.effective_wpos
                    if eff is not None:
                        self._last_wpos = eff
                    self.statusReceived.emit(status)
                    # The streamer parked before the M0 (it's never sent),
                    # so GRBL drains its planner and settles to Idle with
                    # the spindle stopped (the preceding M5 ran). The
                    # moment GRBL reaches Idle it's safe — and possible —
                    # to swap the bit, jog, and re-zero Z. Emit the
                    # deferred tool-change prompt now.
                    if (
                        self._pending_tool_change_bit is not None
                        and status.state == MachineState.IDLE
                    ):
                        bit = self._pending_tool_change_bit
                        self._pending_tool_change_bit = None
                        self.toolChangeRequired.emit(bit)
                continue
            # Non-status line — log to [rx] for the run log.
            run_log.log_rx(line)
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
    grblSilenceDetected = Signal(float, object)

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
        self._worker.grblSilenceDetected.connect(self.grblSilenceDetected.emit)

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
