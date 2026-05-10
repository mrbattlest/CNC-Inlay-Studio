"""Units handling — internal data is always mm; this module converts at display
and G-code emission boundaries.

A small persistent ``Settings`` object stores the user's chosen display units
across launches. Components that care about the current units re-read them
through this module rather than passing units through every call site.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

MM_PER_INCH = 25.4


class Units(Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"

    @property
    def length_label(self) -> str:
        return "mm" if self == Units.METRIC else "in"

    @property
    def feed_label(self) -> str:
        return "mm/min" if self == Units.METRIC else "in/min"

    @property
    def gcode_unit_command(self) -> str:
        return "G21" if self == Units.METRIC else "G20"


# ---------- conversion ----------


def length_to_display(value_mm: float, units: Units) -> float:
    return value_mm if units == Units.METRIC else value_mm / MM_PER_INCH


def length_to_internal(display_value: float, units: Units) -> float:
    return display_value if units == Units.METRIC else display_value * MM_PER_INCH


def feed_to_display(value_mm_min: float, units: Units) -> float:
    return value_mm_min if units == Units.METRIC else value_mm_min / MM_PER_INCH


def feed_to_internal(display_value: float, units: Units) -> float:
    return display_value if units == Units.METRIC else display_value * MM_PER_INCH


# ---------- format ----------


def fmt_length(value_mm: float, units: Units, *, decimals: int | None = None) -> str:
    if units == Units.METRIC:
        d = 2 if decimals is None else decimals
        return f"{value_mm:.{d}f} mm"
    d = 4 if decimals is None else decimals
    return f"{value_mm / MM_PER_INCH:.{d}f} in"


def fmt_dimensions(width_mm: float, height_mm: float, units: Units) -> str:
    if units == Units.METRIC:
        return f"{width_mm:.1f} × {height_mm:.1f} mm"
    return f"{width_mm / MM_PER_INCH:.3f} × {height_mm / MM_PER_INCH:.3f} in"


def fmt_feed(value_mm_min: float, units: Units) -> str:
    if units == Units.METRIC:
        return f"{value_mm_min:g} mm/min"
    return f"{value_mm_min / MM_PER_INCH:.1f} in/min"


# ---------- persistent settings ----------

_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "CNC Inlay Studio"
_CONFIG_PATH = _CONFIG_DIR / "settings.json"


def load_settings() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def get_saved_units() -> Units:
    data = load_settings()
    raw = data.get("units", Units.METRIC.value)
    try:
        return Units(raw)
    except ValueError:
        return Units.METRIC


def set_saved_units(units: Units) -> None:
    data = load_settings()
    data["units"] = units.value
    save_settings(data)


# ----- last-used-folder helpers (one slot per dialog category) ---------------

_LAST_FOLDER_KEY = "last_open_folders"


def get_last_folder(category: str) -> str:
    """Return the directory the user last opened a file in for ``category``,
    falling back to ~/Documents on first use. Categories let "Open Design"
    and "Open Project" each remember their own last-used folder so a user
    bouncing between SVG asset folders and an .inlay project folder doesn't
    end up swimming between the two on every open.
    """
    from pathlib import Path
    data = load_settings()
    folders = data.get(_LAST_FOLDER_KEY) or {}
    saved = folders.get(category)
    if saved:
        p = Path(saved)
        if p.exists() and p.is_dir():
            return str(p)
    documents = Path.home() / "Documents"
    return str(documents if documents.exists() else Path.home())


def set_last_folder(category: str, folder: str) -> None:
    """Remember the directory the user just picked a file from / saved one
    to. Accepts either a directory path or a file path (in which case the
    parent directory is remembered) — the file may not yet exist on disk
    (Save dialog case). Silently no-op when the resolved folder doesn't
    exist on disk."""
    from pathlib import Path
    p = Path(folder)
    # If it's clearly a directory, keep as-is. Otherwise treat as a
    # file path and grab the parent — handles both pre-save (file
    # doesn't exist yet) and post-open (file exists) cases.
    if not p.is_dir():
        p = p.parent
    if not p.exists() or not p.is_dir():
        return
    data = load_settings()
    folders = dict(data.get(_LAST_FOLDER_KEY) or {})
    folders[category] = str(p)
    data[_LAST_FOLDER_KEY] = folders
    save_settings(data)
