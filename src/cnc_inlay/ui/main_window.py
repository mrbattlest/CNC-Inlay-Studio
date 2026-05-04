"""Main window: assembles the controls sidebar, preview canvas, status bar."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QCoreApplication, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QStackedWidget,
    QTabWidget,
)

from cnc_inlay.control.checkpoint import (
    Checkpoint,
    clear_checkpoint,
    find_resume_line,
    load_checkpoint,
)
from cnc_inlay.control.controller import MachineController
from cnc_inlay.control.sleep_inhibitor import SleepInhibitor
from cnc_inlay.core.job import Bit, Design, InlayParams, Job, Material
from cnc_inlay.core.pipeline import PipelineResult, run_pipeline
from cnc_inlay.core.project import ProjectFile, load_project, read_svg_content, save_project
from cnc_inlay.core.units import Units, get_saved_units, set_saved_units
from cnc_inlay.ingest.svg_loader import load_svg
from cnc_inlay.presets.bits import load_bits
from cnc_inlay.presets.materials import load_materials
from cnc_inlay.sim.simulator import Simulator, build_segments, total_duration_seconds
from cnc_inlay.ui.canvas import PreviewCanvas
from cnc_inlay.ui.canvas3d import Canvas3D
from cnc_inlay.ui.finished_canvas import FinishedCanvas
from cnc_inlay.ui.control_panel import ControlPanel
from cnc_inlay.ingest.svg_loader import load_svg_from_string
from cnc_inlay.ui.background_task import run_with_progress
from cnc_inlay.ui.controls import ControlsPanel, ask_design_path
from cnc_inlay.ui.generate_dialog import GenerateDialog
from cnc_inlay.ui.settings_dialog import PreferencesDialog
from cnc_inlay.ui.setup_dialog import SetupGuide
from cnc_inlay.ui.tool_change_dialog import ToolChangeDialog
from PySide6.QtWidgets import QFileDialog


def _escape_html_simple(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _dry_run_gcode(gcode: str, safe_z_mm: float = 5.0) -> str:
    """Replace every plunge in the program with a move to safe Z so the bit
    traces the XY pattern without ever touching the wood. Used for sanity
    checks before a real cut.
    """
    import re

    out_lines: list[str] = []
    safe_z_str = f"{safe_z_mm:.3f}"
    z_pattern = re.compile(r"Z(-?[\d.]+)")
    for line in gcode.splitlines():
        if line.startswith("(") or not line.strip():
            out_lines.append(line)
            continue
        replaced = z_pattern.sub(f"Z{safe_z_str}", line)
        out_lines.append(replaced)
    return "\n".join(out_lines) + "\n"


def _format_clock(seconds: float) -> str:
    """Render a duration as Hh Mm or Mm Ss, never both. Negatives clamp to 0."""
    seconds = max(0.0, seconds)
    if seconds >= 3600:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m:02d}m"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s:02d}s"


def _format_duration(cut_mm: float, air_mm: float, feed_mm_min: float) -> str:
    cut_min = cut_mm / max(feed_mm_min, 1) if feed_mm_min else 0
    # Air moves (G0) happen at machine rapid; assume ~3000 mm/min as a rough estimate.
    air_min = air_mm / 3000.0
    total_seconds = (cut_min + air_min) * 60
    minutes = int(total_seconds // 60)
    seconds = int(total_seconds % 60)
    return f"{minutes}m {seconds:02d}s"


# ---------------------------------------------------------- toolpath caching

def _serialise_pipeline_result(result) -> dict:
    """Convert a PipelineResult to JSON-friendly nested dicts. Stored in the
    project file so reopen doesn't have to re-run the (slow) pipeline. The
    inverse is _deserialise_pipeline_result, called on project open after
    the cache_signature matches.
    """
    def _serialise_side(side) -> dict:
        return {
            "chains_full": side.chains_full,
            "gcode": side.gcode,
            "cut_length_mm": side.cut_length_mm,
            "air_length_mm": side.air_length_mm,
            # bit_chains carries Bit objects; we only need the bit's name
            # so we can look it up in the live bit library on reload.
            "bit_chains": [
                {
                    "bit_name": bit.name,
                    "chains": chains,
                    "role": role,
                    "duration_s": duration_s,
                }
                for (bit, chains, role, duration_s) in (side.bit_chains or [])
            ],
        }

    finished_geom_wkb = None
    if result.finished_geometry is not None:
        try:
            finished_geom_wkb = result.finished_geometry.wkb_hex
        except Exception:
            finished_geom_wkb = None

    return {
        "pocket": _serialise_side(result.pocket),
        "plug": _serialise_side(result.plug),
        "finished_geometry_wkb_hex": finished_geom_wkb,
    }


def _deserialise_pipeline_result(d: dict, bit_library: list):
    """Inverse of _serialise_pipeline_result. Returns a PipelineResult with
    pocket+plug Sides + finished_geometry. Coverage / fragility / stock-fit
    / recommendation are NOT cached — they're cheap to recompute and depend
    on the live design polygon.
    """
    from cnc_inlay.core.pipeline import PipelineResult, Side
    from shapely import wkb as _wkb
    by_name = {b.name: b for b in (bit_library or [])}

    def _deserialise_side(sd: dict) -> Side:
        bit_chains = []
        for entry in sd.get("bit_chains") or []:
            bit = by_name.get(entry.get("bit_name"))
            if bit is None:
                # Cached bit no longer exists in the library — abandon cache.
                raise ValueError(f"cached bit {entry.get('bit_name')!r} missing")
            chains = [
                [tuple(p) for p in chain] for chain in entry.get("chains") or []
            ]
            bit_chains.append(
                (bit, chains, entry.get("role", ""), float(entry.get("duration_s", 0.0)))
            )
        chains_full = [
            [tuple(p) for p in chain] for chain in sd.get("chains_full") or []
        ]
        return Side(
            clear_preview=chains_full,
            chains_full=chains_full,
            gcode=sd.get("gcode", ""),
            cut_length_mm=float(sd.get("cut_length_mm", 0.0)),
            air_length_mm=float(sd.get("air_length_mm", 0.0)),
            bit_chains=bit_chains,
        )

    finished_geom = None
    wkb_hex = d.get("finished_geometry_wkb_hex")
    if wkb_hex:
        try:
            finished_geom = _wkb.loads(bytes.fromhex(wkb_hex))
        except Exception:
            finished_geom = None

    return PipelineResult(
        pocket=_deserialise_side(d["pocket"]),
        plug=_deserialise_side(d["plug"]),
        coverage=None,
        recommendation=None,
        fragility=None,
        stock_fit=None,
        finished_geometry=finished_geom,
    )


def _compute_cache_signature(
    *, design, params, pocket_mat, plug_mat, endmill, bit_library
) -> str:
    """Hash every input that influences the pipeline output. The cache is
    only restored when this hash matches the current state — any edit to
    geometry, params, materials, bit selection, or speeds invalidates it.
    """
    import hashlib
    import json as _json

    h = hashlib.sha256()
    if design is not None and design.polygon is not None:
        try:
            h.update(design.polygon.wkb)
        except Exception:
            pass
    # Params: dump every dataclass field deterministically.
    try:
        from dataclasses import asdict
        params_dict = asdict(params)
        # Convert non-JSON-serialisable enums and nested dataclasses to strings.
        def _norm(v):
            if isinstance(v, dict):
                return {k: _norm(vv) for k, vv in v.items()}
            if isinstance(v, list):
                return [_norm(x) for x in v]
            if hasattr(v, "value"):  # Enum
                return v.value
            return v
        h.update(_json.dumps(_norm(params_dict), sort_keys=True).encode())
    except Exception:
        pass
    h.update((pocket_mat.name if pocket_mat else "").encode())
    h.update((plug_mat.name if plug_mat else "").encode())
    h.update((endmill.name if endmill else "").encode())
    # Bit library names (so adding/removing bits invalidates).
    h.update(",".join(sorted(b.name for b in (bit_library or []))).encode())
    return h.hexdigest()


class MainWindow(QMainWindow):
    # Class-level registry of every open MainWindow in the current process.
    # Holds a strong reference so windows aren't garbage-collected after the
    # local `window = MainWindow()` reference goes out of scope. Used by
    # "New Window" to spawn siblings, and by the CNC connect path to refuse
    # a second connection while another window already owns the serial port.
    _open_windows: list["MainWindow"] = []

    def __init__(self):
        super().__init__()
        # Track this instance and decide whether it's the first window.
        # Recovery prompts only fire on the first window of a session.
        self._is_first_window = not MainWindow._open_windows
        MainWindow._open_windows.append(self)

        self.setWindowTitle("CNC Inlay Studio")
        self.resize(1280, 820)

        self._bits = load_bits()
        self._materials = load_materials()
        self._design: Design | None = None
        self._result: PipelineResult | None = None
        # In-memory copy of the current design's SVG markup. Always populated
        # whenever a design is loaded — from file, from a project's embedded
        # svg_content, or from the AI generator. Used by save_project so the
        # project file always embeds a portable SVG regardless of where the
        # source originally came from. This was previously only set for AI-
        # generated designs, which made loaded projects self-corrupt: the
        # source_path was overwritten to a cosmetic basename, the next save's
        # read_svg_content() couldn't find the file, and svg_content got
        # written as None — losing the design forever.
        self._design_svg_text: str | None = None
        self._units: Units = get_saved_units()
        self._current_project_path: Path | None = None
        # True when there are unsaved changes (design loaded, controls
        # edited, etc.). Used to drive the "Save changes?" prompt on Close
        # Project / window-close. Cleared on save / open / new project.
        self._is_dirty: bool = False

        # Four canvases — pocket and plug each get their own 3D and Top-down view.
        # Each canvas is locked into the right view_mode at construction.
        self._pocket_canvas3d = Canvas3D(self)
        self._pocket_canvas3d.set_view_mode("pocket")
        self._pocket_canvas2d = PreviewCanvas(self)
        self._pocket_canvas2d.set_view_mode("pocket")
        self._plug_canvas3d = Canvas3D(self)
        self._plug_canvas3d.set_view_mode("plug")
        self._plug_canvas2d = PreviewCanvas(self)
        self._plug_canvas2d.set_view_mode("plug")

        # 3D + 2D canvases live in a QStackedWidget; the floating
        # ViewModeToggle on each canvas flips between them. Replaces the
        # nested QTabWidget that used to consume 30 px of vertical space
        # showing two redundant tab strips on every preview.
        self._pocket_stack = QStackedWidget(self)
        self._pocket_stack.addWidget(self._pocket_canvas3d)  # index 0 = 3D
        self._pocket_stack.addWidget(self._pocket_canvas2d)  # index 1 = top-down
        self._pocket_canvas3d.viewModeToggled.connect(self._on_pocket_view_mode_changed)
        self._pocket_canvas2d.viewModeToggled.connect(self._on_pocket_view_mode_changed)

        self._plug_stack = QStackedWidget(self)
        self._plug_stack.addWidget(self._plug_canvas3d)
        self._plug_stack.addWidget(self._plug_canvas2d)
        self._plug_canvas3d.viewModeToggled.connect(self._on_plug_view_mode_changed)
        self._plug_canvas2d.viewModeToggled.connect(self._on_plug_view_mode_changed)

        # The third top-level tab: assembled inlay rendered with wood-species
        # textures so the user can sanity-check colour pairings before cutting.
        self._finished_canvas = FinishedCanvas(self)

        # Fourth top-level tab: surfacing 3D preview. When the user opens
        # Settings → Surface plug backing… and clicks Run, the surfacing
        # raster chains are pushed here and the streaming machine cone
        # tracks live in this canvas — same UX as Pocket / Plug runs.
        self._surfacing_canvas3d = Canvas3D(self)
        self._surfacing_canvas3d.set_view_mode("pocket")  # not mirrored

        self._main_tabs = QTabWidget(self)
        self._main_tabs.addTab(self._pocket_stack, "Pocket")
        self._main_tabs.addTab(self._plug_stack, "Plug")
        self._main_tabs.addTab(self._finished_canvas, "Finished")
        self._main_tabs.addTab(self._surfacing_canvas3d, "Surfacing")
        # 40% larger tab font so Pocket / Plug / Finished read as primary
        # navigation, not nested controls. Bold for the same reason.
        self._main_tabs.setStyleSheet(
            "QTabBar::tab { font-size: 16px; font-weight: 600; "
            "padding: 10px 20px; min-width: 90px; }"
            "QTabBar::tab:selected { color: #f5a623; }"
        )

        self._controls = ControlsPanel(
            self._bits, self._materials, InlayParams(), self
        )
        # Apply persisted unit preference to the freshly built controls.
        self._controls.set_units(self._units)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._controls)

        # Size the sidebar to fit every control on first paint without any
        # horizontal scrolling. ``minimumSizeHint().width()`` reports the
        # narrowest the controls panel can be without clipping (sizeHint is
        # the *preferred* width, which the trailing stretches inflate to a
        # silly number). Add the scrollbar gutter and a small breathing
        # margin so the rightmost glyph never crowds the scroll track.
        scrollbar_w = scroll.verticalScrollBar().sizeHint().width() or 16
        controls_min_w = self._controls.minimumSizeHint().width()
        sidebar_w = max(320, controls_min_w + scrollbar_w + 8)
        scroll.setMinimumWidth(sidebar_w)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(scroll)
        splitter.addWidget(self._main_tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # Initial window is 1280 wide; reserve sidebar_w for the controls,
        # everything else for the preview pane.
        splitter.setSizes([sidebar_w, max(700, self.width() - sidebar_w)])
        # Non-opaque resize: splitter drag shows a thin handle indicator and
        # only resizes the panes ONCE on release. With opaque (continuous)
        # resize, each pixel of drag fires a resizeEvent on every canvas (4
        # of them — pocket+plug × 2D+3D), each one re-fits, re-renders, and
        # invalidates GraphicsView caches. That's what made dragging feel
        # laggy. Pages, Figma, Xcode all use the same pattern for heavy panes.
        splitter.setOpaqueResize(False)
        self.setCentralWidget(splitter)

        # Machine control: bottom dock widget driving a MachineController.
        self._controller = MachineController(self)
        self._control_panel = ControlPanel(self._controller, self)
        self._machine_dock = QDockWidget("Machine", self)
        self._machine_dock.setObjectName("MachineDock")
        self._machine_dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self._machine_dock.setFeatures(
            QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
        )
        self._machine_dock.setWidget(self._control_panel)
        self.addDockWidget(Qt.BottomDockWidgetArea, self._machine_dock)

        # Setup Guide: right-side dock that stays open alongside the canvas
        # so the user can read instructions while editing settings on the left.
        # Tool-change dialog — appears automatically when the cut hits an M0
        # programmed pause for a bit swap. Modeless so the user can use the
        # bottom-dock jog controls while it's open.
        self._tool_change_dialog = ToolChangeDialog(self)
        self._setup_guide = SetupGuide(self)
        self._setup_dock = QDockWidget("Setup Guide", self)
        self._setup_dock.setObjectName("SetupGuideDock")
        self._setup_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self._setup_dock.setFeatures(
            QDockWidget.DockWidgetClosable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )
        self._setup_dock.setWidget(self._setup_guide)
        self._setup_dock.setMinimumWidth(360)
        self.addDockWidget(Qt.RightDockWidgetArea, self._setup_dock)
        self._setup_dock.hide()  # closed by default

        # Toolpath simulator (digital playback).
        self._simulator = Simulator(self)
        self._sim_active_side: str | None = None  # "pocket" or "plug" while running
        self._pocket_seconds: float = 0.0
        self._plug_seconds: float = 0.0
        # Streaming-time tracking: elapsed since stream started + 1 Hz timer
        # so the user sees seconds tick up even during a long G1 move that
        # doesn't generate progress events.
        self._stream_start_time: float | None = None
        self._stream_last_cursor: int = 0
        self._stream_last_total: int = 0
        self._stream_estimated_total_s: float = 0.0
        # Resume bookkeeping: start_line is the original-program line we
        # asked the streamer to begin at; preamble_len is how many extra
        # lines the streamer prepends (5 modal-state-restore lines for
        # any non-zero start, 0 for a fresh run).
        self._streaming_start_line: int = 0
        self._streaming_preamble_len: int = 0
        from PySide6.QtCore import QTimer as _QTimer
        self._stream_clock = _QTimer(self)
        self._stream_clock.setInterval(1000)
        self._stream_clock.timeout.connect(self._tick_stream_clock)

        # Streaming state (set when a real cut starts).
        self._streaming_side: str | None = None
        # Crash recovery: when set, the next Run<side> click will resume from
        # the saved line instead of starting fresh.
        self._pending_resume: Checkpoint | None = None
        # Prevent the Mac from sleeping while streaming a real cut.
        self._sleep_inhibitor = SleepInhibitor()

        self.setStatusBar(QStatusBar(self))
        self._set_status("Load an SVG or PNG to begin.")

        # Trial-status pill on the right side of the status bar. Hidden when
        # licensed; shows "X days left in trial — Buy License" in the final
        # nag window and "Trial expired — Buy License" after that. Click
        # opens the Buy License dialog.
        from PySide6.QtWidgets import QPushButton as _QBtn
        self._trial_pill = _QBtn(self)
        self._trial_pill.setStyleSheet(
            "QPushButton {"
            "  background: rgba(245, 166, 35, 220);"
            "  color: #1d1f24;"
            "  border: none;"
            "  border-radius: 10px;"
            "  padding: 2px 12px;"
            "  font-weight: 600;"
            "  font-size: 12px;"
            "}"
            "QPushButton:hover { background: rgba(255, 184, 60, 240); }"
        )
        self._trial_pill.clicked.connect(self._on_buy_license)
        self.statusBar().addPermanentWidget(self._trial_pill)
        self._refresh_trial_pill()
        # Re-check the trial state every hour so the countdown ticks down
        # without needing an app restart.
        from PySide6.QtCore import QTimer as _QTimer
        self._trial_pill_timer = _QTimer(self)
        self._trial_pill_timer.setInterval(3600 * 1000)
        self._trial_pill_timer.timeout.connect(self._refresh_trial_pill)
        self._trial_pill_timer.start()

        self._build_menu()
        self._wire_signals()
        self.setAcceptDrops(True)

        # Check for an unfinished cut from the previous session and offer to
        # resume. Done on a deferred timer so the main window appears first.
        # Only the first window in a session does this — secondary windows
        # are explicitly new projects, not recoveries.
        if self._is_first_window:
            from PySide6.QtCore import QTimer as _QTimer
            # Post-update session restore: if a previous run was relaunched
            # by the auto-installer, it stashed the open project's path.
            # Pop it (one-shot) and reopen on a deferred timer so the main
            # window paints first.
            _QTimer.singleShot(150, self._maybe_reopen_after_update)
            _QTimer.singleShot(300, self._maybe_offer_resume)
            # Background update check — also first-window-only so opening
            # extra project windows doesn't re-poll GitHub on every spawn.
            # Deferred 5 s so the launch sequence + any resume prompt finish
            # first; users see the update offer once the app is settled.
            _QTimer.singleShot(5000, self._maybe_check_for_updates_silently)
        else:
            # Cascade-position so the new window doesn't sit on top of the
            # previous one (Mac convention).
            offset = 30 * (len(MainWindow._open_windows) - 1)
            self.move(self.x() + offset, self.y() + offset)

        # Defensive: also wire cleanup to aboutToQuit. closeEvent doesn't fire
        # on every exit path (e.g. abrupt scripted shutdown), and an unstopped
        # worker QThread aborts the process at finalisation.
        self._cleaned_up = False
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._cleanup)

    def _cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        try:
            self._sleep_inhibitor.release()
        except Exception:
            pass
        try:
            self._controller.shutdown()
        except Exception:
            pass
        # Drop ourselves from the registry so the process can exit cleanly
        # once the last window is closed.
        try:
            MainWindow._open_windows.remove(self)
        except ValueError:
            pass

    def closeEvent(self, event):
        # Window-close (red traffic light, Cmd+W, Cmd+Q) goes through here.
        # Offer to save unsaved edits before tearing the window down.
        if not self._maybe_prompt_save_changes():
            event.ignore()
            return
        self._cleanup()
        super().closeEvent(event)

    @classmethod
    def open_new_window(cls) -> "MainWindow":
        """Spawn a fresh, blank project in its own top-level window. Mac-style
        — multiple documents, single process. The new window inherits theme,
        bit/material library, and the saved units preference, but starts with
        no design loaded."""
        win = cls()
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    # ------------------------------------------------------------------ menu

    def _build_menu(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")

        new_window_act = QAction("New Project Window", self)
        new_window_act.setShortcut(QKeySequence.New)  # Cmd+N on Mac
        new_window_act.triggered.connect(self._on_new_window)
        file_menu.addAction(new_window_act)

        file_menu.addSeparator()

        open_design_act = QAction("Open Design…", self)
        open_design_act.setShortcut(QKeySequence.Open)
        open_design_act.triggered.connect(self._on_load_requested)
        file_menu.addAction(open_design_act)

        file_menu.addSeparator()

        open_project_act = QAction("Open Project…", self)
        open_project_act.setShortcut(QKeySequence("Ctrl+Shift+O"))
        open_project_act.triggered.connect(self._on_open_project)
        file_menu.addAction(open_project_act)

        save_project_act = QAction("Save Project", self)
        save_project_act.setShortcut(QKeySequence.Save)
        save_project_act.triggered.connect(self._on_save_project)
        file_menu.addAction(save_project_act)

        save_project_as_act = QAction("Save Project As…", self)
        save_project_as_act.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_project_as_act.triggered.connect(self._on_save_project_as)
        file_menu.addAction(save_project_as_act)

        file_menu.addSeparator()
        close_project_act = QAction("Close Project", self)
        close_project_act.setShortcut(QKeySequence("Ctrl+W"))
        close_project_act.triggered.connect(self._on_close_project)
        file_menu.addAction(close_project_act)

        file_menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.setShortcut(QKeySequence.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        # Settings → Units submenu.
        settings_menu = menu.addMenu("&Settings")
        units_menu = settings_menu.addMenu("Units")
        self._units_group = QActionGroup(self)
        self._units_group.setExclusive(True)
        self._metric_action = QAction("Metric (mm)", self, checkable=True)
        self._imperial_action = QAction("Imperial (inches)", self, checkable=True)
        for act, value in (
            (self._metric_action, Units.METRIC),
            (self._imperial_action, Units.IMPERIAL),
        ):
            act.setData(value)
            self._units_group.addAction(act)
            units_menu.addAction(act)
        self._metric_action.setChecked(self._units == Units.METRIC)
        self._imperial_action.setChecked(self._units == Units.IMPERIAL)
        self._units_group.triggered.connect(self._on_units_changed)

        settings_menu.addSeparator()
        # Work-zero actions live up here in the menu rather than as buttons
        # in the bottom dock so a stray click can't silently overwrite the
        # CNC's reference points. Each one re-confirms before sending.
        zero_menu = settings_menu.addMenu("Work zero (machine origin)")
        self._set_xy_act = QAction("Set XY = 0…", self)
        self._set_xy_act.triggered.connect(
            lambda: self._control_panel.confirm_set_xy_zero()
        )
        self._set_z_act = QAction("Set Z = 0…", self)
        self._set_z_act.triggered.connect(
            lambda: self._control_panel.confirm_set_z_zero()
        )
        self._set_all_act = QAction("Set all = 0…", self)
        self._set_all_act.triggered.connect(
            lambda: self._control_panel.confirm_set_all_zero()
        )
        for act in (self._set_xy_act, self._set_z_act, self._set_all_act):
            zero_menu.addAction(act)

        settings_menu.addSeparator()
        my_bits_act = QAction("My Bits…", self)
        my_bits_act.setStatusTip(
            "Pick the endmills, ball-noses, and V-bits you own from the catalog"
        )
        my_bits_act.triggered.connect(self._on_open_my_bits)
        settings_menu.addAction(my_bits_act)
        surface_act = QAction("Surface plug backing…", self)
        surface_act.setStatusTip(
            "Plane the back of an assembled inlay flush, exposing the inlay"
        )
        surface_act.triggered.connect(self._on_open_surfacing_dialog)
        settings_menu.addAction(surface_act)

        prefs_act = QAction("Preferences…", self)
        prefs_act.triggered.connect(self._on_open_preferences)
        settings_menu.addAction(prefs_act)

        # Help menu — About + Check for Updates. macOS auto-promotes any
        # action whose menuRole is AboutRole into the application menu (the
        # bold leftmost menu) AND keeps it visible under Help, which is the
        # convention every native Mac app follows.
        help_menu = menu.addMenu("&Help")
        about_act = QAction("About CNC Inlay Studio", self)
        about_act.setMenuRole(QAction.AboutRole)  # macOS app-menu promotion
        about_act.triggered.connect(self._on_show_about)
        help_menu.addAction(about_act)
        help_menu.addSeparator()
        check_updates_act = QAction("Check for Updates…", self)
        check_updates_act.triggered.connect(self._on_check_for_updates_manual)
        help_menu.addAction(check_updates_act)
        help_menu.addSeparator()
        # Licensing — visible whether or not the user has a license so they
        # can switch machines, re-enter, or upgrade from trial.
        buy_license_act = QAction("Buy License…", self)
        buy_license_act.triggered.connect(self._on_buy_license)
        help_menu.addAction(buy_license_act)
        enter_license_act = QAction("Enter License…", self)
        enter_license_act.triggered.connect(self._on_enter_license)
        help_menu.addAction(enter_license_act)

    # ----------------------------------------------------------------- wiring

    def _wire_signals(self) -> None:
        self._controls.fileRequested.connect(self._on_load_requested)
        self._controls.generateRequested.connect(self._on_generate_requested)
        self._controls.previewRequested.connect(self._on_preview)
        # Cheap, immediate handlers — visual cue (clear stale toolpaths),
        # dirty bookkeeping, optional setup-guide refresh.
        self._controls.parametersChanged.connect(self._invalidate_result)
        self._controls.parametersChanged.connect(self._on_params_changed_refresh_guide)
        self._controls.parametersChanged.connect(self._mark_dirty)
        # Expensive handlers (re-render the Finished canvas + stock outlines on
        # all 4 canvases) are debounced. Without this, dragging a spinbox
        # rebuilds GL items on every tick — visibly fine but wasteful, and on
        # macOS the GL allocator stutters under rapid churn. 80 ms coalesces a
        # spinbox flurry into a single rebuild without feeling laggy.
        from PySide6.QtCore import QTimer as _QTimer
        self._param_debounce = _QTimer(self)
        self._param_debounce.setSingleShot(True)
        self._param_debounce.setInterval(80)
        self._param_debounce.timeout.connect(self._refresh_finished_canvas)
        self._param_debounce.timeout.connect(self._refresh_stock_outlines)
        # Stock thickness / W/H changes need to update the 3D wood-block
        # rendering too, otherwise the cuts visually plunge below the
        # bed when the user hasn't measured stock yet.
        self._param_debounce.timeout.connect(self._push_stock_blocks_to_canvases)
        self._controls.parametersChanged.connect(self._param_debounce.start)
        self._controls.designSizeChanged.connect(self._on_design_size_changed)
        self._controls.designSizeChanged.connect(lambda *_: self._mark_dirty())
        self._controls.rotateRequested.connect(self._on_rotate_design)
        # Updater results: signals are emitted from the daemon worker thread
        # in updater.py; Qt's auto-connection (sender in worker thread,
        # receiver self in main thread) queues delivery to the main-thread
        # event loop so the slot runs there. Without this bridge, a worker
        # thread touching widgets crashes the app on macOS.
        self._updaterResultLoud.connect(self._on_updater_result_loud)
        self._updaterResultQuiet.connect(self._on_updater_result_quiet)

        # Simulator wiring.
        self._control_panel.simulatePocketRequested.connect(
            lambda: self._start_simulation("pocket")
        )
        self._control_panel.simulatePlugRequested.connect(
            lambda: self._start_simulation("plug")
        )
        self._control_panel.simulatePauseRequested.connect(self._toggle_sim_pause)
        self._control_panel.simulateStopRequested.connect(self._stop_simulation)
        self._control_panel.simulateSpeedChanged.connect(self._simulator.set_speed)
        self._control_panel.setupGuideRequested.connect(self._open_setup_dialog)
        self._simulator.positionChanged.connect(self._on_sim_position)
        self._simulator.elapsedChanged.connect(self._control_panel.update_simulation_time)
        self._simulator.finished.connect(self._on_sim_finished)
        self._simulator.set_speed(10.0)

        # Run-on-machine wiring.
        self._control_panel.runPocketRequested.connect(lambda: self._run_on_machine("pocket", dry_run=False))
        self._control_panel.runPlugRequested.connect(lambda: self._run_on_machine("plug", dry_run=False))
        self._control_panel.dryRunPocketRequested.connect(lambda: self._run_on_machine("pocket", dry_run=True))
        self._control_panel.dryRunPlugRequested.connect(lambda: self._run_on_machine("plug", dry_run=True))
        self._control_panel.streamPauseRequested.connect(self._controller.stream_pause)
        # On resume, clear the banner, hide the tool-change dialog, and re-
        # disable jog (cut is moving again).
        self._control_panel.streamResumeRequested.connect(self._controller.stream_resume)
        self._control_panel.streamResumeRequested.connect(
            self._control_panel.clear_tool_change_banner
        )
        self._control_panel.streamResumeRequested.connect(
            lambda: self._control_panel.set_paused_for_tool_change(False)
        )
        self._control_panel.streamResumeRequested.connect(
            self._tool_change_dialog.hide
        )
        self._control_panel.streamAbortRequested.connect(self._controller.stream_abort)
        # Scrub-bar wiring: live position preview while dragging, and
        # "▶ Run from cursor" → confirm + start_stream(start_line=N).
        self._control_panel.scrubPositionChanged.connect(self._on_scrub_preview)
        self._control_panel.runFromLineRequested.connect(self._on_run_from_line)
        self._controller.connectionChanged.connect(self._on_machine_connection_changed)
        self._controller.streamStarted.connect(self._on_stream_started)
        # Progress updates go through main_window so it can compute and
        # display the elapsed/remaining time alongside the line count.
        self._controller.streamProgress.connect(self._on_stream_progress)
        self._controller.streamCompleted.connect(self._on_stream_completed)
        self._controller.streamAborted.connect(self._on_stream_aborted)
        # Tool-change handling: status banner + modeless dialog + re-enable jog.
        self._controller.toolChangeRequired.connect(
            self._control_panel.show_tool_change_banner
        )
        self._controller.toolChangeRequired.connect(self._on_tool_change_required)
        # Dialog button shortcuts wire back to the same machine-control flows.
        self._tool_change_dialog.resumeRequested.connect(
            self._control_panel.streamResumeRequested.emit
        )
        self._tool_change_dialog.setZeroZRequested.connect(
            lambda: self._control_panel.confirm_set_z_zero()
        )
        self._tool_change_dialog.openSetupGuideRequested.connect(
            self._open_setup_dialog
        )
        self._controller.statusChanged.connect(self._on_machine_status_changed)
        self._controller.settingsReceived.connect(self._on_grbl_settings_received)
        self._controller.response.connect(self._on_grbl_response)
        self._control_panel.grblConfigRequested.connect(self._controller.query_settings)
        self._control_panel.grblUnlockRequested.connect(self._on_grbl_unlock)
        self._control_panel.grblRawCommandRequested.connect(self._on_grbl_raw_command)

    # ----------------------------------------------------------------- helpers

    def _all_3d_canvases(self):
        return (self._pocket_canvas3d, self._plug_canvas3d, self._surfacing_canvas3d)

    def _all_2d_canvases(self):
        return (self._pocket_canvas2d, self._plug_canvas2d)

    # ---------- 3D / Top-down toggle handlers (replace nested QTabWidgets)

    def _on_pocket_view_mode_changed(self, mode: str) -> None:
        idx = 0 if mode == "3d" else 1
        self._pocket_stack.setCurrentIndex(idx)
        # Keep both toggles in sync — the user might click on either canvas
        # but the active toggle should always reflect the visible side.
        self._pocket_canvas3d._view_toggle.set_mode(mode, _emit=False)
        self._pocket_canvas2d._view_toggle.set_mode(mode, _emit=False)

    def _on_plug_view_mode_changed(self, mode: str) -> None:
        idx = 0 if mode == "3d" else 1
        self._plug_stack.setCurrentIndex(idx)
        self._plug_canvas3d._view_toggle.set_mode(mode, _emit=False)
        self._plug_canvas2d._view_toggle.set_mode(mode, _emit=False)

    def _push_stock_blocks_to_canvases(self) -> None:
        """Tell each 3D canvas its physical wood dimensions so it can
        render the wood block sitting on the bed and lift toolpaths
        accordingly. Pocket, plug, and surfacing canvases each get
        their own dimensions; if a stock dimension isn't set, the
        canvas falls back to the design extent + pad."""
        if self._design is None:
            return
        params = self._controls.current_params()
        # Pocket-side wood: actual stock or fall back to design footprint.
        p_w = params.pocket_stock_width_mm or self._design.width_mm
        p_h = params.pocket_stock_height_mm or self._design.height_mm
        p_t = params.pocket_thickness_mm or (params.pocket_depth_mm + params.glue_gap_mm)
        self._pocket_canvas3d.set_stock_block(p_w, p_h, p_t)
        # Plug-side wood: same idea.
        l_w = params.plug_stock_width_mm or self._design.width_mm
        l_h = params.plug_stock_height_mm or self._design.height_mm
        l_t = params.plug_thickness_mm or (params.pocket_depth_mm + params.glue_gap_mm)
        self._plug_canvas3d.set_stock_block(l_w, l_h, l_t)
        # Surfacing: combined assembled-panel block as 3 stacked
        # coloured layers — pocket stock (warm brown), glue gap (amber
        # band, often quite thin so highlighted in a higher-contrast
        # colour), and plug backing (lighter wood tone). Footprint =
        # pocket stock because the assembled panel sits on the pocket
        # wood.
        pocket_layer = (p_t, (0.62, 0.45, 0.28, 1.0), "Pocket")
        glue_layer = (max(0.05, params.glue_gap_mm), (0.95, 0.70, 0.20, 1.0), "Glue gap")
        plug_layer = (l_t, (0.85, 0.72, 0.50, 1.0), "Plug backing")
        self._surfacing_canvas3d.set_stock_layers(p_w, p_h, [pocket_layer, glue_layer, plug_layer])

    def _refresh_stock_outlines(self) -> None:
        """Re-render the dashed stock rectangles when the user edits W/H values
        — without this they wouldn't update until the next Preview."""
        if self._design is None:
            return
        params = self._controls.current_params()
        for c in (self._pocket_canvas2d, self._pocket_canvas3d):
            c.set_stock_outline(
                params.pocket_stock_width_mm, params.pocket_stock_height_mm
            )
        for c in (self._plug_canvas2d, self._plug_canvas3d):
            c.set_stock_outline(
                params.plug_stock_width_mm, params.plug_stock_height_mm
            )

    def _refresh_finished_canvas(self) -> None:
        """Re-push the achievable plug geometry + material names into the
        Finished tab. Uses the pipeline's ``finished_geometry`` (which models
        the bit's reach) when a preview has been computed; otherwise prompts
        the user to run preview, since the raw SVG would mislead about thin
        features that won't actually be cut.
        """
        if self._result is not None and self._result.finished_geometry is not None:
            polygon = self._result.finished_geometry
            preview_pending = False
        else:
            polygon = None
            preview_pending = self._design is not None
        self._finished_canvas.set_design(polygon, preview_pending=preview_pending)
        pocket_mat = self._controls.current_pocket_material()
        plug_mat = self._controls.current_plug_material()
        self._finished_canvas.set_materials(
            pocket_mat.name if pocket_mat else "",
            plug_mat.name if plug_mat else "",
        )

    def _update_size_overlays(self, design: Design, params: InlayParams) -> None:
        """Show physical dimensions on each canvas overlay so the user knows
        the wood size to prepare for each side, and draw the actual stock
        outline if the user provided one."""
        pocket_w = design.width_mm
        pocket_h = design.height_mm
        plug_w = pocket_w + 2 * params.boundary_offset_mm
        plug_h = pocket_h + 2 * params.boundary_offset_mm
        for c in (self._pocket_canvas2d, self._pocket_canvas3d):
            c.set_size_info("Pocket stock min", pocket_w, pocket_h, units=self._units)
            c.set_stock_outline(
                params.pocket_stock_width_mm, params.pocket_stock_height_mm
            )
        for c in (self._plug_canvas2d, self._plug_canvas3d):
            c.set_size_info("Plug stock min", plug_w, plug_h, units=self._units)
            c.set_stock_outline(
                params.plug_stock_width_mm, params.plug_stock_height_mm
            )

    # ----------------------------------------------------------- project I/O

    def _gather_project(self) -> ProjectFile:
        params = self._controls.current_params()
        rpm_e, feed_e, plunge_e = self._controls.edit_state()
        pocket_mat = self._controls.current_pocket_material()
        plug_mat = self._controls.current_plug_material()
        endmill = self._controls.current_endmill()
        vbit = self._controls.current_vbit()
        svg_content: str | None = None
        design_path: str | None = None
        design_filename: str | None = None
        if self._design and self._design.source_path is not None:
            design_path = str(self._design.source_path)
            design_filename = self._design.source_path.name
            # AI-generated designs have a synthetic source_path that doesn't
            # exist on disk; embed the markup we kept in memory instead.
            if self._design_svg_text is not None:
                svg_content = self._design_svg_text
            else:
                svg_content = read_svg_content(self._design.source_path)
        # Capture the user's resized inlay dimensions so the saved project
        # round-trips faithfully. Without these, an SVG resized from 161 mm
        # down to 80 mm reverts to 161 mm on next open.
        design_w: float | None = None
        design_h: float | None = None
        if self._design is not None:
            design_w = self._design.width_mm
            design_h = self._design.height_mm
        # If we already have a pipeline result and the user hasn't edited
        # anything since it was generated, cache it in the project file so
        # reopening doesn't have to re-run the (slow) pipeline.
        cached_result = None
        cache_signature = None
        if self._result is not None:
            try:
                cached_result = _serialise_pipeline_result(self._result)
                cache_signature = _compute_cache_signature(
                    design=self._design,
                    params=params,
                    pocket_mat=pocket_mat,
                    plug_mat=plug_mat,
                    endmill=endmill,
                    bit_library=self._bits,
                )
            except Exception:
                # Don't let a cache-serialisation bug block the save —
                # worst case the project just won't have toolpaths cached.
                cached_result = None
                cache_signature = None

        # Snapshot the user's current 3D view orientation. We grab it from the
        # pocket canvas; both 3D canvases share the same default + saved view
        # so reading either is fine. The user may be sitting on the plug tab
        # — in which case the pocket canvas's last orientation is whatever
        # they were last looking at when on the pocket tab. Acceptable.
        try:
            view_orientation = self._pocket_canvas3d.get_view_orientation()
        except Exception:
            view_orientation = None

        return ProjectFile(
            design_path=design_path,
            design_filename=design_filename,
            svg_content=svg_content,
            design_width_mm=design_w,
            design_height_mm=design_h,
            pocket_material=pocket_mat.name if pocket_mat else "",
            plug_material=plug_mat.name if plug_mat else "",
            endmill_name=endmill.name if endmill else "",
            vbit_name=vbit.name if vbit else None,
            params=params,
            rpm_user_edited=rpm_e,
            feed_user_edited=feed_e,
            plunge_user_edited=plunge_e,
            cached_result=cached_result,
            cache_signature=cache_signature,
            view_orientation=view_orientation,
        )

    def _on_save_project(self) -> bool:
        """Save the project. Returns True if saved successfully (or if there
        was no project to save), False if the save failed or the user
        cancelled the Save As dialog. Used by the close-project flow."""
        if self._current_project_path is None:
            return self._on_save_project_as()
        try:
            save_project(self._current_project_path, self._gather_project())
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._is_dirty = False
        self._set_status(f"Saved project to {self._current_project_path.name}")
        return True

    def _on_save_project_as(self) -> bool:
        """Returns True on successful save, False on cancel or error."""
        # Default to ~/Documents — the SVG's source dir is often a Downloads
        # or temp folder that isn't where the user wants their projects to
        # end up. The filename still inherits from the SVG when available.
        documents = Path.home() / "Documents"
        if self._design and self._design.source_path is not None:
            stem = self._design.source_path.stem
            suggested = str(documents / f"{stem}.inlay")
        else:
            suggested = str(documents / "untitled.inlay")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project as", suggested, "Inlay project (*.inlay);;All files (*)"
        )
        if not path:
            return False
        if not path.endswith(".inlay"):
            path = path + ".inlay"
        try:
            save_project(path, self._gather_project())
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._current_project_path = Path(path)
        self.setWindowTitle(f"CNC Inlay Studio — {self._current_project_path.name}")
        self._is_dirty = False
        self._set_status(f"Saved project to {Path(path).name}")
        return True

    def _on_new_window(self) -> None:
        """Open a fresh, blank project in its own top-level window so the
        user can work on multiple inlay projects side-by-side."""
        MainWindow.open_new_window()

    # ------------------------------------------------------ close / dirty

    def _maybe_prompt_save_changes(self) -> bool:
        """If the project has unsaved changes, ask the user what to do.

        Returns True if it's safe to proceed (either nothing to save, or the
        user chose Save / Discard and any save succeeded). Returns False if
        the user cancelled or the save itself failed — in which case the
        caller should abort the close.
        """
        if not self._is_dirty:
            return True
        if self._design is None and self._current_project_path is None:
            # Nothing meaningful loaded — silently proceed.
            return True
        name = (
            self._current_project_path.name
            if self._current_project_path is not None
            else "this project"
        )
        reply = QMessageBox.question(
            self,
            "Save changes?",
            f"<b>{name}</b> has unsaved changes.<br><br>"
            "Do you want to save them before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Save:
            return self._on_save_project()
        # Discard
        return True

    def _on_close_project(self) -> None:
        """File → Close Project. Prompts to save unsaved changes, then resets
        the current window to an empty-project state (does NOT close the
        window itself — to close the window use Cmd+W if Close Project is
        bound to a different shortcut, or the red traffic-light button)."""
        if not self._maybe_prompt_save_changes():
            return
        self._reset_to_blank_project()

    def _reset_to_blank_project(self) -> None:
        """Wipe all project state and return the UI to its launch defaults.
        Used by Close Project. Intentionally does NOT close the window so
        the user can immediately load a different design or project."""
        from cnc_inlay.core.job import InlayParams

        self._design = None
        self._design_svg_text = None
        self._result = None
        self._current_project_path = None
        self._pending_resume = None

        for c in self._all_2d_canvases():
            c.set_design(None)
        for c in self._all_3d_canvases():
            c.set_design(None)

        # Reset every control back to defaults.
        self._controls.apply_params(InlayParams())
        self._controls.set_design_summary("No project loaded", 0.0, 0.0)
        self._controls.set_summary("")
        self._controls.set_recommendation(
            "Load an SVG or open a project to begin."
        )

        self._refresh_finished_canvas()
        # Clear stock-outline overlays; no design to size against.
        for c in (*self._all_2d_canvases(), *self._all_3d_canvases()):
            try:
                c.set_stock_outline(0.0, 0.0)
            except Exception:
                pass
        self._control_panel.set_run_available(False)
        self._control_panel.set_simulation_available(False)

        self.setWindowTitle("CNC Inlay Studio")
        self._is_dirty = False
        self._set_status("Project closed.")

    def _mark_dirty(self) -> None:
        """Hook invoked whenever the user changes something that should
        prompt a save on close. Cheap; safe to call on every parameter
        change. Only takes effect after a project / design is loaded — until
        then there's nothing to lose."""
        if self._design is None and self._current_project_path is None:
            return
        self._is_dirty = True

    def _maybe_restore_cached_result(self, project: ProjectFile) -> bool:
        """If ``project`` has a cached pipeline result whose signature still
        matches the current params + design, install it as ``self._result``
        and paint the canvases — skipping the slow run_pipeline call.

        Returns True iff the cache was restored. False means the caller
        should let the user run Preview Toolpaths to compute fresh.
        """
        if project.cached_result is None or project.cache_signature is None:
            return False
        if self._design is None:
            return False
        params = self._controls.current_params()
        pocket_mat = self._controls.current_pocket_material()
        plug_mat = self._controls.current_plug_material()
        endmill = self._controls.current_endmill()
        live_sig = _compute_cache_signature(
            design=self._design,
            params=params,
            pocket_mat=pocket_mat,
            plug_mat=plug_mat,
            endmill=endmill,
            bit_library=self._bits,
        )
        if live_sig != project.cache_signature:
            return False
        try:
            result = _deserialise_pipeline_result(project.cached_result, self._bits)
        except Exception:
            return False
        # Hand the cached result through the same path the live pipeline uses.
        self._on_preview_done(result, params, endmill, pocket_mat, plug_mat)
        return True

    def _maybe_reopen_after_update(self) -> None:
        """If the previous launch was triggered by the auto-installer, the
        update flow stashed the open project's path. Pop and re-open it."""
        from cnc_inlay.updater import take_reopen_project_path
        from cnc_inlay.core.project import load_project

        path = take_reopen_project_path()
        if not path:
            return
        try:
            project = load_project(path)
        except Exception as exc:
            self._set_status(f"Couldn't reopen project after update: {exc}")
            return
        self._apply_project(project, source_path=Path(path))

    def prepare_for_update_install(self) -> bool:
        """Persist enough state for the post-update relaunch to restore the
        user's open project. Called by the in-app updater right before it
        kicks off the download.

        Returns True if it's safe to proceed with the update, False if the
        user cancelled (e.g. declined to save an unsaved untitled project).
        """
        from cnc_inlay.updater import set_reopen_project_path

        # No project loaded — nothing to restore. Clear any stale reopen marker.
        if self._design is None and self._current_project_path is None:
            set_reopen_project_path(None)
            return True

        # Untitled project (design loaded but never saved as .inlay): need
        # the user to give it a name before we can record what to reopen.
        if self._current_project_path is None:
            reply = QMessageBox.question(
                self,
                "Save before updating?",
                "<p>Save your unsaved work so it reopens automatically after "
                "the update?</p>",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply == QMessageBox.Cancel:
                return False
            if reply == QMessageBox.Save:
                if not self._on_save_project_as():
                    # User cancelled the Save As file dialog.
                    return False
            # Discard: don't save anything, don't mark for reopen.
            if self._current_project_path is None:
                set_reopen_project_path(None)
                return True

        # Project has a path. Save in place if there are unsaved edits, then
        # record the path for the post-restart reopener.
        if self._is_dirty:
            try:
                save_project(
                    self._current_project_path, self._gather_project()
                )
                self._is_dirty = False
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Save failed",
                    f"<p>Couldn't save the project before updating:</p>"
                    f"<p><code>{exc}</code></p>"
                    "<p>The update will still install, but you'll need to "
                    "reopen the project manually after restart.</p>",
                )
                # Still proceed — user explicitly chose to update.
        set_reopen_project_path(str(self._current_project_path))
        return True

    def _on_open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", "", "Inlay project (*.inlay);;All files (*)"
        )
        if not path:
            return
        try:
            project = load_project(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self._apply_project(project, source_path=Path(path))

    def _apply_project(self, project: ProjectFile, source_path: Path) -> None:
        # Apply units first so subsequent UI updates use the right display.
        self._units = project.params.gcode_units
        if project.params.gcode_units == Units.METRIC:
            self._metric_action.setChecked(True)
        else:
            self._imperial_action.setChecked(True)
        set_saved_units(self._units)

        # Selections.
        if project.pocket_material:
            self._controls.select_pocket_material(project.pocket_material)
        if project.plug_material:
            self._controls.select_plug_material(project.plug_material)
        if project.endmill_name:
            self._controls.select_endmill(project.endmill_name)
        if project.vbit_name:
            self._controls.select_vbit(project.vbit_name)

        # Spinboxes / overrides.
        self._controls.apply_params(project.params)

        # Post-load step: applies the saved inlay dimensions (if any) and the
        # final window-title / status bar updates. Runs AFTER the design has
        # been parsed and installed by the async _load_design path. Defined
        # as a closure so all the project-load branches below can hand it as
        # ``on_done`` and the cleanup happens uniformly.
        def _after_design_loaded(*, embedded_svg_text: str | None = None,
                                 display_filename: str | None = None) -> None:
            if self._design is None:
                # Loading failed — error dialog already shown by _load_design.
                self._current_project_path = source_path
                self.setWindowTitle(f"CNC Inlay Studio — {source_path.name}")
                self._set_status(f"Loaded project {source_path.name} (no design)")
                return
            # When the SVG came from the project's embedded copy, override
            # source_path with the user-facing filename and stash the raw
            # text so future saves round-trip the same SVG (JMU bug).
            if embedded_svg_text is not None:
                original_name = display_filename or "design.svg"
                self._design.source_path = Path(original_name)
                self._design_svg_text = embedded_svg_text
                self._controls.set_design_summary(
                    original_name, self._design.width_mm, self._design.height_mm
                )
            # Apply the saved inlay dimensions, if present. _load_design parses
            # the SVG natively-sized; this is what restores the user's resize.
            if (
                project.design_width_mm is not None
                and project.design_height_mm is not None
                and project.design_width_mm > 0
                and project.design_height_mm > 0
            ):
                self._on_design_size_changed(
                    project.design_width_mm, project.design_height_mm
                )
                self._controls.set_design_summary(
                    self._design.source_path.name if self._design.source_path else "design",
                    self._design.width_mm, self._design.height_mm,
                )
            self._current_project_path = source_path
            self.setWindowTitle(f"CNC Inlay Studio — {source_path.name}")
            # Project just opened from disk — nothing to save.
            # _apply_loaded_design / _on_design_size_changed both set
            # _is_dirty=True earlier in the chain; clear it now that we
            # know the load came from a saved project, not user edits.
            self._is_dirty = False

            # Restore cached toolpaths (skip the slow pipeline) IFF the
            # signature still matches the loaded params + design. This may
            # reset the 3D camera as a side effect of _on_preview_done, so
            # we apply the saved view orientation AFTER, last-write-wins.
            cache_restored = self._maybe_restore_cached_result(project)

            # Now apply the saved 3D camera angle if the project file has
            # one (v7+); otherwise fall through to the default orientation.
            if project.view_orientation:
                for canvas in self._all_3d_canvases():
                    canvas.set_view_orientation(project.view_orientation)
            else:
                for canvas in self._all_3d_canvases():
                    canvas.reset_view()

            if cache_restored:
                self._set_status(
                    f"Loaded project {source_path.name} (cached toolpaths)"
                )
            else:
                self._set_status(f"Loaded project {source_path.name}")

        # Prefer the embedded SVG content (portable); fall back to the path on
        # disk (legacy projects). If both are missing, the project loaded
        # without a design and the user can attach one via Open Design….
        if project.svg_content:
            try:
                import tempfile
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", suffix=".svg", delete=False
                )
                tmp.write(project.svg_content)
                tmp.close()
                original_name = (
                    project.design_filename
                    or Path(project.design_path or "design.svg").name
                )
                self._load_design(
                    tmp.name,
                    on_done=lambda: _after_design_loaded(
                        embedded_svg_text=project.svg_content,
                        display_filename=original_name,
                    ),
                )
                return  # post-load chained — don't fall through.
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Embedded design failed to load",
                    f"The project's embedded SVG couldn't be loaded:\n{exc}",
                )

        if project.design_path:
            design_path = Path(project.design_path)
            if design_path.exists():
                self._load_design(str(design_path), on_done=_after_design_loaded)
                return
            # Fall back: if just a basename was saved, look for it next
            # to the .inlay file before giving up entirely.
            sibling = source_path.parent / design_path.name
            if sibling.exists():
                self._load_design(str(sibling), on_done=_after_design_loaded)
                return
            choice = QMessageBox.question(
                self,
                "Design file missing",
                f"This project's design file isn't where it used to be "
                f"(<code>{design_path.name}</code>) and no copy is "
                f"embedded in the project. The settings (depth, materials, "
                f"speeds, bit sequence) loaded fine — you just need to "
                f"reattach the SVG.<br><br>"
                f"Browse to the SVG now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if choice == QMessageBox.Yes:
                from cnc_inlay.ui.controls import ask_design_path
                chosen = ask_design_path(self)
                if chosen:
                    self._load_design(chosen, on_done=_after_design_loaded)
                    return

        # No design path or embed found — finalise with whatever was loaded.
        _after_design_loaded()

    def _on_units_changed(self, action: QAction) -> None:
        new_units = action.data()
        if new_units is None or new_units == self._units:
            return
        self._units = new_units
        set_saved_units(new_units)
        self._controls.set_units(new_units)
        if self._design is not None:
            params = self._controls.current_params()
            self._update_size_overlays(self._design, params)
        self._set_status(
            f"Switched to {'metric (mm)' if new_units == Units.METRIC else 'imperial (inches)'}."
        )

    # -------------------------------------------------------- drag-and-drop

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".svg"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".svg"):
                self._load_design(path)
                return

    # ----------------------------------------------------------- design load

    def _on_load_requested(self) -> None:
        path = ask_design_path(self)
        if path:
            self._load_design(path)

    def _on_open_preferences(self) -> None:
        dlg = PreferencesDialog(self)
        dlg.exec()

    def _on_open_surfacing_dialog(self) -> None:
        """Settings → Surface plug backing… — open the surfacing planner."""
        from cnc_inlay.ui.surfacing_dialog import SurfacingDialog
        # Surfacing-bit candidates = dedicated surfacing bits PLUS any
        # owned flat endmill (a roughing endmill works fine for small
        # surfacing jobs and the user may not own a dedicated surfacer).
        # Sort surfacing bits first (largest → smallest), then endmills
        # by diameter so the friendliest defaults bubble to the top.
        surfacers = [b for b in self._bits if b.type == "surfacing"]
        endmills = [b for b in self._bits if b.type == "endmill"]
        surfacers.sort(key=lambda b: -b.diameter_mm)
        endmills.sort(key=lambda b: -b.diameter_mm)
        surfacing_bits = surfacers + endmills
        params = self._controls.current_params()
        # Default panel dimensions = the pocket stock dimensions, since
        # the assembled panel is the same physical piece. Fall back to
        # the design size if no stock dimensions are set.
        panel_w = params.pocket_stock_width_mm
        panel_h = params.pocket_stock_height_mm
        if panel_w <= 0 and self._design is not None:
            panel_w = self._design.width_mm
        if panel_h <= 0 and self._design is not None:
            panel_h = self._design.height_mm
        dlg = SurfacingDialog(
            self,
            surfacing_bits=surfacing_bits,
            default_panel_width_mm=panel_w or 200.0,
            default_panel_height_mm=panel_h or 100.0,
            default_glue_gap_mm=params.glue_gap_mm,
            default_pocket_depth_mm=params.pocket_depth_mm,
            default_plug_thickness_mm=params.plug_thickness_mm,
            default_pocket_thickness_mm=params.pocket_thickness_mm,
            machine_connected=self._controller.is_connected,
        )
        dlg.runRequested.connect(self._on_run_surfacing)
        dlg.saveRequested.connect(self._on_save_surfacing_gcode)
        dlg.exec()

    def _refresh_surfacing_preview(self) -> None:
        """Generate a default surfacing plan (using current params and
        the user's selected surfacing bit) and push the chains to the
        Surfacing tab. Skips silently when no surfacing bit is set."""
        bit = self._controls.current_surfacing_bit()
        if bit is None or self._design is None:
            self._surfacing_canvas3d.set_pocket_clear([])
            return
        params = self._controls.current_params()
        panel_w = params.pocket_stock_width_mm or self._design.width_mm
        panel_h = params.pocket_stock_height_mm or self._design.height_mm
        from cnc_inlay.core.pipeline import compute_surfacing_plan
        plan = compute_surfacing_plan(
            bit,
            stock_width_mm=panel_w,
            stock_height_mm=panel_h,
            pocket_depth_mm=params.pocket_depth_mm,
            glue_gap_mm=params.glue_gap_mm,
            plug_thickness_mm=params.plug_thickness_mm,
        )
        if not plan.chains:
            return
        self._push_surfacing_to_canvas(plan, panel_w, panel_h)

    def _push_surfacing_to_canvas(
        self, plan, panel_w_mm: float, panel_h_mm: float, combined_z_mm: float | None = None,
    ) -> None:
        """Render a surfacing plan in the Surfacing tab's 3D canvas.

        Re-pushes the layered wood-block (pocket / glue / plug) from
        current params before drawing the chains, so the visual is
        always in sync with the sidebar values.
        """
        # Make sure the layered block reflects current params. This is
        # idempotent and cheap.
        self._push_stock_blocks_to_canvases()
        # Reuse the existing chain renderer — surfacing chains are just
        # closed-loop-style polylines at depth, the same shape the
        # pocket-clear path renders.
        self._surfacing_canvas3d.set_pocket_clear(plan.chains)
        # Stash the bit diameter so the live machine cone is sized right.
        self._surfacing_bit_diameter_mm = plan.bit_diameter_mm
        self._surfacing_canvas3d.reset_view()

    def _on_run_surfacing(self, plan, combined_z_mm: float = 0.0) -> None:
        """User clicked Run on Machine in the surfacing dialog. Push the
        plan to the Surfacing tab so they can watch it run, switch the
        tab into focus, then confirm + stream."""
        if not self._controller.is_connected:
            QMessageBox.information(
                self, "Not connected", "Connect to the CNC machine first."
            )
            return
        params = self._controls.current_params()
        panel_w = params.pocket_stock_width_mm or (
            self._design.width_mm if self._design else 200.0
        )
        panel_h = params.pocket_stock_height_mm or (
            self._design.height_mm if self._design else 100.0
        )
        self._push_surfacing_to_canvas(
            plan, panel_w, panel_h,
            combined_z_mm=combined_z_mm if combined_z_mm > 0 else None,
        )
        # Switch the main tab strip to Surfacing so the user sees the
        # cone tracking immediately when streaming starts.
        for i in range(self._main_tabs.count()):
            if self._main_tabs.tabText(i) == "Surfacing":
                self._main_tabs.setCurrentIndex(i)
                break
        confirm = QMessageBox.question(
            self,
            "Run surfacing",
            f"<b>Start surfacing now?</b><br><br>"
            f"<b>Bit:</b> {plan.bit_name}<br>"
            f"<b>Total depth:</b> {plan.total_depth_mm:.2f} mm<br>"
            f"<b>Cut length:</b> {plan.cut_length_mm:,.0f} mm<br><br>"
            "<b style='color:#e0524a;'>Final checks:</b><br>"
            "• Glued panel is clamped <b>face-down</b> on the bed<br>"
            "• Surfacing bit is installed and tightened<br>"
            "• Z-zero is set on the <b>highest point</b> of the plug backing<br>"
            "• Dust shroud is removed or set high<br>"
            "• You can reach the e-stop<br><br>"
            "Click Yes to start streaming.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._streaming_side = "surfacing"
        self._streaming_start_line = 0
        self._streaming_preamble_len = 0
        self._controller.run_program(plan.gcode, start_line=0, checkpoint=None)
        self._set_status(
            f"Surfacing in progress — removing {plan.total_depth_mm:.2f} mm with {plan.bit_name}…"
        )

    def _on_save_surfacing_gcode(self, plan) -> None:
        """Save the surfacing G-code to a file the user can open in any sender."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save surfacing G-code",
            f"surfacing_{plan.bit_diameter_mm:.0f}mm.gcode",
            "G-code (*.gcode);;All files (*)",
        )
        if not path:
            return
        try:
            from pathlib import Path
            Path(path).write_text(plan.gcode)
            self._set_status(f"Saved surfacing G-code to {Path(path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def _on_open_my_bits(self) -> None:
        """Settings → My Bits — pick owned bits from the catalog. After
        save, reload bits from disk and refresh the controls panel so the
        Pocket / Plug / Roughing dropdowns reflect the new owned set
        across every open project window."""
        from cnc_inlay.ui.my_bits_dialog import MyBitsDialog
        dlg = MyBitsDialog(self)
        dlg.bitsChanged.connect(self._reload_bits_after_my_bits_change)
        dlg.exec()

    def _reload_bits_after_my_bits_change(self) -> None:
        from cnc_inlay.presets.bits import load_bits as _reload
        new_bits = _reload()
        # Update every open MainWindow's bit library + sidebar so the
        # change is reflected across the whole app, not just the window
        # that opened the dialog.
        for win in MainWindow._open_windows:
            win._bits = new_bits
            try:
                win._controls.set_bit_library(new_bits)
            except AttributeError:
                # Older controls panel without dynamic refresh — leave
                # the dropdown stale; user can restart to pick up the
                # change. Avoid crashing the dialog flow.
                pass

    def _on_show_about(self) -> None:
        from cnc_inlay.ui.about_dialog import show_about_dialog
        show_about_dialog(self)

    # -------------------------------------------------------- licensing UI

    def _on_buy_license(self) -> None:
        """Help → Buy License…  also fired by the trial-status pill."""
        from cnc_inlay.ui.license_dialog import show_buy_license_dialog
        if show_buy_license_dialog(self):
            self._refresh_trial_pill()

    def _on_enter_license(self) -> None:
        """Help → Enter License… — direct-to-activation (skips the marketing
        dialog), useful when a buyer has just received their key."""
        from cnc_inlay.ui.license_dialog import EnterLicenseDialog
        from PySide6.QtWidgets import QDialog
        dlg = EnterLicenseDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._refresh_trial_pill()

    def _refresh_trial_pill(self) -> None:
        """Show / hide / update the orange trial-status pill on the status bar."""
        from cnc_inlay.licensing import status_bar_text
        text = status_bar_text()
        if text is None:
            self._trial_pill.hide()
        else:
            self._trial_pill.setText(text)
            self._trial_pill.show()

    def _require_license_or_show_buy(self, feature_name: str) -> bool:
        """Gate helper for the paid features (Run on Machine, AI Generate).

        Returns True when the user is allowed to proceed (in trial OR
        licensed). Returns False after popping the Buy License dialog when
        the trial has expired and there's no license — caller should bail.
        After a successful in-dialog activation the pill refreshes so the
        user sees the new state immediately.
        """
        from cnc_inlay.licensing import is_unlocked
        if is_unlocked():
            return True
        from cnc_inlay.ui.license_dialog import show_buy_license_dialog
        activated = show_buy_license_dialog(self, feature_name=feature_name)
        if activated:
            self._refresh_trial_pill()
        return is_unlocked()

    # ---------------------------------------------------------- update checks

    # Cross-thread bridge for the updater. The check runs on a daemon thread
    # so the UI stays responsive; its result has to be marshalled back to
    # the main thread before any dialog is shown — touching widgets from a
    # non-Qt thread crashes the app instantly. The previous attempt used
    # ``QTimer.singleShot(0, fn)`` from the worker thread WITHOUT a parent;
    # PySide6 leaves that timer's affinity ambiguous and on macOS it
    # crashed the process the moment the user clicked the menu item. The
    # @Slot decorators + emit-from-worker pattern below is the canonical
    # Qt thread-safe handoff.

    _updaterResultLoud = Signal(object)
    _updaterResultQuiet = Signal(object)

    def _maybe_check_for_updates_silently(self) -> None:
        """Auto-check on launch. Honours the user's auto-check preference and
        the per-version Skip flag. Failures (no network etc.) are silent so
        the launch UX isn't disturbed."""
        from cnc_inlay.updater import check_for_update_async, get_auto_check_enabled
        if not get_auto_check_enabled():
            return
        check_for_update_async(self._updaterResultQuiet.emit)

    def _on_check_for_updates_manual(self) -> None:
        """Help → Check for Updates… — user-initiated, surfaces every outcome
        (up-to-date, error, update available)."""
        from cnc_inlay.updater import check_for_update_async
        check_for_update_async(self._updaterResultLoud.emit)

    def _on_updater_result_quiet(self, result) -> None:
        """Silent variant — runs in the main thread (queued from the worker
        signal). Only opens UI when there's an actionable update."""
        if result.is_update_available and not result.is_skipped_by_user:
            from cnc_inlay.ui.update_dialog import show_update_available_dialog
            show_update_available_dialog(self, result)

    def _on_updater_result_loud(self, result) -> None:
        """Manual variant — runs in the main thread. Always shows a dialog so
        the user knows the menu click did something."""
        from cnc_inlay.ui.update_dialog import (
            show_check_failed_dialog,
            show_up_to_date_dialog,
            show_update_available_dialog,
        )
        if result.release is None:
            show_check_failed_dialog(self)
            return
        if result.is_update_available:
            show_update_available_dialog(self, result)
        else:
            show_up_to_date_dialog(self, result.current_version)

    def _on_generate_requested(self) -> None:
        # License gate: AI generation is a paid feature.
        if not self._require_license_or_show_buy("AI Generate"):
            return
        from cnc_inlay.ai.secrets_store import has_api_key

        if not has_api_key():
            choice = QMessageBox.question(
                self,
                "API key required",
                "Generating SVGs uses the Google Gemini API and needs an "
                "API key. Open Preferences now to add one?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if choice == QMessageBox.Yes:
                self._on_open_preferences()
            return
        dlg = GenerateDialog(self)
        if dlg.exec() != QDialog.Accepted or not dlg.svg_text:
            return
        try:
            design = load_svg_from_string(dlg.svg_text)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Generated SVG couldn't be parsed",
                f"The traced SVG was rejected by the loader:\n\n{exc}\n\n"
                "Try a different prompt — the model may have produced a "
                "mostly-empty image.",
            )
            return
        prompt_short = dlg.prompt_edit.text().strip()[:40] or "generated"
        self._design_svg_text = dlg.svg_text  # for project save
        design.source_path = Path(f"{prompt_short}.svg")
        self._apply_loaded_design(design, display_name=design.source_path.name)

    def _load_design(
        self, path: str, *, on_done: Callable[[], None] | None = None
    ) -> None:
        """Load an SVG file in a background thread so the UI stays responsive
        while shapely processes complex paths. A modal busy dialog appears
        immediately so the user knows work is in progress, and any failure
        is surfaced via an error dialog instead of a silent freeze.

        ``on_done`` fires after the loaded design has been applied to the UI,
        used by the project-open flow to chain post-load setup (apply saved
        size, set window title) — those steps depend on ``self._design`` being
        populated, which only happens once the worker thread completes.
        """
        self._set_status(f"Loading {Path(path).name}…")

        def _on_loaded(design: Design) -> None:
            try:
                self._design_svg_text = Path(path).read_text(encoding="utf-8")
            except Exception:
                self._design_svg_text = None
            design.source_path = Path(path)
            self._apply_loaded_design(design, display_name=Path(path).name)
            if on_done is not None:
                on_done()

        def _on_failed(msg: str) -> None:
            self._set_status("Failed to load design.")
            QMessageBox.critical(
                self,
                "Failed to load design",
                f"{Path(path).name} could not be loaded.\n\n{msg.splitlines()[0]}",
            )

        run_with_progress(
            self,
            title="Loading design",
            message=(
                f"Parsing {Path(path).name}…\n\n"
                "Complex SVGs with many paths can take a minute or more."
            ),
            fn=load_svg,
            path=path,
            on_success=_on_loaded,
            on_failure=_on_failed,
        )

    def _apply_loaded_design(self, design: Design, *, display_name: str) -> None:
        """Install ``design`` as the current project geometry, auto-fitting it
        to the bed envelope if necessary, and refresh every UI surface."""
        from cnc_inlay.ui.canvas import BED_HEIGHT_MM, BED_WIDTH_MM

        auto_fit_msg: str | None = None
        if design.original_width_mm > BED_WIDTH_MM or design.original_height_mm > BED_HEIGHT_MM:
            sx = BED_WIDTH_MM / design.original_width_mm
            sy = BED_HEIGHT_MM / design.original_height_mm
            scale = min(sx, sy)
            new_w = design.original_width_mm * scale
            new_h = design.original_height_mm * scale
            design.rescale(new_w, new_h)
            auto_fit_msg = (
                f" — auto-scaled to {new_w:.1f}×{new_h:.1f} mm to fit the "
                f"{int(BED_WIDTH_MM)}×{int(BED_HEIGHT_MM)} mm bed"
            )

        self._design = design
        self._result = None
        params = self._controls.current_params()
        stock_depth = params.pocket_depth_mm + params.glue_gap_mm
        for c in self._all_2d_canvases():
            c.set_design(design.polygon)
        for c in self._all_3d_canvases():
            c.set_design(design.polygon, stock_depth_mm=stock_depth)
            # Fresh design with no saved project view — snap each 3D canvas
            # back to the default orientation so the design is centred and
            # framed at the X+ right / Y+ away / 30° tilt convention.
            c.reset_view()
        # Push the per-side wood-block dimensions so 3D rendering shows
        # the wood as a 3D block sitting on the bed grid (with toolpaths
        # cutting INTO the top), instead of toolpaths appearing to plunge
        # below the bed.
        self._push_stock_blocks_to_canvases()
        self._update_size_overlays(design, params)
        self._refresh_finished_canvas()
        self._controls.set_design_summary(
            display_name, design.width_mm, design.height_mm
        )
        self._controls.set_recommendation(
            "Click Preview Toolpaths to compute the toolpath and bit recommendation."
        )
        self._controls.set_summary("")
        suffix = auto_fit_msg or ""
        # Loading a fresh design (not via project open) starts dirty —
        # nothing has been saved yet.
        self._is_dirty = True
        self._set_status(f"Loaded {display_name}{suffix}")

    def _on_design_size_changed(self, width_mm: float, height_mm: float) -> None:
        """User dragged the size spinboxes — rescale the design and refresh."""
        if self._design is None:
            return
        self._design.rescale(width_mm, height_mm)
        self._result = None
        params = self._controls.current_params()
        stock_depth = params.pocket_depth_mm + params.glue_gap_mm
        for c in self._all_2d_canvases():
            c.set_design(self._design.polygon)
        for c in self._all_3d_canvases():
            c.set_design(self._design.polygon, stock_depth_mm=stock_depth)
        self._push_stock_blocks_to_canvases()
        self._update_size_overlays(self._design, params)
        self._refresh_finished_canvas()
        self._controls.set_summary("")
        self._on_params_changed_refresh_guide()

    def _on_rotate_design(self) -> None:
        """User clicked Rotate 90°. Spin the design + scaling baseline,
        push the rotated geometry into every canvas, and update the
        sidebar size spinboxes to reflect the swapped width/height."""
        if self._design is None:
            return
        self._design.rotate_90_clockwise()
        self._result = None
        params = self._controls.current_params()
        stock_depth = params.pocket_depth_mm + params.glue_gap_mm
        for c in self._all_2d_canvases():
            c.set_design(self._design.polygon)
        for c in self._all_3d_canvases():
            c.set_design(self._design.polygon, stock_depth_mm=stock_depth)
        self._push_stock_blocks_to_canvases()
        self._update_size_overlays(self._design, params)
        self._refresh_finished_canvas()
        # Sidebar reflects the new width/height (they swapped) without
        # firing designSizeChanged — set_design_summary already coalesces
        # via _suppress_size_signals.
        display_name = self._design.basename if self._design.source_path else "design"
        self._controls.set_design_summary(
            display_name, self._design.width_mm, self._design.height_mm
        )
        self._controls.set_summary("")
        self._on_params_changed_refresh_guide()
        self._is_dirty = True
        self._set_status(
            f"Rotated 90° → {self._design.width_mm:.1f} × {self._design.height_mm:.1f} mm"
        )

    # ------------------------------------------------------------ preview

    def _on_preview(self) -> None:
        if self._design is None:
            QMessageBox.information(self, "No design", "Load an SVG first.")
            return
        params = self._controls.current_params()
        vbit = self._controls.current_vbit()
        endmill = self._controls.current_endmill()
        pocket_mat = self._controls.current_pocket_material()
        plug_mat = self._controls.current_plug_material()
        if endmill is None or pocket_mat is None or plug_mat is None:
            QMessageBox.warning(
                self, "Missing selection", "Select endmill and materials."
            )
            return

        job = Job(
            design=self._design,
            vbit=vbit,
            endmill=endmill,
            pocket_material=pocket_mat,
            plug_material=plug_mat,
            params=params,
        )

        def _on_failed(msg: str) -> None:
            self._set_status("Generation failed.")
            QMessageBox.critical(
                self, "Generation failed", msg.splitlines()[0]
            )

        self._set_status("Generating toolpaths…")
        run_with_progress(
            self,
            title="Generating toolpaths",
            message=(
                "Computing pocket and plug toolpaths…\n\n"
                "Complex designs and small step-overs can take a minute or two."
            ),
            fn=run_pipeline,
            job=job,
            bit_library=self._bits,
            on_success=lambda result: self._on_preview_done(
                result, params, endmill, pocket_mat, plug_mat
            ),
            on_failure=_on_failed,
        )

    def _on_preview_done(
        self,
        result: PipelineResult,
        params: InlayParams,
        endmill: Bit,
        pocket_mat: Material,
        plug_mat: Material,
    ) -> None:
        # Force-release the pipeline's transient skimage / shapely arrays
        # (medial-axis bool + distance float64 buffers can be 10s of MB on
        # hi-res designs). They're already unreachable; this just gets them
        # back to the OS now instead of at the next GC cycle.
        import gc
        gc.collect()

        self._result = result
        stock_depth = params.pocket_depth_mm + params.glue_gap_mm
        for c in self._all_3d_canvases():
            c.set_design(self._design.polygon, stock_depth_mm=stock_depth)
            # Snap the 3D camera back to the default orientation whenever
            # fresh toolpaths are computed. Without this the camera might
            # be left over from a different design / pre-resize state and
            # the freshly-computed paths render at an awkward angle.
            c.reset_view()
        self._update_size_overlays(self._design, params)
        self._refresh_finished_canvas()

        # Pre-compute simulator timing for both sides so the user sees an estimate.
        eff_pocket = self._effective_material(pocket_mat, params)
        eff_plug = self._effective_material(plug_mat, params)
        pocket_segs = build_segments(
            result.pocket.chains_full,
            feed_mm_min=eff_pocket.feed_mm_min,
            plunge_mm_min=eff_pocket.plunge_mm_min,
            safe_z_mm=params.safe_z_mm,
        )
        plug_segs = build_segments(
            result.plug.chains_full,
            feed_mm_min=eff_plug.feed_mm_min,
            plunge_mm_min=eff_plug.plunge_mm_min,
            safe_z_mm=params.safe_z_mm,
        )
        self._pocket_seconds = total_duration_seconds(pocket_segs)
        self._plug_seconds = total_duration_seconds(plug_segs)
        self._control_panel.set_simulation_available(
            available=True,
            pocket_seconds=self._pocket_seconds,
            plug_seconds=self._plug_seconds,
        )
        # Enable Run-on-machine buttons if the machine is also connected.
        self._control_panel.set_run_available(True)
        # Build the scrub-bar segment + position index for both sides so
        # the operator can drag to a line, see the bit at that XYZ in the
        # 3D preview, and start cutting from there.
        self._refresh_scrub_programs(result, endmill)
        # Surfacing preview — if the user has picked a surfacing bit
        # in the sidebar, generate the raster pass so it shows up in
        # the Surfacing tab alongside Pocket and Plug previews. Same
        # auto-derived parameters the Surface plug backing dialog
        # uses (depth = glue_gap + 0.20 mm safety, panel = pocket
        # stock dimensions).
        self._refresh_surfacing_preview()
        # Push fresh values into the Setup Guide if it's visible.
        self._on_params_changed_refresh_guide()

        # Single endmill clearing layer per side. Pocket clears inside the
        # design; plug clears the complement around the design.
        # If we have a per-bit breakdown, use the colour-coded multi-bit
        # renderer so each bit's chains stand out and the legend appears.
        # Falls back to single-colour rendering for single-bit programs.
        for canvas in (self._pocket_canvas2d, self._pocket_canvas3d):
            canvas.set_vcarve([])
            if result.pocket.bit_chains and len(result.pocket.bit_chains) > 1:
                canvas.set_pocket_clear_by_bit(result.pocket.bit_chains)
            else:
                canvas.set_pocket_clear(result.pocket.clear_preview)
            canvas.set_boundary([])
        for canvas in (self._plug_canvas2d, self._plug_canvas3d):
            canvas.set_vcarve([])
            if result.plug.bit_chains and len(result.plug.bit_chains) > 1:
                canvas.set_pocket_clear_by_bit(result.plug.bit_chains)
            else:
                canvas.set_pocket_clear(result.plug.clear_preview)
            canvas.set_boundary([])

        # Build a recommendation message that prioritises coverage warnings,
        # since those mean the current bit will physically fail to clear regions.
        rec_lines: list[str] = []
        # First: surface the actual bit sequence per side. If the user asked
        # for roughing bits but the geometry made them redundant, the
        # pipeline auto-skips them. Without this line the user sees only the
        # finishing-bit's chains in the preview and assumes something broke.
        for label, side, requested in (
            ("Pocket", result.pocket, params.pocket_roughing_bits),
            ("Plug", result.plug, params.plug_roughing_bits),
        ):
            if not side.bit_chains:
                continue
            actual_names = [entry[0].name for entry in side.bit_chains]
            requested_with_finishing = list(requested) + [endmill.name]
            skipped = [n for n in requested_with_finishing if n not in actual_names]
            seq_descr = " → ".join(actual_names)
            if skipped:
                rec_lines.append(
                    f"{label} bit sequence: {seq_descr}. "
                    f"Auto-skipped: {', '.join(skipped)} "
                    f"(redundant — larger bits already cleared all reachable area)."
                )
            elif len(actual_names) > 1:
                rec_lines.append(f"{label} bit sequence: {seq_descr}.")
        cov = result.coverage
        if cov is not None and cov.has_warning:
            issue_parts = []
            if cov.uncleared:
                issue_parts.append(f"{cov.uncleared} can't be cleared at all")
            if cov.partially_cleared:
                issue_parts.append(f"{cov.partially_cleared} only partial")
            issue = ", ".join(issue_parts)
            rec_lines.append(
                f"⚠ {issue} of {cov.total_regions} regions with the {endmill.name}. "
                f"Thinnest region is {cov.thinnest_region_max_inset_mm * 2:.1f} mm wide; "
                f"use an endmill ≤ {cov.recommended_max_diameter_mm:.2f} mm to clear it."
            )
        # Stock-fit check: surface any side that doesn't fit on the user's
        # wood. These are warnings — the user can still cut, but the design
        # may overrun the stock edge.
        if result.stock_fit and result.stock_fit.has_issues:
            for issue in result.stock_fit.pocket_issues:
                rec_lines.append(f"⚠ Pocket fit: {issue}")
            for issue in result.stock_fit.plug_issues:
                rec_lines.append(f"⚠ Plug fit: {issue}")
        frag = result.fragility
        if frag is not None and frag.is_fragile:
            rec_lines.append(
                f"⚠ Plug-fragility check: {frag.fragile_fraction * 100:.0f}% of "
                f"the design is narrower than {frag.min_feature_mm:.1f} mm — "
                "the plug will likely break during glue-up. Use a larger bit, "
                "simplify the design, or auto-simplify before running."
            )
        if result.recommendation:
            r = result.recommendation
            rec_lines.append(r.rationale)
            if r.endmill is not None and r.endmill.name != endmill.name:
                rec_lines.append(f"→ try {r.endmill.name} for cleaner clearing.")
        # Cutting-parameters advisor: chip load + plunge rate + pass depth.
        # This is the bit-saver — without it the user has no warning that
        # their feed rate is fatiguing the carbide until the bit snaps.
        from cnc_inlay.core.cutting_recommendations import advise as _advise
        adv = _advise(endmill, pocket_mat, params)
        if adv.severity == "ok":
            rec_lines.append(
                f"Chip load: {adv.chip_load_mm:.4f} mm/tooth (safe range "
                f"{adv.chip_load_safe_lo:.4f}–{adv.chip_load_safe_hi:.4f}) — "
                f"speeds look healthy for this bit + wood."
            )
        else:
            for issue in adv.issues:
                rec_lines.append(issue)
        self._controls.set_recommendation(rec_lines)

        pocket_dur = _format_duration(
            result.pocket.cut_length_mm, result.pocket.air_length_mm, pocket_mat.feed_mm_min
        )
        plug_dur = _format_duration(
            result.plug.cut_length_mm, result.plug.air_length_mm, plug_mat.feed_mm_min
        )
        from cnc_inlay.core.units import fmt_length
        u = self._units
        summary = (
            f"Pocket: {pocket_dur}  •  cut {fmt_length(result.pocket.cut_length_mm, u, decimals=0)}"
            f"  •  travel {fmt_length(result.pocket.air_length_mm, u, decimals=0)}\n"
            f"Plug:   {plug_dur}  •  cut {fmt_length(result.plug.cut_length_mm, u, decimals=0)}"
            f"  •  travel {fmt_length(result.plug.air_length_mm, u, decimals=0)}\n"
            f"Endmill: {endmill.name}  (Ø {fmt_length(endmill.diameter_mm, u, decimals=3)})"
        )
        self._controls.set_summary(summary)
        self._set_status("Toolpaths ready.")

    def _invalidate_result(self) -> None:
        if self._result is None:
            return
        self._result = None
        for c in self._all_2d_canvases():
            c.clear_toolpaths()
        for c in self._all_3d_canvases():
            c.clear_toolpaths()
            c.clear_machine_position()
        self._controls.set_summary("")
        self._stop_simulation()
        self._control_panel.set_simulation_available(False)
        self._control_panel.clear_scrub_programs()
        self._scrub_indices = {}
        self._set_status("Parameters changed — click Preview to recompute.")

    def _refresh_scrub_programs(self, result, finishing_endmill) -> None:
        """Index both sides' G-code into bit segments + per-line XYZ, then
        push to the scrub bar. Called after Preview completes."""
        from cnc_inlay.gcode.program_index import index_program
        from cnc_inlay.ui.scrub_bar import BitSegment
        from cnc_inlay.ui.theme import color_for_bit_index

        self._scrub_indices = {}

        for side, side_result in (("pocket", result.pocket), ("plug", result.plug)):
            gcode = side_result.gcode
            if not gcode:
                continue
            # First bit name = first entry in bit_chains, or fall back to
            # the finishing endmill (single-bit programs have no roughing).
            first_bit_name = (
                side_result.bit_chains[0][0].name
                if side_result.bit_chains else finishing_endmill.name
            )
            idx = index_program(gcode, first_bit_name=first_bit_name)
            self._scrub_indices[side] = idx
            segments = [
                BitSegment(
                    start_line=s,
                    end_line=e,
                    bit_name=name,
                    color=color_for_bit_index(i),
                )
                for i, (s, e, name) in enumerate(idx.segments)
            ]
            self._control_panel.set_scrub_program(side, idx.total_lines, segments)

    def _on_scrub_preview(self, side: str, line: int) -> None:
        """Scrub handle moved — update the matching 3D canvas's bit-position
        marker so the operator can see where the cut would resume."""
        idx = getattr(self, "_scrub_indices", {}).get(side)
        if idx is None or not idx.positions:
            return
        # Clamp the line index — the scrub bar may be one past the last
        # line if the run is finished.
        i = max(0, min(line, len(idx.positions) - 1))
        x, y, z = idx.positions[i]
        canvas = (
            self._pocket_canvas3d if side == "pocket" else self._plug_canvas3d
        )
        # Use the same bit-tip cone marker the live machine position uses
        # so the visual is consistent between scrub-preview and real cut.
        endmill = self._controls.current_endmill()
        bit_d = endmill.diameter_mm if endmill is not None else 3.0
        canvas.set_machine_position(x, y, z, bit_d)

    def _on_run_from_line(self, side: str, line: int) -> None:
        """User clicked '▶ Run from cursor'. Confirm + start_stream(start_line=N)."""
        if self._result is None or not self._controller.is_connected:
            return
        idx = getattr(self, "_scrub_indices", {}).get(side)
        if idx is None:
            return
        # Find the bit in effect at this line for the confirmation prompt.
        bit_name = "(unknown)"
        for s, e, name in idx.segments:
            if s <= line < e:
                bit_name = name
                break
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Run from cursor")
        msg.setTextFormat(Qt.RichText)
        msg.setText(
            f"<b>Start the {side} cut at line {line:,}?</b>"
        )
        msg.setInformativeText(
            f"<p>Bit at this line: <b>{bit_name}</b>.</p>"
            "<p>Before clicking <b>Start</b>:</p>"
            "<ul>"
            f"<li>Make sure the <b>{bit_name}</b> is installed in the spindle.</li>"
            "<li>Manually jog the bit to a safe Z (well above the wood).</li>"
            "<li>Re-zero Z if you've changed bits since the last cut.</li>"
            "</ul>"
            "<p><b>The spindle is started automatically</b> with a 3-second "
            "ramp-up dwell before the first plunge — you don't need to send "
            "M3 manually.</p>"
            "<p>The bit will start its first move from wherever it is right "
            "now — there is no auto-return to the original cut path.</p>"
        )
        start = msg.addButton("▶ Start", QMessageBox.AcceptRole)
        msg.addButton(QMessageBox.Cancel)
        msg.exec()
        if msg.clickedButton() is not start:
            return
        # Reuse the existing run-on-machine path with an explicit start_line.
        self._run_on_machine(side, dry_run=False, start_line=line)

    # -------------------------------------------------------------- simulator

    def _effective_material(self, material, params: InlayParams, bit=None):
        from cnc_inlay.core.pipeline import _apply_speed_overrides
        if bit is None:
            bit = self._controls.current_endmill()
        return _apply_speed_overrides(material, params, bit)

    def _start_simulation(self, side: str) -> None:
        if self._result is None:
            return
        params = self._controls.current_params()
        if side == "pocket":
            chains = self._result.pocket.chains_full
            material = self._effective_material(
                self._controls.current_pocket_material(), params
            )
            target_canvases = (self._pocket_canvas2d, self._pocket_canvas3d)
            self._main_tabs.setCurrentIndex(0)
        else:
            chains = self._result.plug.chains_full
            material = self._effective_material(
                self._controls.current_plug_material(), params
            )
            target_canvases = (self._plug_canvas2d, self._plug_canvas3d)
            self._main_tabs.setCurrentIndex(1)
        for c in target_canvases:
            c.clear_machine_position()
        segs = build_segments(
            chains,
            feed_mm_min=material.feed_mm_min,
            plunge_mm_min=material.plunge_mm_min,
            safe_z_mm=params.safe_z_mm,
        )
        self._simulator.load(segs)
        self._sim_active_side = side
        self._control_panel.set_simulation_running(True)
        self._simulator.play()

    def _toggle_sim_pause(self) -> None:
        if self._simulator.is_running:
            self._simulator.pause()
        else:
            self._simulator.play()

    def _stop_simulation(self) -> None:
        self._simulator.stop()
        self._sim_active_side = None
        self._control_panel.set_simulation_running(False)
        self._control_panel.reset_simulation_progress()
        for c in (
            self._pocket_canvas2d, self._pocket_canvas3d,
            self._plug_canvas2d, self._plug_canvas3d,
        ):
            c.clear_machine_position()

    def _on_sim_position(self, x: float, y: float, z: float) -> None:
        if self._sim_active_side == "pocket":
            self._pocket_canvas2d.set_sim_position(x, y, z)
            self._pocket_canvas3d.set_sim_position(x, y, z)
        elif self._sim_active_side == "plug":
            self._plug_canvas2d.set_sim_position(x, y, z)
            self._plug_canvas3d.set_sim_position(x, y, z)

    def _on_sim_finished(self) -> None:
        self._control_panel.set_simulation_running(False)
        self._set_status("Simulation finished.")

    def _on_params_changed_refresh_guide(self) -> None:
        """Live-update the right-side Setup Guide while the user edits settings."""
        if self._setup_dock.isVisible():
            self._refresh_setup_guide()

    # ---------------------------------------------------- crash recovery

    def _maybe_offer_resume(self) -> None:
        cp = load_checkpoint()
        if cp is None or not cp.gcode:
            return
        progress_pct = (
            int(cp.cursor / max(1, cp.total_lines) * 100) if cp.total_lines else 0
        )
        from datetime import datetime
        when = datetime.fromtimestamp(cp.last_updated_at).strftime("%b %-d, %H:%M")
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Recover unfinished cut?")
        msg.setTextFormat(Qt.RichText)
        msg.setText(
            f"<h3 style='margin:0;'>An unfinished cut was found.</h3>"
            f"<p>The previous session was streaming the <b>{cp.side}</b> of "
            f"<b>{cp.design_filename or 'a design'}</b> when it stopped.</p>"
            f"<table style='margin-top:8px;'>"
            f"<tr><td style='color:#9aa0a6; padding-right:12px;'>Last update:</td><td>{when}</td></tr>"
            f"<tr><td style='color:#9aa0a6; padding-right:12px;'>Progress:</td>"
            f"<td><b>{cp.cursor:,} / {cp.total_lines:,} lines ({progress_pct}%)</b></td></tr>"
            f"<tr><td style='color:#9aa0a6; padding-right:12px;'>Endmill:</td><td>{cp.endmill_name}</td></tr>"
            f"</table>"
            f"<p style='margin-top:12px;'>"
            f"Choose <b>Resume</b> to load the project and prepare to continue from the "
            f"last chain boundary. You'll need to re-zero X/Y/Z before clicking Run.<br><br>"
            f"Choose <b>Discard</b> to delete the recovery file and start fresh.</p>"
        )
        resume_btn = msg.addButton("Resume", QMessageBox.AcceptRole)
        discard_btn = msg.addButton("Discard", QMessageBox.DestructiveRole)
        msg.addButton("Decide later", QMessageBox.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is discard_btn:
            clear_checkpoint()
            self._set_status("Recovery file discarded.")
            return
        if clicked is resume_btn:
            self._begin_resume(cp)

    def _begin_resume(self, cp: Checkpoint) -> None:
        """Load the project from the checkpoint and arm a 'resume from line N'
        run. Actual streaming starts when the user clicks the highlighted
        Run button after re-zeroing.
        """
        # Load the original project if it's still on disk; otherwise we can't
        # restore the design and the user has to re-create it.
        if cp.project_path and Path(cp.project_path).exists():
            from cnc_inlay.core.project import load_project
            try:
                project = load_project(cp.project_path)
                self._apply_project(project, source_path=Path(cp.project_path))
            except Exception as exc:
                QMessageBox.warning(
                    self, "Couldn't load project for resume", str(exc)
                )
                return
        # Stash the resume context so the next click on the appropriate Run
        # button picks up from the saved cursor.
        self._pending_resume = cp
        self._set_status(
            f"Resume armed: {cp.side} from line {cp.cursor:,} / {cp.total_lines:,}. "
            f"Re-zero X/Y/Z, then click Run {cp.side.title()}."
        )
        QMessageBox.information(
            self,
            "Re-zero before resuming",
            f"<b>Project loaded.</b><br><br>"
            f"Now: connect to the machine, re-zero <b>X / Y / Z</b> as carefully as "
            f"you can to the same reference points you used originally, then click "
            f"<b>▶ Run {cp.side.title()}</b>.<br><br>"
            f"The app will skip ahead to the chain boundary nearest line "
            f"<b>{cp.cursor:,}</b> and resume from there.",
        )

    # ---------------------------------------------------- run on machine

    def _confirm_unsafe_speeds(self, side: str) -> bool:
        """Refuse to start a real cut whose chip load / plunge / pass depth
        will break the bit. Three-button: Apply recommended / Run anyway / Cancel.

        Returns True if the run should proceed (either speeds were already safe,
        or the user applied the recommendation, or they explicitly chose to
        override).
        """
        from cnc_inlay.core.cutting_recommendations import advise

        endmill = self._controls.current_endmill()
        if endmill is None:
            return True
        # Use the side's own material — plug material may be harder than
        # pocket and need slower feeds.
        side_mat = (
            self._controls.current_pocket_material()
            if side == "pocket" else self._controls.current_plug_material()
        )
        if side_mat is None:
            return True
        params = self._controls.current_params()
        adv = advise(endmill, side_mat, params)
        if adv.severity == "ok":
            return True

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Cutting parameters likely to break the bit")
        title = (
            "<b>Cutting speeds are unsafe for this bit + wood.</b>"
            if adv.severity == "block"
            else "<b>Cutting speeds may stress the bit.</b>"
        )
        body = title + "<br><br>" + "<br><br>".join(adv.issues)
        body += (
            f"<br><br><b>Recommended for {endmill.name} in {side_mat.name}:</b><br>"
            f"&nbsp;&nbsp;Feed {adv.feed_safe_lo_mm_min:.0f}–{adv.feed_safe_hi_mm_min:.0f} mm/min<br>"
            f"&nbsp;&nbsp;Plunge ≤ {adv.plunge_safe_max_mm_min:.0f} mm/min<br>"
            f"&nbsp;&nbsp;Pass depth ≤ {adv.pass_depth_safe_max_mm:.2f} mm<br>"
            f"&nbsp;&nbsp;Spindle {adv.rpm_safe_lo}–{adv.rpm_safe_hi} RPM"
        )
        msg.setText(body)
        msg.setInformativeText(
            "Choose:<br>"
            "&nbsp;&nbsp;<b>Apply recommended</b> — set the spinboxes to safe values "
            "(you'll need to click <b>Preview Toolpaths</b> again before running)<br>"
            "&nbsp;&nbsp;<b>Run anyway</b> — proceed at your own risk<br>"
            "&nbsp;&nbsp;<b>Cancel</b> — go back and adjust manually"
        )
        apply_btn = msg.addButton("Apply recommended", QMessageBox.AcceptRole)
        proceed_btn = msg.addButton("Run anyway", QMessageBox.DestructiveRole)
        cancel_btn = msg.addButton(QMessageBox.Cancel)
        msg.setDefaultButton(apply_btn)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is cancel_btn:
            return False
        if clicked is proceed_btn:
            return True
        # Apply recommended values: pass-depth on the global Inlay control,
        # RPM/feed/plunge on the FINISHING bit's per-bit Speeds tab. Middle
        # of the safe range for feed, 40% of recommended feed for plunge,
        # safe pass depth, middle of safe RPM. After this the user must
        # re-Preview before they run.
        u = self._controls.units
        target_feed = (adv.feed_safe_lo_mm_min + adv.feed_safe_hi_mm_min) / 2
        target_plunge = target_feed * 0.40
        target_pass = adv.pass_depth_safe_max_mm * 0.85
        target_rpm = (adv.rpm_safe_lo + adv.rpm_safe_hi) // 2
        from cnc_inlay.core.units import length_to_display
        self._controls.pass_spin.setValue(length_to_display(target_pass, u))
        self._controls.apply_cutting_recommendation(
            rpm=target_rpm,
            feed_mm_min=target_feed,
            plunge_mm_min=target_plunge,
        )
        QMessageBox.information(
            self, "Speeds updated",
            "The Speeds and Pass Depth controls have been adjusted to safe "
            "values. Click <b>Preview Toolpaths</b> to regenerate the G-code, "
            "then run it.",
        )
        return False  # don't proceed — the user needs to re-preview first

    def _confirm_or_simplify_fragile(self, side: str) -> bool:
        """Three-button gate when fragility fails: cancel / simplify / proceed.

        Returns True if the run should continue. If the user picks Simplify,
        rebuilds the toolpath with auto-simplified geometry and returns True
        once the new pipeline result is in place.
        """
        from cnc_inlay.core.inlay_minimums import auto_simplify

        frag = self._result.fragility
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Plug fragility warning")
        msg.setText(
            f"<b>This design will likely produce a fragile plug.</b><br><br>"
            f"{frag.fragile_fraction * 100:.0f}% of the design is narrower than "
            f"{frag.min_feature_mm:.1f} mm — the recommended minimum for the "
            f"current endmill. Thin features tend to snap during glue-up or "
            f"plane-down, ruining the inlay."
        )
        msg.setInformativeText(
            "Choose:<br>"
            "&nbsp;&nbsp;<b>Auto-simplify</b> — drop features below the threshold "
            "and regenerate toolpaths (recommended)<br>"
            "&nbsp;&nbsp;<b>Run anyway</b> — proceed at your own risk<br>"
            "&nbsp;&nbsp;<b>Cancel</b> — go back and edit the design"
        )
        simplify_btn = msg.addButton("Auto-simplify", QMessageBox.AcceptRole)
        proceed_btn = msg.addButton("Run anyway", QMessageBox.DestructiveRole)
        cancel_btn = msg.addButton(QMessageBox.Cancel)
        msg.setDefaultButton(simplify_btn)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is cancel_btn:
            return False
        if clicked is proceed_btn:
            return True
        # Auto-simplify path: rewrite the design polygon, rerun the pipeline.
        try:
            simplified = auto_simplify(
                self._design.polygon, self._controls.current_endmill().diameter_mm
            )
        except Exception as exc:
            QMessageBox.critical(self, "Auto-simplify failed", str(exc))
            return False
        if simplified.is_empty:
            QMessageBox.critical(
                self, "Auto-simplify produced nothing",
                "Every feature in this design is below the minimum width — "
                "there's nothing left to mill. Choose a larger bit or a "
                "different design.",
            )
            return False
        # Replace the design's polygon (keep original_polygon untouched so
        # the user can still resize from the natural source) and re-preview.
        self._design.polygon = simplified
        minx, miny, maxx, maxy = simplified.bounds
        self._design.width_mm = maxx - minx
        self._design.height_mm = maxy - miny
        self._set_status("Auto-simplified design — regenerating toolpaths…")
        self._on_preview()
        # If the rerun still flags fragility (shouldn't, but be safe), abort.
        if self._result is None:
            return False
        if self._result.fragility and self._result.fragility.is_fragile:
            QMessageBox.warning(
                self, "Still fragile after simplify",
                "Auto-simplify did not eliminate the fragile features. "
                "Try a larger endmill or a different design.",
            )
            return False
        return True

    def _run_on_machine(self, side: str, *, dry_run: bool, start_line: int = 0) -> None:
        # License gate: Run on Machine is one of the paid features. Dry-run
        # (simulator) is left FREE so trial users can preview machine motion.
        if not dry_run and not self._require_license_or_show_buy("Run on Machine"):
            return
        # If a resume is armed for this side, branch into the resume flow.
        if (
            not dry_run
            and self._pending_resume is not None
            and self._pending_resume.side == side
        ):
            self._resume_run(side)
            return
        if self._result is None:
            return
        if not self._controller.is_connected:
            QMessageBox.information(
                self, "Not connected", "Connect to the CNC machine first."
            )
            return
        side_result = self._result.pocket if side == "pocket" else self._result.plug
        gcode = side_result.gcode
        if not gcode.strip():
            QMessageBox.warning(self, "Empty toolpath", "No G-code to stream.")
            return

        # Plug-fragility gate: refuse to start a real cut on a design whose
        # plug will break. Dry runs don't actually cut, so let those through.
        # Pocket-only runs are also gated, since the pocket cavity matches
        # the plug shape — if the plug is filigree, the pocket walls are
        # equally fragile and won't survive either.
        if not dry_run and self._result.fragility and self._result.fragility.is_fragile:
            if not self._confirm_or_simplify_fragile(side):
                return

        # Bit-saver gate: refuse a real cut when chip load / plunge / pass-depth
        # are unsafe. Use the side's actual material so plug-side gets checked
        # against plug wood (could be harder than pocket wood and need slower
        # feeds even though the pocket cut was fine).
        if not dry_run:
            if not self._confirm_unsafe_speeds(side):
                return

        params = self._controls.current_params()
        all_chains = side_result.chains_full
        if all_chains:
            min_z_mm = min(p[2] for c in all_chains for p in c)
            xs = [p[0] for c in all_chains for p in c]
            ys = [p[1] for c in all_chains for p in c]
            x_extent = max(xs) - min(xs)
            y_extent = max(ys) - min(ys)
        else:
            min_z_mm = 0.0
            x_extent = y_extent = 0.0

        max_depth_mm = abs(min_z_mm)

        from cnc_inlay.core.units import Units, fmt_dimensions, fmt_length
        u = self._units
        max_depth_str = fmt_length(max_depth_mm, u, decimals=3)
        extents_str = fmt_dimensions(x_extent, y_extent, u)

        # Per-side stock thickness check using the user's caliper-measured
        # value for the side being cut. 0 = check disabled.
        SAFETY_MARGIN_MM = 0.5
        side_stock_mm = (
            params.pocket_thickness_mm if side == "pocket" else params.plug_thickness_mm
        )
        if not dry_run and side_stock_mm > 0:
            if max_depth_mm + SAFETY_MARGIN_MM >= side_stock_mm:
                stock_str = fmt_length(side_stock_mm, u, decimals=3)
                QMessageBox.critical(
                    self,
                    "Cut would go through the wood",
                    f"<b>Refusing to run.</b><br><br>"
                    f"The {side} program plunges to <b>{max_depth_str}</b>, "
                    f"but the {side} stock is only "
                    f"<b>{stock_str}</b> thick (with {SAFETY_MARGIN_MM:g} mm "
                    f"safety margin).<br><br>"
                    f"<b>Fixes:</b><br>"
                    f"• Reduce pocket depth in the Inlay group<br>"
                    f"• Re-measure the {side} stock with calipers and update "
                    f"its thickness field in the Materials group",
                )
                return

        if dry_run:
            gcode = _dry_run_gcode(gcode, safe_z_mm=params.safe_z_mm)
            confirm = QMessageBox.question(
                self,
                f"Dry Run {side}",
                f"<b>Trace the {side} toolpath at safe Z, no cutting?</b>"
                "<br><br>"
                f"The bit will hover at <b>+{fmt_length(params.safe_z_mm, u, decimals=2)}</b> "
                "above your work zero and trace the entire XY pattern.<br><br>"
                f"<b>XY extents:</b> {extents_str}<br><br>"
                "Watch the bit. If it visibly dips into the wood at any point, "
                "<b>your Z-zero is wrong</b> — abort immediately.<br><br>"
                "Click Yes to start.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if confirm != QMessageBox.Yes:
                return
            self._controller.run_program(gcode)
            self._set_status(f"Dry-running {side} (no cut)…")
            return

        confirm = QMessageBox.question(
            self,
            f"Run {side}",
            f"<b>Start cutting the {side} now?</b>"
            "<br><br>"
            "<b>This program will plunge the bit to:</b>"
            f"<br>&nbsp;&nbsp;&nbsp;Z = <span style='color:#e0524a; font-size:14px;'>"
            f"−{max_depth_str}</span> below the current work zero"
            "<br><br>"
            f"<b>XY extents:</b> {extents_str}"
            "<br><br>"
            "<b style='color:#f5a623;'>Recommended: click 'Dry Run' first</b> "
            "to verify the bit doesn't hit the wood unexpectedly."
            "<br><br>"
            "Confirm before starting the real cut:<br>"
            "• Correct stock is clamped on the bed<br>"
            "• X/Y/Z work zero is set with the bit "
            "<b>touching the top of the wood</b><br>"
            "• Endmill is installed and tightened<br>"
            "• Stock is thicker than the depth above<br>"
            "• You can reach the e-stop<br><br>"
            "The cut starts as soon as you click <b>Yes</b>.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._streaming_side = side
        # Switch to the active side's tab so the live cone is visible immediately.
        self._main_tabs.setCurrentIndex(0 if side == "pocket" else 1)
        # Remember the resume offset so progress events can be mapped from
        # streamer-line space (which prepends a 5-line preamble for any
        # non-zero start) back to original-program line space — needed for
        # the scrub bar to advance correctly and for an accurate "elapsed
        # / total estimated" readout.
        self._streaming_start_line = start_line
        # Streamer prepends a 7-line modal-state + M3 + dwell preamble on
        # any non-zero start_line. Keep this in sync with control/streamer.py.
        self._streaming_preamble_len = 7 if start_line > 0 else 0

        # Build a checkpoint for crash recovery — saved every couple of seconds
        # while the cut runs.
        endmill = self._controls.current_endmill()
        pocket_mat = self._controls.current_pocket_material()
        plug_mat = self._controls.current_plug_material()
        checkpoint = Checkpoint(
            side=side,
            gcode=gcode,
            cursor=0,
            total_lines=0,
            project_path=str(self._current_project_path) if self._current_project_path else None,
            design_path=str(self._design.source_path) if self._design and self._design.source_path else None,
            design_filename=self._design.source_path.name if self._design and self._design.source_path else None,
            pocket_material=pocket_mat.name if pocket_mat else "",
            plug_material=plug_mat.name if plug_mat else "",
            endmill_name=endmill.name if endmill else "",
            pocket_thickness_mm=params.pocket_thickness_mm,
            plug_thickness_mm=params.plug_thickness_mm,
            pocket_depth_mm=params.pocket_depth_mm,
        )
        # checkpoint cursor mirrors start_line so a recovered run from a
        # mid-program start position picks up at the right place.
        checkpoint.cursor = start_line
        self._controller.run_program(gcode, start_line=start_line, checkpoint=checkpoint)
        if start_line > 0:
            self._set_status(f"Streaming {side} to machine from line {start_line:,}…")
        else:
            self._set_status(f"Streaming {side} to machine…")

    def _resume_run(self, side: str) -> None:
        """Run a stream resumed from the saved checkpoint cursor."""
        cp = self._pending_resume
        if cp is None or cp.side != side:
            return
        if not self._controller.is_connected:
            QMessageBox.information(
                self, "Not connected", "Connect to the CNC machine first."
            )
            return
        start_line = find_resume_line(cp.gcode, cp.cursor)
        confirm = QMessageBox.question(
            self,
            f"Resume {side}",
            f"<b>Resume the {side} cut from line {start_line:,}?</b>"
            f"<br><br>"
            f"Original total: <b>{cp.total_lines:,}</b> lines.<br>"
            f"Was at: line <b>{cp.cursor:,}</b> ({int(cp.cursor / max(1, cp.total_lines) * 100)}% complete).<br>"
            f"Resuming from chain boundary at line: <b>{start_line:,}</b>.<br><br>"
            f"<b>Verify before clicking Yes:</b><br>"
            f"• Work zero is set to the SAME reference as the original run<br>"
            f"• Stock has not shifted<br>"
            f"• Same endmill installed<br><br>"
            f"If alignment is off, the new cut won't match the existing pocket.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        # Build a new checkpoint that picks up where the old one left off.
        new_cp = Checkpoint(**cp.__dict__)
        new_cp.cursor = start_line
        self._streaming_side = side
        self._main_tabs.setCurrentIndex(0 if side == "pocket" else 1)
        self._controller.run_program(cp.gcode, start_line=start_line, checkpoint=new_cp)
        self._pending_resume = None
        self._set_status(f"Resumed {side} from line {start_line:,}.")

    def _on_machine_connection_changed(self, connected: bool, _msg: str) -> None:
        self._control_panel.set_run_available(self._result is not None)
        if not connected:
            for c in self._all_2d_canvases() + self._all_3d_canvases():
                c.clear_machine_position()

    def _on_stream_started(self, total: int) -> None:
        import time
        self._control_panel.set_streaming(True)
        self._stream_start_time = time.monotonic()
        # Map streamer total → original-program total (compensate for the
        # 5-line preamble the streamer prepends on a non-zero start_line).
        idx = getattr(self, "_scrub_indices", {}).get(self._streaming_side)
        original_total = idx.total_lines if idx is not None else total
        self._stream_last_cursor = self._streaming_start_line
        self._stream_last_total = original_total
        # Estimated remaining time = total cumulative seconds minus
        # cumulative-at-start, so a "Run from cursor" at line N reflects
        # only the work that's left, not the full side. Falls back to the
        # whole-side estimate if there's no index for this side.
        if idx is not None and idx.cumulative_seconds:
            total_s = idx.cumulative_seconds[-1]
            start_s = (
                idx.cumulative_seconds[self._streaming_start_line]
                if self._streaming_start_line < len(idx.cumulative_seconds)
                else total_s
            )
            self._stream_estimated_total_s = max(0.0, total_s - start_s)
        else:
            self._stream_estimated_total_s = (
                self._pocket_seconds if self._streaming_side == "pocket"
                else self._plug_seconds if self._streaming_side == "plug"
                else 0.0
            )
        self._stream_clock.start()
        self._control_panel.set_stream_status(
            f"Streaming — {total:,} lines queued, starting…"
        )
        self._set_status(f"Cutting in progress — {total:,} G-code lines queued.")
        self._sleep_inhibitor.acquire()

    def _on_stream_progress(self, cursor: int, total: int) -> None:
        # Streamer cursor/total are in streamer-line space (preamble +
        # remainder for resumes). Map back to original-program line space
        # so the scrub bar advances correctly and the percentage matches
        # the program the user is looking at.
        original_line = self._streaming_start_line + max(
            0, cursor - self._streaming_preamble_len
        )
        original_total = self._stream_last_total or total
        self._control_panel.set_stream_progress(original_line, original_total)
        self._stream_last_cursor = original_line
        self._stream_last_total = original_total
        # Refresh the elapsed/ETA text immediately rather than waiting for
        # the next 1 Hz tick — feels snappier on fast-streaming sections.
        self._render_stream_time_status()

    def _on_tool_change_required(self, bit_name: str) -> None:
        """Cut paused on M0 for a bit swap. Pop the modeless instructions
        dialog and re-enable jog so the operator can navigate to uncut wood
        and re-zero Z. Both stay active until they click Resume."""
        self._control_panel.set_paused_for_tool_change(True)
        self._tool_change_dialog.show_for_bit(bit_name)

    def _tick_stream_clock(self) -> None:
        """Fires once a second so elapsed-time counts up even when no new
        progress events arrive (e.g. mid-line during a long G1 move)."""
        if self._stream_start_time is None:
            return
        self._render_stream_time_status()

    def _render_stream_time_status(self) -> None:
        if self._stream_start_time is None:
            return
        import time
        elapsed = time.monotonic() - self._stream_start_time
        cursor = self._stream_last_cursor
        total = self._stream_last_total
        fraction = (cursor / total) if total > 0 else 0.0
        # Show elapsed against the static total-time estimate from the
        # simulator. Earlier versions extrapolated a "remaining" time from
        # lines-processed, but G-code lines vary by orders of magnitude in
        # cut time (G0 rapids vs long G1 sweeps), so the remaining number
        # would actually GROW as the cut got into the slow real-cutting
        # phase — the opposite of what users expect from a countdown.
        total_est = self._stream_estimated_total_s
        if total_est > 0:
            time_text = f"⏱ {_format_clock(elapsed)} / {_format_clock(total_est)} estimated"
        else:
            time_text = f"⏱ {_format_clock(elapsed)} elapsed"
        text = (
            f"Line {cursor:,} / {total:,} ({fraction * 100:.0f}%)  •  "
            f"{time_text}"
        )
        self._control_panel.set_stream_status(text)

    def _on_stream_completed(self) -> None:
        self._control_panel.set_streaming(False)
        self._stream_clock.stop()
        elapsed_str = ""
        if self._stream_start_time is not None:
            import time
            elapsed_str = f" in {_format_clock(time.monotonic() - self._stream_start_time)}"
        self._stream_start_time = None
        self._control_panel.set_stream_progress(0, 0)
        self._control_panel.set_stream_status(f"Idle — last run completed ✓{elapsed_str}")
        self._streaming_side = None
        for c in self._all_2d_canvases() + self._all_3d_canvases():
            c.clear_machine_position()
        self._sleep_inhibitor.release()
        self._set_status(f"Cut completed{elapsed_str}.")

    def _on_stream_aborted(self, reason: str) -> None:
        self._control_panel.set_streaming(False)
        self._stream_clock.stop()
        self._stream_start_time = None
        self._control_panel.set_stream_progress(0, 0)
        self._control_panel.set_stream_status(f"Aborted ({reason})")
        self._streaming_side = None
        for c in self._all_2d_canvases() + self._all_3d_canvases():
            c.clear_machine_position()
        self._sleep_inhibitor.release()
        self._set_status(f"Cut aborted: {reason}")

    def _on_grbl_response(self, resp) -> None:
        """Mirror every GRBL line into the on-screen console panel."""
        kind = resp.kind.value if hasattr(resp.kind, "value") else "other"
        text = resp.raw or resp.text or ""
        if text.strip():
            self._control_panel.append_console_received(text.strip(), kind=kind)

    def _on_grbl_unlock(self) -> None:
        self._control_panel.append_console_sent("$X")
        self._controller.send_raw_line("$X")

    def _on_grbl_raw_command(self, cmd: str) -> None:
        self._control_panel.append_console_sent(cmd)
        self._controller.send_raw_line(cmd)

    def _on_grbl_settings_received(self, settings: dict, raw_lines: list | None = None) -> None:
        """Show a dialog with GRBL settings, highlighting steps/mm and Z-axis health."""
        raw_lines = raw_lines or []
        if not settings:
            captured = "<br>".join(_escape_html_simple(l) for l in raw_lines if l.strip())
            captured_block = (
                f"<br><br><b>Captured during 3-second window:</b>"
                f"<pre style='background:#15171b; color:#d8dce2; padding:8px; "
                f"border-radius:4px; font-size:11px; max-height:200px;'>"
                f"{captured if captured else '<i>(nothing — silence on the wire)</i>'}"
                f"</pre>"
            )
            QMessageBox.warning(
                self,
                "No GRBL settings received",
                "GRBL didn't return any <code>$N=value</code> lines within 3 seconds."
                "<br><br>"
                "<b>What this might mean:</b><br>"
                "• The machine is in an Alarm state — click <b>Unlock</b> ($X) and try again.<br>"
                "• A previous job is still buffered — disconnect, power-cycle the Lunyee, reconnect.<br>"
                "• The firmware isn't standard GRBL — see the captured output below.<br>"
                "• You can also try <b>Send…</b> with the literal text <code>$$</code> "
                "and watch the console panel — every received line shows up there."
                f"{captured_block}",
            )
            return

        # Friendly names for the most useful settings.
        names = {
            100: "X axis steps/mm",
            101: "Y axis steps/mm",
            102: "Z axis steps/mm",
            110: "X max feed rate (mm/min)",
            111: "Y max feed rate (mm/min)",
            112: "Z max feed rate (mm/min)",
            120: "X max acceleration",
            121: "Y max acceleration",
            122: "Z max acceleration",
            130: "X max travel (mm)",
            131: "Y max travel (mm)",
            132: "Z max travel (mm)",
            10: "Status report mask",
            20: "Soft limits",
            21: "Hard limits",
            22: "Homing cycle",
            30: "Spindle max RPM",
            31: "Spindle min RPM",
        }

        # Z-axis steps/mm sanity check. Lunyee 3018 PRO MAX is typically 400.
        z_steps = settings.get(102)
        if z_steps is None:
            z_diag = "<i>$102 not reported — unable to assess.</i>"
        elif z_steps < 250:
            z_diag = (
                f"<span style='color:#e0524a; font-weight:700;'>"
                f"⚠ $102 = {z_steps:g} looks LOW.</span><br>"
                "Many Lunyee 3018 PRO MAX machines use 400 or 800 steps/mm. "
                "If your Z movements have been ~2× what they should be, "
                f"sending <code>$102={z_steps * 2:g}</code> is the likely fix."
            )
        elif z_steps > 1200:
            z_diag = (
                f"<span style='color:#f5a623;'>"
                f"$102 = {z_steps:g} is unusually HIGH. "
                "If Z moves are too small, try halving it.</span>"
            )
        else:
            z_diag = (
                f"<span style='color:#3eea7a;'>"
                f"✓ $102 = {z_steps:g} is in the typical range for the Lunyee 3018.</span>"
            )

        # Build a human-readable list of the most relevant settings.
        relevant_ids = [10, 20, 21, 22, 30, 31, 100, 101, 102, 110, 111, 112,
                        120, 121, 122, 130, 131, 132]
        rows = []
        for i in relevant_ids:
            if i in settings:
                label = names.get(i, f"setting ${i}")
                emphasis = "background:#3a3f47;" if i == 102 else ""
                rows.append(
                    f"<tr style='{emphasis}'>"
                    f"<td style='padding:2px 8px; color:#9aa0a6;'>${i}</td>"
                    f"<td style='padding:2px 8px;'>{label}</td>"
                    f"<td style='padding:2px 8px; text-align:right;"
                    f" font-family:monospace;'>{settings[i]:g}</td></tr>"
                )
        # Plus everything else in compact form.
        other_ids = sorted(set(settings.keys()) - set(relevant_ids))
        for i in other_ids:
            rows.append(
                f"<tr><td style='padding:1px 8px; color:#9aa0a6;'>${i}</td>"
                f"<td colspan='2' style='padding:1px 8px; font-family:monospace;'>"
                f"{settings[i]:g}</td></tr>"
            )

        body = (
            f"<h3 style='margin:0;'>GRBL configuration</h3>"
            f"<p style='margin:8px 0;'>{z_diag}</p>"
            f"<table style='border-collapse:collapse;'>"
            f"<tr><th style='padding:2px 8px; text-align:left;'>ID</th>"
            f"<th style='padding:2px 8px; text-align:left;'>Setting</th>"
            f"<th style='padding:2px 8px; text-align:right;'>Value</th></tr>"
            f"{''.join(rows)}"
            f"</table>"
            f"<p style='margin-top:12px; color:#9aa0a6; font-size:10px;'>"
            f"To change a setting, send <code>$N=value</code> from a sender's "
            f"command line (e.g. <code>$102=400</code>).</p>"
        )
        box = QMessageBox(self)
        box.setWindowTitle("GRBL Configuration")
        box.setIcon(QMessageBox.Information)
        box.setTextFormat(Qt.RichText)
        box.setText(body)
        box.setStandardButtons(QMessageBox.Close)
        box.exec()

    def _on_machine_status_changed(self, status) -> None:
        """Route live work-coordinate position to the active side's canvases."""
        if self._streaming_side is None:
            return
        wpos = status.effective_wpos
        if wpos is None:
            return
        endmill = self._controls.current_endmill()
        bit_d = endmill.diameter_mm if endmill else 3.0
        if self._streaming_side == "pocket":
            self._pocket_canvas2d.set_machine_position(wpos.x, wpos.y, wpos.z, bit_d)
            self._pocket_canvas3d.set_machine_position(wpos.x, wpos.y, wpos.z, bit_d)
        elif self._streaming_side == "plug":
            # The plug G-code already carries mirrored X coordinates (plug
            # chains are computed from the X-flipped design), and the plug
            # canvas renders those mirrored chains directly. So WPos.x —
            # which the machine reports in the same coord frame as the
            # G-code it's running — already matches the canvas X. Earlier
            # versions negated X here, which made the indicator appear
            # mirrored relative to the actual cut. The simulator
            # (_on_sim_position below) does no mirror either, for the same
            # reason — kept consistent here.
            self._plug_canvas2d.set_machine_position(wpos.x, wpos.y, wpos.z, bit_d)
            self._plug_canvas3d.set_machine_position(wpos.x, wpos.y, wpos.z, bit_d)
        elif self._streaming_side == "surfacing":
            # Surfacing uses whatever bit the user picked in the
            # Surface plug backing dialog — it's stored on the canvas
            # in self._surfacing_bit_diameter_mm when the plan is pushed.
            sd = getattr(self, "_surfacing_bit_diameter_mm", 6.0)
            self._surfacing_canvas3d.set_machine_position(wpos.x, wpos.y, wpos.z, sd)

    # ----------------------------------------------------------- setup guide

    def _open_setup_dialog(self) -> None:
        """Toggle the right-side Setup Guide dock and refresh its contents."""
        if self._setup_dock.isVisible():
            self._setup_dock.hide()
            return
        self._refresh_setup_guide()
        self._setup_dock.show()
        self._setup_dock.raise_()

    def _refresh_setup_guide(self) -> None:
        """Push the latest project state into the Setup Guide widget. Cheap to
        call on every parametersChanged emit because the widget just updates
        an HTML string."""
        if self._design is None:
            self._setup_guide.set_empty_state()
            return
        params = self._controls.current_params()
        endmill = self._controls.current_endmill()
        pocket_mat = self._controls.current_pocket_material()
        plug_mat = self._controls.current_plug_material()
        if endmill is None or pocket_mat is None or plug_mat is None:
            self._setup_guide.set_empty_state()
            return
        pocket_dim = (self._design.width_mm, self._design.height_mm)
        plug_dim = (
            self._design.width_mm + 2 * params.boundary_offset_mm,
            self._design.height_mm + 2 * params.boundary_offset_mm,
        )
        self._setup_guide.refresh(
            design_name=self._design.basename,
            pocket_dim=pocket_dim,
            plug_dim=plug_dim,
            endmill=endmill,
            pocket_material=pocket_mat,
            plug_material=plug_mat,
            params=params,
            pocket_seconds=self._pocket_seconds,
            plug_seconds=self._plug_seconds,
            units=self._units,
        )

    # -------------------------------------------------------------- status

    def _set_status(self, msg: str) -> None:
        self.statusBar().showMessage(msg)
