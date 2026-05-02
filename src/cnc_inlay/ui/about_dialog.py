"""About CNC Inlay Studio dialog.

Shows the app icon, version, a short description of what the app does, and
credits + links. Reachable via the Help menu and (on macOS) the standard
"About CNC Inlay Studio" item in the application menu.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

import cnc_inlay
from cnc_inlay.updater import GITHUB_OWNER, GITHUB_REPO


_LUNYEE_AMAZON_URL = (
    "https://www.amazon.com/LUNYEE-MAX-All-Metal-Switches-Emergency-Stop/dp/"
    "B0C6WQWX3R/"
)
_GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
_AUTHOR_NAME = "Malcolm Ross"
_AUTHOR_EMAIL = "ross.malcolm@gmail.com"


def _find_icon_path() -> Path | None:
    """Locate a 256-px app icon for the About header — works in both dev mode
    (running from source) and the bundled .app (PyInstaller copies assets/
    next to the package)."""
    candidates = [
        # dev mode: cnc_inlay/ui/about_dialog.py → ../../../assets
        Path(__file__).resolve().parent.parent.parent.parent / "assets" / "icon.iconset" / "icon_256x256.png",
        # bundle: assets/ folder copied next to package
        Path(__file__).resolve().parent.parent / "assets" / "icon.iconset" / "icon_256x256.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About CNC Inlay Studio")
        self.setModal(True)
        self.setMinimumWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 16)
        outer.setSpacing(14)

        # Header row: app icon + name/version stacked next to it.
        header = QHBoxLayout()
        header.setSpacing(16)

        icon_label = QLabel()
        icon_path = _find_icon_path()
        if icon_path is not None:
            pix = QPixmap(str(icon_path)).scaled(
                96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            icon_label.setPixmap(pix)
        icon_label.setFixedSize(96, 96)
        header.addWidget(icon_label, 0, Qt.AlignTop)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        name = QLabel(
            "<h2 style='margin:0;'>CNC Inlay Studio</h2>"
        )
        name.setTextFormat(Qt.RichText)
        version = QLabel(
            f"<span style='color:#9aa0a6;'>Version {cnc_inlay.__version__}</span>"
        )
        version.setTextFormat(Qt.RichText)
        tagline = QLabel(
            "<span style='color:#c9ced6;'>"
            "Generate paired pocket + plug G-code for wood inlays from "
            "any SVG or AI-generated design."
            "</span>"
        )
        tagline.setTextFormat(Qt.RichText)
        tagline.setWordWrap(True)
        title_block.addWidget(name)
        title_block.addWidget(version)
        title_block.addSpacing(6)
        title_block.addWidget(tagline)
        title_block.addStretch(1)
        header.addLayout(title_block, 1)
        outer.addLayout(header)

        # Body: what the app does, who it's for, the target machine, credits.
        body = QLabel(
            "<p>"
            "CNC Inlay Studio takes an SVG silhouette and produces two G-code "
            "files — one that carves the matching <b>pocket</b> into a base "
            "wood, and one that carves the contrasting <b>plug</b> from a "
            "second wood. The two pieces are glued together and planed flush "
            "to reveal a flush wood inlay. A built-in GRBL streamer drives "
            "the machine directly over USB so you never have to switch to a "
            "third-party sender."
            "</p>"
            f"<h3 style='margin-top:14px; margin-bottom:4px;'>"
            f"Designed for the LUNYEE 3018 PRO MAX</h3>"
            "<p>"
            "Toolpaths, the bed envelope (270 × 150 mm), the screw-hole map, "
            "and every default in the Speeds and Inlay panels are tuned for "
            f"the <a href='{_LUNYEE_AMAZON_URL}'>LUNYEE 3018 PRO MAX</a>. "
            "Other GRBL 1.1 machines may work but no guarantees on bed "
            "dimensions, soft-limits, or recommended feeds and speeds."
            "</p>"
            "<h3 style='margin-top:14px; margin-bottom:4px;'>Created by</h3>"
            f"<p>{_AUTHOR_NAME} &nbsp; "
            f"&middot; &nbsp; "
            f"<a href='mailto:{_AUTHOR_EMAIL}'>{_AUTHOR_EMAIL}</a> &nbsp; "
            f"&middot; &nbsp; "
            f"<a href='{_GITHUB_REPO_URL}'>Source on GitHub</a></p>"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(
            Qt.TextBrowserInteraction | Qt.LinksAccessibleByMouse
        )
        outer.addWidget(body)

        outer.addStretch(1)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        outer.addWidget(button_box)


def show_about_dialog(parent: QWidget | None = None) -> None:
    AboutDialog(parent).exec()
