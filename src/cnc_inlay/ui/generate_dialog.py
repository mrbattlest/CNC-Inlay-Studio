"""Modal dialog: generate an inlay-ready SVG from a natural-language prompt.

Two-step pipeline runs on a worker QThread so the UI stays responsive:
  1. Call Gemini → get a black-on-white silhouette PNG.
  2. Vectorize the PNG → produce an SVG of solid black filled paths.

The resulting SVG markup is exposed via ``svg_text`` after the dialog accepts.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from cnc_inlay.ai.image_generator import DetailLevel, generate_silhouette_png
from cnc_inlay.ai.secrets_store import get_api_key, get_model
from cnc_inlay.ai.vectorizer import png_to_svg
from cnc_inlay.core.units import load_settings, save_settings

_SETTINGS_KEY_DETAIL = "ai_detail_level"

# Slider position → (DetailLevel, short tick label, full hint).
# Five distinct stops so the slider reads as a real spectrum (not three
# disguised radio buttons) and each step represents a meaningful change
# in both the prompt and the matching CNC bit size.
_DETAIL_LEVELS: list[tuple[DetailLevel, str, str]] = [
    (
        DetailLevel.BOLD,
        "Bold",
        "Chunky stencil · no internal cutouts · safe with any bit ≥6 mm.",
    ),
    (
        DetailLevel.SIMPLE,
        "Simple",
        "Clean stencil · 1–2 defining cutouts · for 3–4 mm endmills.",
    ),
    (
        DetailLevel.DETAILED,
        "Detailed",
        "Paper-cut style · several cutouts for character · for 2 mm endmills.",
    ),
    (
        DetailLevel.RICH,
        "Rich",
        "Decorative paper-cut · many internal cutouts · for 1.5 mm endmills.",
    ),
    (
        DetailLevel.ORNATE,
        "Ornate",
        "Scherenschnitte / wood-block · ornate internal cutouts · "
        "for 1 mm endmills with a V-bit pre-pass.",
    ),
]


class _GenerateWorker(QObject):
    progressed = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, prompt: str, api_key: str, detail: DetailLevel) -> None:
        super().__init__()
        self._prompt = prompt
        self._api_key = api_key
        self._detail = detail

    def run(self) -> None:
        try:
            self.progressed.emit("Asking Gemini for an image…")
            png = generate_silhouette_png(
                self._prompt, api_key=self._api_key, detail=self._detail,
            )
            self.progressed.emit("Tracing the silhouette into vectors…")
            svg = png_to_svg(png)
            self.finished.emit(svg)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class GenerateDialog(QDialog):
    """Prompt entry + Generate button + status. Returns ``svg_text`` on accept."""

    EXAMPLES = [
        "a butterfly",
        "an oak leaf",
        "a fox in profile",
        "a deer head",
        "a Celtic knot",
        "a feather",
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Generate inlay SVG with Gemini")
        self.setMinimumWidth(640)
        self.svg_text: str | None = None
        self._thread: QThread | None = None
        self._worker: _GenerateWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(
            f"Describe what you'd like to inlay. Gemini "
            f"(<i>{get_model()}</i>) generates a silhouette image, then we "
            "trace it into an inlay SVG."
            "<br><br>"
            "<b>What works:</b> single iconic silhouettes — animals in "
            "profile, leaves, geometric symbols, monograms, simple objects."
            "<br>"
            "<b>What doesn't:</b> scenes, multi-figure compositions, anything "
            "with text. Match the <b>Detail</b> slider below to your finest "
            "endmill — finer detail needs a finer bit."
        )
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.prompt_edit = QLineEdit()
        self.prompt_edit.setPlaceholderText("e.g. a butterfly")
        self.prompt_edit.returnPressed.connect(self._on_generate)
        layout.addWidget(self.prompt_edit)

        # --- Detail slider --------------------------------------------------
        # Five discrete stops. We expose them as a slider with visible tick
        # labels so the user reads it as a spectrum and can see exactly how
        # many positions exist before they move it. Each tick maps 1:1 to a
        # DetailLevel; intermediate values aren't possible because the
        # underlying prompt is per-stop.
        layout.addWidget(QLabel("Detail:"))

        # Restore last-used level. Default = Detailed (index 2 of 5).
        saved = (load_settings().get(_SETTINGS_KEY_DETAIL) or "").lower()
        initial_idx = next(
            (i for i, (lvl, _, _) in enumerate(_DETAIL_LEVELS)
             if lvl.value == saved),
            2,  # Detailed
        )

        self.detail_slider = QSlider(Qt.Horizontal)
        self.detail_slider.setRange(0, len(_DETAIL_LEVELS) - 1)
        self.detail_slider.setSingleStep(1)
        self.detail_slider.setPageStep(1)
        self.detail_slider.setTickPosition(QSlider.TicksBelow)
        self.detail_slider.setTickInterval(1)
        self.detail_slider.setValue(initial_idx)
        layout.addWidget(self.detail_slider)

        # Per-tick labels underneath. Each one sits in an evenly-stretched
        # row cell so it lines up roughly with its tick. The active label
        # gets the orange accent so the user sees the current position
        # without having to read the slider thumb's pixel x.
        ticks_row = QWidget()
        ticks_h = QHBoxLayout(ticks_row)
        ticks_h.setContentsMargins(0, 0, 0, 0)
        ticks_h.setSpacing(0)
        self._tick_labels: list[QLabel] = []
        for i, (_lvl, name, _hint) in enumerate(_DETAIL_LEVELS):
            cell = QLabel(name)
            cell.setAlignment(
                Qt.AlignLeft if i == 0
                else Qt.AlignRight if i == len(_DETAIL_LEVELS) - 1
                else Qt.AlignHCenter
            )
            cell.setStyleSheet("color: #9aa0a6; font-size: 11px;")
            ticks_h.addWidget(cell, 1)
            self._tick_labels.append(cell)
        layout.addWidget(ticks_row)

        self.detail_hint = QLabel(_DETAIL_LEVELS[initial_idx][2])
        self.detail_hint.setStyleSheet(
            "color: #9aa0a6; font-size: 11px; padding-top: 2px;"
        )
        self.detail_hint.setWordWrap(True)
        layout.addWidget(self.detail_hint)
        self.detail_slider.valueChanged.connect(self._on_detail_changed)
        # Apply the initial highlight to the active tick label.
        self._on_detail_changed(initial_idx)

        examples_row = QWidget()
        ex_h = QHBoxLayout(examples_row)
        ex_h.setContentsMargins(0, 0, 0, 0)
        ex_h.setSpacing(4)
        ex_h.addWidget(QLabel("Try:"))
        for ex in self.EXAMPLES:
            btn = QPushButton(ex)
            btn.setStyleSheet(
                "QPushButton { background:#2a2d33; color:#9aa0a6; "
                "border:1px solid #3a3f47; border-radius:10px; "
                "padding:2px 8px; font-size:11px; }"
                "QPushButton:hover { color:#e8e8ea; border-color:#f5a623; }"
            )
            btn.clicked.connect(lambda _=False, t=ex: self.prompt_edit.setText(t))
            ex_h.addWidget(btn)
        ex_h.addStretch(1)
        layout.addWidget(examples_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.RichText)
        # Allow the user to select / copy the message — important when the
        # model returns a verbose error and they want to share it.
        self.status_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.button_box = QDialogButtonBox()
        self.generate_btn = self.button_box.addButton(
            "Generate", QDialogButtonBox.AcceptRole
        )
        self.cancel_btn = self.button_box.addButton(QDialogButtonBox.Cancel)
        self.generate_btn.clicked.connect(self._on_generate)
        self.cancel_btn.clicked.connect(self.reject)
        layout.addWidget(self.button_box)

    # ---------------------------------------------------------------- internals

    def _on_generate(self) -> None:
        prompt = self.prompt_edit.text().strip()
        if not prompt:
            self.status_label.setText(
                "<span style='color:#e0524a'>Enter a prompt first.</span>"
            )
            return
        api_key = get_api_key()
        if not api_key:
            self.status_label.setText(
                "<span style='color:#e0524a'>No API key configured. "
                "Open Settings → Preferences first.</span>"
            )
            return

        self._set_busy(True)

        detail = _DETAIL_LEVELS[self.detail_slider.value()][0]
        # Persist the chosen level so the next dialog open remembers it.
        data = load_settings()
        data[_SETTINGS_KEY_DETAIL] = detail.value
        save_settings(data)

        # Detach the QThread from the dialog so closing the dialog mid-request
        # can't destroy a still-running thread (Qt aborts the process if so).
        self._thread = QThread()
        self._worker = _GenerateWorker(prompt, api_key, detail)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progressed.connect(self._on_worker_progress)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_detail_changed(self, idx: int) -> None:
        idx = max(0, min(idx, len(_DETAIL_LEVELS) - 1))
        _, _name, hint = _DETAIL_LEVELS[idx]
        self.detail_hint.setText(hint)
        for i, lbl in enumerate(self._tick_labels):
            if i == idx:
                lbl.setStyleSheet(
                    "color: #f5a623; font-size: 11px; font-weight: 600;"
                )
            else:
                lbl.setStyleSheet("color: #9aa0a6; font-size: 11px;")

    def _on_worker_progress(self, msg: str) -> None:
        self.status_label.setText(msg)

    def _on_worker_finished(self, svg: str) -> None:
        self.svg_text = svg
        self.accept()

    def _on_worker_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status_label.setText(
            f"<span style='color:#e0524a'>Generation failed: {message}</span>"
        )

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None

    def _set_busy(self, busy: bool) -> None:
        self.prompt_edit.setEnabled(not busy)
        self.detail_slider.setEnabled(not busy)
        self.generate_btn.setEnabled(not busy)
        # Cancel disabled while busy — there's no way to cancel an in-flight
        # Gemini call cleanly, and abandoning the worker thread would risk a
        # SIGABRT at app exit.
        self.cancel_btn.setEnabled(not busy)
        self.progress.setVisible(busy)

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._thread is not None and self._thread.isRunning():
            event.ignore()
            return
        super().closeEvent(event)
