"""Dialog shown after the user shift-drags a selection rectangle on a
preview canvas. Lets them type an observation and copy a clipboard
snippet (built by ``feedback.build_feedback_snippet``) that captures
all the context a developer / agent needs to diagnose without re-loading
the project.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class FeedbackDialog(QDialog):
    """Shows the rendered snippet + a comment textbox + Copy-to-clipboard."""

    def __init__(
        self,
        snippet_builder,           # callable: comment -> markdown
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._snippet_builder = snippet_builder
        self.setWindowTitle("Copy area feedback")
        self.setMinimumWidth(640)
        self.setMinimumHeight(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        intro = QLabel(
            "<span style='color:#9aa0a6'>This will copy a markdown snippet "
            "to your clipboard with the selected area's coordinates, the "
            "active project + parameters, and a summary of the toolpaths "
            "actually inside the selection. Paste it back to me with your "
            "observation and I can diagnose without you having to "
            "describe the geometry.</span>"
        )
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Comment field — typed comment is folded into the markdown at the
        # bottom, so the recipient sees "context block + your observation".
        comment_label = QLabel("Your observation (will appear at the bottom of the snippet):")
        layout.addWidget(comment_label)
        self._comment = QPlainTextEdit()
        self._comment.setPlaceholderText(
            "e.g. The pocket toolpath leaves the ear partially unmilled — "
            "I think the plug ear won't seat here."
        )
        self._comment.setMinimumHeight(80)
        self._comment.textChanged.connect(self._refresh_preview)
        layout.addWidget(self._comment)

        # Live preview of the exact snippet the Copy button will copy.
        preview_label = QLabel("Snippet preview (what gets copied):")
        layout.addWidget(preview_label)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setStyleSheet(
            "font-family: 'SF Mono','Menlo','Courier New',monospace; font-size: 11px;"
        )
        layout.addWidget(self._preview, 1)

        bb = QDialogButtonBox()
        self._copy_btn = bb.addButton("📋 Copy to clipboard", QDialogButtonBox.AcceptRole)
        self._copy_btn.setDefault(True)
        self._copy_btn.setStyleSheet(
            "QPushButton { background:#f5a623; color:#15171b; "
            "font-weight:700; padding:6px 14px; }"
        )
        self._copy_btn.clicked.connect(self._on_copy)
        cancel_btn = bb.addButton(QDialogButtonBox.Cancel)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(bb)

        self._refresh_preview()
        self._comment.setFocus()

    def _refresh_preview(self) -> None:
        try:
            text = self._snippet_builder(self._comment.toPlainText())
        except Exception as exc:
            text = f"(snippet build failed: {exc})"
        self._preview.setPlainText(text)

    def _on_copy(self) -> None:
        QGuiApplication.clipboard().setText(self._preview.toPlainText())
        self.accept()
