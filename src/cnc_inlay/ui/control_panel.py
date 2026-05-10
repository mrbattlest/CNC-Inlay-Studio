"""Bottom-dock CNC control panel — connection, status, jog, work-zero."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


# Wheel-proof versions — Qt's default treats hover-and-scroll as value
# changes, which is dangerous on combo boxes (port selector!) and the
# simulation-speed slider. Match the behaviour we already enforce in the
# left-hand controls panel.
class _NoScrollComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class _NoScrollSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()


QComboBox = _NoScrollComboBox  # type: ignore[misc]
QSlider = _NoScrollSlider  # type: ignore[misc]

from cnc_inlay.control.controller import MachineController
from cnc_inlay.control.grbl import MachineState, Status
from cnc_inlay.control.serial_io import list_serial_ports
from cnc_inlay.ui.scrub_bar import BitSegment, ScrubBar


_STATE_COLORS = {
    MachineState.IDLE: "#3eea7a",   # green
    MachineState.RUN: "#f5a623",    # amber
    MachineState.JOG: "#3d8bff",    # blue
    MachineState.HOLD: "#f5a623",
    MachineState.ALARM: "#e0524a",  # red
    MachineState.UNKNOWN: "#9aa0a6",
}


class ControlPanel(QWidget):
    """The bottom-dock CNC control widget.

    Sections, top to bottom: connection + status header / jog grid / work-zero /
    simulate / setup-guide / run-on-machine (phase 5).
    """

    simulatePocketRequested = Signal()
    simulatePlugRequested = Signal()
    simulatePauseRequested = Signal()
    simulateStopRequested = Signal()
    simulateSpeedChanged = Signal(float)
    setupGuideRequested = Signal()
    runPocketRequested = Signal()
    runPlugRequested = Signal()
    dryRunPocketRequested = Signal()
    dryRunPlugRequested = Signal()
    # Run from a specific G-code line (selected via the scrub bar). The
    # side ("pocket" or "plug") plus the start_line index. Host opens a
    # confirmation dialog and calls start_stream(start_line=N) on the
    # controller.
    runFromLineRequested = Signal(str, int)
    # Continuous scrub-handle position. Host uses this to update the 3D
    # canvas's bit-position marker so the operator can see where they're
    # about to start cutting.
    scrubPositionChanged = Signal(str, int)  # side, line
    streamPauseRequested = Signal()
    streamResumeRequested = Signal()
    streamAbortRequested = Signal()
    grblConfigRequested = Signal()
    grblUnlockRequested = Signal()
    grblRawCommandRequested = Signal(str)

    def __init__(self, controller: MachineController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._streaming = False
        # Whether streaming is currently paused on a programmed M0 for a
        # tool change. Different from a user-initiated feed-hold pause —
        # this state should re-enable jog so the operator can re-zero Z.
        self._paused_for_tool_change = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)

        # Top status row.
        self._build_status_row()
        outer.addLayout(self._status_row)

        # Main row: jog grid + simulate box + run box.
        # (Work-zero buttons are deliberately in the Settings menu — they
        # change the machine's reference points and a misclick down here
        # would silently destroy alignment mid-job.)
        main = QHBoxLayout()
        main.setSpacing(12)
        main.addWidget(self._build_jog_box())
        main.addWidget(self._build_simulate_box(), 1)
        main.addWidget(self._build_run_box())
        outer.addLayout(main)
        outer.addWidget(self._build_console_box())

        self._wire_controller()
        self._refresh_ports()
        self._refresh_button_states()

    # ------------------------------------------------------------- top row

    def _build_status_row(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(10)

        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(260)
        self._port_combo.setToolTip(
            "USB serial port your CNC is connected to. Lunyee/3018-class "
            "machines typically show up as a CH340 device. Click ↻ to "
            "rescan after plugging in."
        )

        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setFixedWidth(28)
        self._refresh_btn.setToolTip("Refresh port list")
        self._refresh_btn.clicked.connect(self._refresh_ports)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setToolTip(
            "Open a serial connection to the selected port and start polling "
            "GRBL status. Click again to disconnect."
        )
        self._connect_btn.clicked.connect(self._on_connect_clicked)

        self._grbl_config_btn = QPushButton("GRBL Config…")
        self._grbl_config_btn.setToolTip(
            "Read GRBL settings from the machine ($$) and show the steps/mm + "
            "max feed rates. Useful when diagnosing 'cut too deep' or "
            "wrong-distance issues."
        )
        self._grbl_config_btn.clicked.connect(self.grblConfigRequested.emit)
        self._grbl_config_btn.setEnabled(False)

        self._grbl_unlock_btn = QPushButton("Unlock")
        self._grbl_unlock_btn.setToolTip(
            "Send GRBL's $X 'kill alarm lock' command. Use this if the state "
            "is Alarm and jog/run buttons aren't working."
        )
        self._grbl_unlock_btn.clicked.connect(self.grblUnlockRequested.emit)
        self._grbl_unlock_btn.setEnabled(False)

        self._grbl_raw_btn = QPushButton("Send…")
        self._grbl_raw_btn.setToolTip(
            "Send a raw G-code or GRBL command (e.g. $102=400 to fix Z steps/mm)."
        )
        self._grbl_raw_btn.clicked.connect(self._on_grbl_raw_clicked)
        self._grbl_raw_btn.setEnabled(False)

        self._state_dot = QLabel("●")
        self._state_dot.setStyleSheet(f"color: {_STATE_COLORS[MachineState.UNKNOWN]}; font-size: 16px;")
        self._state_label = QLabel("Disconnected")
        self._state_label.setObjectName("summary")

        # Stream-control buttons live up here (next to the connection status)
        # so pause / resume / abort are reachable without hunting through the
        # run box during a real cut.
        self._stream_pause_btn = QPushButton("⏸")
        self._stream_resume_btn = QPushButton("▶")
        self._stream_abort_btn = QPushButton("⏹ Abort")
        self._stream_pause_btn.setFixedWidth(34)
        self._stream_resume_btn.setFixedWidth(34)
        self._stream_pause_btn.setToolTip("Pause (feed hold)")
        self._stream_resume_btn.setToolTip("Resume (cycle start)")
        self._stream_abort_btn.setToolTip(
            "Abort (soft reset). Bit will stop and spindle will turn off."
        )
        self._stream_pause_btn.clicked.connect(self.streamPauseRequested.emit)
        self._stream_resume_btn.clicked.connect(self.streamResumeRequested.emit)
        self._stream_abort_btn.clicked.connect(self.streamAbortRequested.emit)
        for b in (self._stream_pause_btn, self._stream_resume_btn, self._stream_abort_btn):
            b.setEnabled(False)

        self._pos_label = QLabel("WPos —")
        self._pos_label.setStyleSheet("font-family: 'SF Mono','Menlo','Courier New',monospace;")
        self._pos_label.setMinimumWidth(280)

        row.addWidget(QLabel("Port:"))
        row.addWidget(self._port_combo)
        row.addWidget(self._refresh_btn)
        row.addWidget(self._connect_btn)
        row.addWidget(self._grbl_config_btn)
        row.addWidget(self._grbl_unlock_btn)
        row.addWidget(self._grbl_raw_btn)
        row.addSpacing(20)
        row.addWidget(self._state_dot)
        row.addWidget(self._state_label)
        row.addSpacing(12)
        row.addWidget(self._stream_pause_btn)
        row.addWidget(self._stream_resume_btn)
        row.addWidget(self._stream_abort_btn)
        row.addStretch(1)
        row.addWidget(self._pos_label)

        self._status_row = row

    # -------------------------------------------------------------- jog box

    def _build_jog_box(self) -> QGroupBox:
        box = QGroupBox("Jog")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # Step-size selector.
        step_row = QHBoxLayout()
        step_row.setSpacing(2)
        step_row.addWidget(QLabel("Step (mm):"))
        self._step_group = QButtonGroup(self)
        self._step_group.setExclusive(True)
        self._step_buttons: dict[float, QPushButton] = {}
        # Amber-highlight the selected step so the active jog distance is
        # unmistakable. The global theme's default checked state isn't
        # visually distinct enough — users couldn't tell at a glance
        # whether a jog button would move 0.1 mm or 10 mm.
        step_btn_style = (
            "QPushButton { padding: 4px 8px; }"
            "QPushButton:checked {"
            "  background: #f5a623; color: #1d1f24;"
            "  border: 1px solid #f5a623; font-weight: 700;"
            "}"
            "QPushButton:checked:hover { background: #ffb945; color: #1d1f24; }"
        )
        # 0.001 / 0.01 / 0.1 / 1 / 10 mm steps. The two finer ones (0.001
        # and 0.01) let the user dial in precise positioning when paper-
        # feeler-zeroing or aligning to a fixture mark — earlier the
        # smallest step was 0.1 mm which is way too coarse for those
        # workflows. Buttons get slightly narrower so all five fit on
        # the same row without crowding.
        for value in (0.001, 0.01, 0.1, 1.0, 10.0):
            # Show the literal value, not f-string :g (which would print
            # "0.001" as "0.001" but "0.01" as "0.01" and "10" as "10" —
            # consistent enough). Use a fixed-width font column for
            # alignment.
            label = f"{value:g}"
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedWidth(46)
            btn.setStyleSheet(step_btn_style)
            btn.setToolTip(
                f"Each jog button moves the bit {label} mm. "
                f"Use 0.001 / 0.01 for fine paper-feeler Z-zero work; "
                f"1 / 10 for general positioning."
            )
            self._step_group.addButton(btn)
            self._step_buttons[value] = btn
            step_row.addWidget(btn)
        self._step_buttons[1.0].setChecked(True)
        step_row.addStretch(1)
        layout.addLayout(step_row)

        # XY/Z grid.
        grid = QGridLayout()
        grid.setSpacing(4)

        def jog_btn(label: str, axis: str, sign: int, tip: str) -> QPushButton:
            btn = QPushButton(label)
            btn.setFixedSize(46, 36)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, a=axis, s=sign: self._jog(a, s))
            return btn

        self._xminus = jog_btn("X−", "X", -1, "Jog the bit left by the selected step")
        self._xplus  = jog_btn("X+", "X", +1, "Jog the bit right by the selected step")
        self._yminus = jog_btn("Y−", "Y", -1, "Jog the bit toward you by the selected step")
        self._yplus  = jog_btn("Y+", "Y", +1, "Jog the bit away from you by the selected step")
        self._zminus = jog_btn("Z−", "Z", -1, "Jog the bit down by the selected step")
        self._zplus  = jog_btn("Z+", "Z", +1, "Jog the bit up by the selected step")

        # Go to 0,0,0 — moves the bit to the existing work zero (does NOT
        # change the zero, so it lives with the jog controls instead of
        # alongside the destructive Set-zero actions).
        self._goto_zero_btn = QPushButton("⌂")
        self._goto_zero_btn.setFixedSize(46, 36)
        self._goto_zero_btn.setToolTip("Go to work zero (0, 0, 0) — does not change the zero")
        self._goto_zero_btn.clicked.connect(self._controller.goto_zero)

        # Layout: XY cross + Z column on right + home button at the cross centre.
        grid.addWidget(self._yplus, 0, 1)
        grid.addWidget(self._xminus, 1, 0)
        grid.addWidget(self._goto_zero_btn, 1, 1)
        grid.addWidget(self._xplus, 1, 2)
        grid.addWidget(self._yminus, 2, 1)
        grid.addWidget(self._zplus, 0, 3)
        grid.addWidget(self._zminus, 2, 3)
        layout.addLayout(grid)

        return box

    # ---------------------------------------------------- work-zero (menu API)

    def _confirm_set_zero(self, axes_label: str, detail: str) -> bool:
        """Block-and-confirm dialog before overwriting a work-zero. Re-zeroing
        mid-job destroys alignment, so we want a deliberate two-click action.

        Refuses outright if disconnected. While streaming, only allows the
        action when the machine is paused (M0 tool change OR user-initiated
        feed-hold) — re-zeroing Z after installing a new bit is the entire
        reason a tool-change pause exists, so blocking it would force the
        user to abort the whole run just to set Z = 0.
        """
        if not self._controller.is_connected:
            QMessageBox.information(
                self, "Not connected",
                "Connect to the CNC machine first.",
            )
            return False
        if self._streaming and not self._paused_for_tool_change:
            QMessageBox.warning(
                self, "Streaming in progress",
                "The job isn't paused. Click ⏸ Pause first, then re-zero.",
            )
            return False
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(f"Confirm Set {axes_label} = 0")
        box.setText(f"<b>Set {axes_label} work-zero to the current position?</b>")
        box.setInformativeText(
            detail
            + "<br><br>This overwrites the existing zero. If a job is "
            "paused, re-zeroing will misalign every subsequent move."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        return box.exec() == QMessageBox.Yes

    def confirm_set_xy_zero(self) -> None:
        if self._confirm_set_zero(
            "XY",
            "Sets the current X and Y machine position as the new work origin.",
        ):
            self._controller.set_work_zero_xy()

    def confirm_set_z_zero(self) -> None:
        if self._confirm_set_zero(
            "Z",
            "Sets the current Z height as the new work surface "
            "(typically just touching the wood with a paper feeler).",
        ):
            self._controller.set_work_zero_z()

    def confirm_set_all_zero(self) -> None:
        if self._confirm_set_zero(
            "all axes",
            "Sets the current X, Y, and Z position as the new work origin.",
        ):
            self._controller.set_work_zero_all()

    def confirm_set_custom_zero(self) -> None:
        """Settings → Set custom X / Y / Z… — opens the per-axis
        custom-value dialog and sends the resulting G10 L20 P1 command."""
        if not self._controller.is_connected:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Not connected", "Connect to the CNC machine first.",
            )
            return
        if self._streaming and not self._paused_for_tool_change:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Streaming in progress",
                "The job isn't paused. Click ⏸ Pause first, then re-zero.",
            )
            return
        from cnc_inlay.ui.grbl_command_dialog import SetCustomZeroDialog
        dlg = SetCustomZeroDialog(self)
        dlg.commandRequested.connect(self._controller.send_raw_line)
        dlg.exec()

    # ---------------------------------------------------------- simulate box

    def _build_simulate_box(self) -> QGroupBox:
        box = QGroupBox("Simulate cut")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # Single row: pocket / plug / pause / stop / speed slider.
        row = QHBoxLayout()
        row.setSpacing(4)
        self._sim_pocket_btn = QPushButton("▶ Pocket")
        self._sim_plug_btn = QPushButton("▶ Plug")
        self._sim_pause_btn = QPushButton("⏸")
        self._sim_stop_btn = QPushButton("⏹")
        self._sim_pocket_btn.setToolTip(
            "Simulate the pocket cut on the canvas — animated bit position, "
            "no machine connection needed."
        )
        self._sim_plug_btn.setToolTip(
            "Simulate the plug cut on the canvas — animated bit position, "
            "no machine connection needed."
        )
        self._sim_pause_btn.setToolTip("Pause the running simulation")
        self._sim_stop_btn.setToolTip("Stop the simulation and reset to the start")
        self._sim_pocket_btn.clicked.connect(self.simulatePocketRequested.emit)
        self._sim_plug_btn.clicked.connect(self.simulatePlugRequested.emit)
        self._sim_pause_btn.clicked.connect(self.simulatePauseRequested.emit)
        self._sim_stop_btn.clicked.connect(self.simulateStopRequested.emit)
        for btn in (self._sim_pocket_btn, self._sim_plug_btn):
            btn.setMinimumWidth(80)
        for btn in (self._sim_pause_btn, self._sim_stop_btn):
            btn.setFixedWidth(30)
        for btn in (self._sim_pocket_btn, self._sim_plug_btn, self._sim_pause_btn, self._sim_stop_btn):
            btn.setEnabled(False)
        row.addWidget(self._sim_pocket_btn)
        row.addWidget(self._sim_plug_btn)
        row.addWidget(self._sim_pause_btn)
        row.addWidget(self._sim_stop_btn)
        row.addSpacing(8)
        row.addWidget(QLabel("Speed:"))
        self._sim_speed_slider = QSlider(Qt.Horizontal)
        self._sim_speed_slider.setMinimum(1)
        self._sim_speed_slider.setMaximum(100)
        self._sim_speed_slider.setValue(10)
        self._sim_speed_slider.setMinimumWidth(120)
        self._sim_speed_slider.setToolTip(
            "Simulation playback speed. 1× = realtime, 100× = very fast preview."
        )
        self._sim_speed_label = QLabel("10×")
        self._sim_speed_label.setMinimumWidth(36)
        self._sim_speed_slider.valueChanged.connect(self._on_sim_speed)
        row.addWidget(self._sim_speed_slider, 1)
        row.addWidget(self._sim_speed_label)
        layout.addLayout(row)

        # Slim progress bar + status text on one line.
        self._sim_progress = QProgressBar()
        self._sim_progress.setRange(0, 1000)
        self._sim_progress.setValue(0)
        self._sim_progress.setTextVisible(False)
        self._sim_progress.setFixedHeight(8)
        layout.addWidget(self._sim_progress)
        self._sim_time_label = QLabel("Load a design and Preview to enable simulation.")
        self._sim_time_label.setObjectName("hint")
        layout.addWidget(self._sim_time_label)

        return box

    # -------------------------------------------------------------- run box

    def _build_run_box(self) -> QGroupBox:
        box = QGroupBox("On machine")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # 2×2 grid: dry runs on top row, real runs on bottom row.
        grid = QGridLayout()
        grid.setSpacing(4)

        self._dry_pocket_btn = QPushButton("👁 Dry Pocket")
        self._dry_plug_btn = QPushButton("👁 Dry Plug")
        self._run_pocket_btn = QPushButton("▶ Run Pocket")
        self._run_plug_btn = QPushButton("▶ Run Plug")

        self._dry_pocket_btn.setToolTip(
            "Run the pocket toolpath at +safe Z (no plunge, no cutting). "
            "Use this BEFORE the real cut to verify XY alignment and Z-zero."
        )
        self._dry_plug_btn.setToolTip(
            "Run the plug toolpath at +safe Z (no plunge, no cutting). "
            "Use this BEFORE the real cut to verify XY alignment and Z-zero."
        )
        self._run_pocket_btn.setToolTip(
            "Stream the pocket G-code to the machine. Connect first and click Preview."
        )
        self._run_plug_btn.setToolTip(
            "Stream the plug G-code to the machine. Connect first and click Preview."
        )

        self._dry_pocket_btn.clicked.connect(self.dryRunPocketRequested.emit)
        self._dry_plug_btn.clicked.connect(self.dryRunPlugRequested.emit)
        self._run_pocket_btn.clicked.connect(self.runPocketRequested.emit)
        self._run_plug_btn.clicked.connect(self.runPlugRequested.emit)
        for b in (self._dry_pocket_btn, self._dry_plug_btn,
                  self._run_pocket_btn, self._run_plug_btn):
            b.setEnabled(False)

        grid.addWidget(self._dry_pocket_btn, 0, 0)
        grid.addWidget(self._dry_plug_btn, 0, 1)
        grid.addWidget(self._run_pocket_btn, 1, 0)
        grid.addWidget(self._run_plug_btn, 1, 1)
        layout.addLayout(grid)

        # Coloured scrub bar replacing the old plain progress bar. Each
        # bit gets its own coloured band; live progress dims the streamed
        # portion; a draggable amber handle picks a start-from line.
        self._scrub_bar = ScrubBar()
        layout.addWidget(self._scrub_bar)
        # The scrub bar shows whichever side the operator most recently
        # interacted with via Run / Dry / scrub. Tracked so signals know
        # whether to start the pocket or plug program.
        self._scrub_side: str = "pocket"
        self._scrub_programs: dict[str, tuple[int, list[BitSegment]]] = {}
        self._scrub_bar.scrubMoved.connect(
            lambda line: self.scrubPositionChanged.emit(self._scrub_side, line)
        )

        # Run-from-cursor controls live just under the scrub bar.
        scrub_row = QHBoxLayout()
        scrub_row.setSpacing(4)
        self._scrub_side_label = QLabel("Showing: —")
        self._scrub_side_label.setObjectName("hint")
        scrub_row.addWidget(self._scrub_side_label)
        scrub_row.addStretch(1)
        self._show_pocket_btn = QPushButton("Show Pocket")
        self._show_plug_btn = QPushButton("Show Plug")
        self._show_pocket_btn.setToolTip("Load the pocket program into the scrub bar")
        self._show_plug_btn.setToolTip("Load the plug program into the scrub bar")
        self._show_pocket_btn.clicked.connect(lambda: self._show_scrub_side("pocket"))
        self._show_plug_btn.clicked.connect(lambda: self._show_scrub_side("plug"))
        for b in (self._show_pocket_btn, self._show_plug_btn):
            b.setEnabled(False)
            b.setMaximumWidth(110)
        scrub_row.addWidget(self._show_pocket_btn)
        scrub_row.addWidget(self._show_plug_btn)
        self._snap_next_bit_btn = QPushButton("⏭ Next bit")
        self._snap_next_bit_btn.setToolTip(
            "Snap the scrub handle to the next bit-change point"
        )
        self._snap_next_bit_btn.setEnabled(False)
        self._snap_next_bit_btn.setMaximumWidth(96)
        self._snap_next_bit_btn.clicked.connect(self._on_snap_to_next_bit)
        scrub_row.addWidget(self._snap_next_bit_btn)
        self._run_from_cursor_btn = QPushButton("▶ Run from cursor")
        self._run_from_cursor_btn.setToolTip(
            "Start cutting at the scrub-handle line. Make sure the right bit "
            "is installed and Z is zeroed for that bit."
        )
        self._run_from_cursor_btn.setEnabled(False)
        self._run_from_cursor_btn.clicked.connect(self._on_run_from_cursor)
        scrub_row.addWidget(self._run_from_cursor_btn)
        layout.addLayout(scrub_row)

        # Status label + Setup Guide on one row to save vertical space.
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self._stream_status_label = QLabel("Idle")
        self._stream_status_label.setObjectName("hint")
        bottom.addWidget(self._stream_status_label, 1)
        self._setup_btn = QPushButton("📋 Setup Guide…")
        self._setup_btn.setToolTip("Open the pre-cut walkthrough.")
        self._setup_btn.setEnabled(False)
        self._setup_btn.clicked.connect(self.setupGuideRequested.emit)
        bottom.addWidget(self._setup_btn)
        layout.addLayout(bottom)

        return box

    # ----------------------------------------------------------- scrub bar

    def set_scrub_program(
        self, side: str, total_lines: int, segments: list[BitSegment]
    ) -> None:
        """Host calls this after Preview to register a side's program with
        the scrub bar. Both sides can be registered; ``_show_scrub_side``
        picks which one is currently visible."""
        self._scrub_programs[side] = (total_lines, segments)
        self._show_pocket_btn.setEnabled("pocket" in self._scrub_programs)
        self._show_plug_btn.setEnabled("plug" in self._scrub_programs)
        # Default to pocket on first load.
        if not self._scrub_bar.total_lines or self._scrub_side == side:
            self._show_scrub_side(side)

    def clear_scrub_programs(self) -> None:
        self._scrub_programs.clear()
        self._scrub_bar.clear()
        self._scrub_side_label.setText("Showing: —")
        self._show_pocket_btn.setEnabled(False)
        self._show_plug_btn.setEnabled(False)
        self._snap_next_bit_btn.setEnabled(False)
        self._run_from_cursor_btn.setEnabled(False)

    def _show_scrub_side(self, side: str) -> None:
        if side not in self._scrub_programs:
            return
        self._scrub_side = side
        total, segs = self._scrub_programs[side]
        self._scrub_bar.set_program(total, segs)
        self._scrub_side_label.setText(
            f"Showing: <b>{side.title()}</b> — {total:,} lines, "
            f"{len(segs)} bit{'s' if len(segs) != 1 else ''}"
        )
        self._scrub_side_label.setTextFormat(Qt.RichText)
        self._snap_next_bit_btn.setEnabled(True)
        self._run_from_cursor_btn.setEnabled(
            not self._streaming and self._controller.is_connected
        )

    def _on_snap_to_next_bit(self) -> None:
        nxt = self._scrub_bar.next_bit_boundary(self._scrub_bar.scrub_line)
        if nxt is not None:
            self._scrub_bar.set_scrub_line(nxt)

    def _on_run_from_cursor(self) -> None:
        self.runFromLineRequested.emit(self._scrub_side, self._scrub_bar.scrub_line)

    # ------------------------------------------------------------ console

    def _build_console_box(self) -> QGroupBox:
        # Checkable group box: clicking the title checkbox collapses/expands
        # the console. Default is collapsed so it doesn't eat sidebar height.
        box = QGroupBox("GRBL console")
        box.setCheckable(True)
        box.setChecked(False)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        self._console_view = QPlainTextEdit()
        self._console_view.setReadOnly(True)
        self._console_view.setMaximumBlockCount(400)
        self._console_view.setStyleSheet(
            "QPlainTextEdit { background:#15171b; color:#d8dce2; "
            "font-family:'SF Mono','Menlo','Courier New',monospace; "
            "font-size:11px; border:1px solid #3a3f47; }"
        )
        self._console_view.setFixedHeight(110)
        self._console_view.setPlaceholderText(
            "Connect to the machine — sent commands and GRBL responses will appear here."
        )
        layout.addWidget(self._console_view)
        # Wire toggle to hide/show contents (default = hidden).
        self._console_box = box
        self._console_view.setVisible(False)
        box.toggled.connect(self._on_console_toggled)
        return box

    def _on_console_toggled(self, checked: bool) -> None:
        self._console_view.setVisible(checked)
        # Hint the dock to recompute its size now that contents changed.
        if not checked:
            self._console_box.setMaximumHeight(self._console_box.fontMetrics().height() + 16)
        else:
            self._console_box.setMaximumHeight(16777215)  # Qt's QWIDGETSIZE_MAX

    def append_console_sent(self, text: str) -> None:
        self._console_view.appendHtml(
            f"<span style='color:#9aa0a6;'>&gt; </span>"
            f"<span style='color:#f5a623;'>{_escape_html(text)}</span>"
        )

    def append_console_received(self, text: str, kind: str = "other") -> None:
        color = {
            "ok": "#3eea7a",
            "error": "#e0524a",
            "alarm": "#e0524a",
            "welcome": "#bf3aff",
            "setting": "#3d8bff",
        }.get(kind, "#d8dce2")
        self._console_view.appendHtml(
            f"<span style='color:{color};'>{_escape_html(text)}</span>"
        )

    # ----------------------------------------------------------- behaviour

    def _wire_controller(self) -> None:
        self._controller.connectionChanged.connect(self._on_connection_changed)
        self._controller.statusChanged.connect(self._on_status_changed)
        self._controller.error.connect(self._on_error)
        self._controller.alarm.connect(self._on_alarm)

    def _refresh_ports(self) -> None:
        self._port_combo.clear()
        for port in list_serial_ports():
            self._port_combo.addItem(port.display(), port.device)
        if self._port_combo.count() == 0:
            self._port_combo.addItem("No serial ports detected", None)

    def _on_connect_clicked(self) -> None:
        if self._controller.is_connected:
            self._controller.disconnect_machine()
        else:
            port = self._port_combo.currentData()
            if port is None:
                self._on_error("No serial port selected.")
                return
            self._controller.connect_to(port)

    def _selected_step_mm(self) -> float:
        for value, btn in self._step_buttons.items():
            if btn.isChecked():
                return value
        return 1.0

    def _jog(self, axis: str, sign: int) -> None:
        step = self._selected_step_mm() * sign
        # Slower jog feed for Z so the bit doesn't slam.
        feed = 600 if axis == "Z" else 1500
        self._controller.jog(axis, step, feed)

    @Slot(bool, str)
    def _on_connection_changed(self, connected: bool, message: str) -> None:
        self._connect_btn.setText("Disconnect" if connected else "Connect")
        self._port_combo.setEnabled(not connected)
        self._refresh_btn.setEnabled(not connected)
        for btn in (self._grbl_config_btn, self._grbl_unlock_btn, self._grbl_raw_btn):
            btn.setEnabled(connected)
        # Pause/Resume/Abort: always available when connected so the user can
        # recover from a stale machine state (e.g. Hold after the host slept).
        for btn in (self._stream_pause_btn, self._stream_resume_btn, self._stream_abort_btn):
            btn.setEnabled(connected)
        if connected:
            self._state_label.setText(message)
        else:
            self._state_label.setText(message or "Disconnected")
            self._update_state_dot(MachineState.UNKNOWN)
            self._pos_label.setText("WPos —")
        self._refresh_button_states()

    @Slot(object)
    def _on_status_changed(self, status: Status) -> None:
        self._state_label.setText(f"State: {status.state.value}")
        self._update_state_dot(status.state)
        wpos = status.effective_wpos or status.mpos
        if wpos is not None:
            self._pos_label.setText(
                f"WPos  X {wpos.x:7.3f}   Y {wpos.y:7.3f}   Z {wpos.z:7.3f}"
            )
        # Recovery-jog gate: when a stream is paused (Hold) — whether from
        # a programmed M0 tool change or a user-initiated feed-hold — let
        # the operator jog so they can lift the bit, swap a broken one,
        # re-zero Z, etc. before clicking Resume. When motion resumes
        # (Run/Jog/Idle), lock jog out again so a stray click can't crash
        # into the running cut.
        if self._streaming:
            should_allow = status.state in (MachineState.HOLD, MachineState.ALARM)
            if should_allow != self._paused_for_tool_change:
                self.set_paused_for_tool_change(should_allow)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._state_label.setText(f"⚠ {message}")

    @Slot(int, str)
    def _on_alarm(self, code: int, message: str) -> None:
        self._state_label.setText(f"⚠ ALARM:{code} — {message}")
        self._update_state_dot(MachineState.ALARM)

    def _update_state_dot(self, state: MachineState) -> None:
        color = _STATE_COLORS.get(state, _STATE_COLORS[MachineState.UNKNOWN])
        self._state_dot.setStyleSheet(f"color: {color}; font-size: 16px;")

    def _refresh_button_states(self) -> None:
        connected = self._controller.is_connected
        for btn in (
            self._xminus, self._xplus, self._yminus, self._yplus,
            self._zminus, self._zplus,
            self._goto_zero_btn,
        ):
            btn.setEnabled(connected)

    # ----------------------------------------------------------- simulator API

    def _on_grbl_raw_clicked(self) -> None:
        # Use the dropdown-driven command builder rather than a bare
        # text prompt — picks the right G-code line from a list of
        # common commands and surfaces only the parameters that command
        # needs (RPM for M3, dwell seconds for G4, X/Y/Z for G0/G1, etc.).
        # Falling back to the raw-text option is still available inside
        # the dialog as the "(Custom raw command…)" entry.
        from cnc_inlay.ui.grbl_command_dialog import SendCommandDialog
        dlg = SendCommandDialog(self)
        dlg.commandRequested.connect(self.grblRawCommandRequested.emit)
        dlg.exec()

    def _on_sim_speed(self, value: int) -> None:
        self._sim_speed_label.setText(f"{value}×")
        self.simulateSpeedChanged.emit(float(value))

    def set_simulation_available(
        self, available: bool, pocket_seconds: float = 0.0, plug_seconds: float = 0.0
    ) -> None:
        """Enable simulate buttons and seed the time hint after a Preview."""
        self._sim_pocket_btn.setEnabled(available)
        self._sim_plug_btn.setEnabled(available)
        self._setup_btn.setEnabled(available)
        if available:
            self._sim_time_label.setText(
                f"Pocket: ~{_fmt_seconds(pocket_seconds)}   •   "
                f"Plug: ~{_fmt_seconds(plug_seconds)}"
            )
        else:
            self._sim_time_label.setText(
                "Load a design and Preview to enable simulation."
            )

    def set_simulation_running(self, running: bool) -> None:
        """Toggle pause/stop button availability while a sim is animating."""
        self._sim_pause_btn.setEnabled(running)
        self._sim_stop_btn.setEnabled(running)

    def update_simulation_time(self, elapsed_s: float, total_s: float) -> None:
        if total_s <= 0:
            return
        self._sim_progress.setValue(int(elapsed_s / total_s * 1000))
        self._sim_time_label.setText(
            f"{_fmt_seconds(elapsed_s)} / {_fmt_seconds(total_s)}"
        )

    def reset_simulation_progress(self) -> None:
        self._sim_progress.setValue(0)

    # --------------------------------------------------------- streaming API

    def set_run_available(self, has_result: bool) -> None:
        """Enable Run buttons when a Preview result exists AND the machine is connected."""
        connected = self._controller.is_connected
        for btn in (self._run_pocket_btn, self._run_plug_btn,
                    self._dry_pocket_btn, self._dry_plug_btn):
            btn.setEnabled(has_result and connected)
        # Run-from-cursor is also gated on having a program loaded into
        # the scrub bar, not just having a result.
        self._run_from_cursor_btn.setEnabled(
            has_result and connected
            and self._scrub_bar.total_lines > 0
            and not self._streaming
        )

    def set_streaming(self, streaming: bool) -> None:
        # While streaming, lock out jog/zero/run/disconnect — the only safe
        # actions are pause/resume/abort. Pause/Resume/Abort stay available
        # any time we're connected, so the user can recover from a stale
        # Hold even if the streamer state has been cleared.
        self._streaming = streaming
        # Leaving tool-change-pause mode whenever streaming state changes.
        self._paused_for_tool_change = False
        for btn in (self._run_pocket_btn, self._run_plug_btn,
                    self._dry_pocket_btn, self._dry_plug_btn):
            btn.setEnabled(not streaming and self._controller.is_connected)
        for btn in (
            self._xminus, self._xplus, self._yminus, self._yplus,
            self._zminus, self._zplus,
            self._goto_zero_btn,
        ):
            btn.setEnabled(self._controller.is_connected and not streaming)
        for btn in (self._stream_pause_btn, self._stream_resume_btn, self._stream_abort_btn):
            btn.setEnabled(self._controller.is_connected)
        self._connect_btn.setEnabled(not streaming)
        # Run-from-cursor obeys the same gate as the regular Run buttons.
        self._run_from_cursor_btn.setEnabled(
            not streaming and self._controller.is_connected
            and self._scrub_bar.total_lines > 0
        )

    def set_paused_for_tool_change(self, paused: bool) -> None:
        """Mid-stream M0 tool-change mode. Re-enable jog so the operator can
        navigate the bit to uncut wood and re-zero Z, while keeping Run-on-
        machine buttons disabled (the existing program is still streaming —
        we're just paused inside it).
        """
        self._paused_for_tool_change = paused
        # Jog buttons → enabled when paused, disabled when actively cutting.
        for btn in (
            self._xminus, self._xplus, self._yminus, self._yplus,
            self._zminus, self._zplus,
            self._goto_zero_btn,
        ):
            btn.setEnabled(self._controller.is_connected and (paused or not self._streaming))

    def set_stream_progress(self, line: int, total: int) -> None:
        if total <= 0:
            self._scrub_bar.set_progress(0)
            self._stream_status_label.setText("Idle")
            return
        # Forward to the scrub bar so the dim live-progress overlay grows
        # as the stream advances. Scrub bar pulls its handle along with
        # progress so the operator can't pick a line behind the cursor.
        self._scrub_bar.set_progress(line)
        self._stream_status_label.setText(f"Line {line:,} / {total:,}  ({line/total*100:.0f}%)")

    def set_stream_status(self, text: str) -> None:
        self._stream_status_label.setText(text)

    def show_tool_change_banner(self, bit_name: str) -> None:
        """Highlight the streaming-status label as a yellow tool-change banner.

        The G-code will pause on the matching M0 in a moment; the user must
        install the named bit, jog to the wood surface, click Set Z = 0,
        then click ▶ Resume to continue.
        """
        self._stream_status_label.setStyleSheet(
            "background:#3d2f10; color:#f5a623; "
            "border:1px solid #f5a623; border-radius:4px; "
            "padding:4px 8px; font-weight:600;"
        )
        self._stream_status_label.setText(
            f"⏸ TOOL CHANGE — install {bit_name}, jog to surface, "
            f"set Z = 0, then ▶ Resume"
        )

    def clear_tool_change_banner(self) -> None:
        """Restore the stream-status label to its normal style."""
        self._stream_status_label.setStyleSheet("")
        # Caller will set fresh text next.


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt_seconds(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"
