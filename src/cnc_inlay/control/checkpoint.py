"""Streaming-progress checkpoint — survives crashes, sleep, or manual aborts.

While a real cut is in progress, we periodically write a JSON file recording:
- The G-code being streamed (full text)
- How many lines have been ack'd so far
- Which side (pocket/plug) is running
- Project context (so we can offer to reload everything on next launch)

On clean completion, the checkpoint is deleted. If it survives to the next
app launch, we know the previous cut didn't finish — and we can offer the
user the option to resume from where we left off.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import time

_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "CNC Inlay Studio"
CHECKPOINT_PATH = _CONFIG_DIR / "in_progress_cut.json"


@dataclass
class Checkpoint:
    """Snapshot of an in-progress cut. All fields JSON-serialisable."""
    side: str = ""                  # "pocket" or "plug"
    gcode: str = ""                 # full G-code program text
    cursor: int = 0                 # 0-indexed line number of last GRBL ack
    total_lines: int = 0
    started_at: float = 0.0         # epoch seconds
    last_updated_at: float = 0.0
    # Project context — enough to fully restore the workspace.
    project_path: str | None = None
    design_path: str | None = None
    design_filename: str | None = None
    pocket_material: str = ""
    plug_material: str = ""
    endmill_name: str = ""
    pocket_thickness_mm: float = 0.0
    plug_thickness_mm: float = 0.0
    pocket_depth_mm: float = 0.0
    # Last known work-coordinate position (from a recent GRBL status
    # report). Persisted every checkpoint tick so that if GRBL goes
    # silent mid-cut and the user has to power-cycle the controller,
    # we can offer to restore the work-zero offsets to keep the bit's
    # current physical position aligned with the cut. None on legacy
    # checkpoints / pre-stream state.
    last_wpos_x_mm: float | None = None
    last_wpos_y_mm: float | None = None
    last_wpos_z_mm: float | None = None
    # True when the cut ended because GRBL stopped responding (silence
    # detector fired), not a clean completion or a user abort. This
    # flag drives the "GRBL went silent — restore last known position?"
    # offer on next reconnect.
    grbl_silenced: bool = False


def save_checkpoint(cp: Checkpoint) -> None:
    """Atomically write the checkpoint to disk. Idempotent + safe to call often."""
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cp.last_updated_at = time()
        if cp.started_at == 0.0:
            cp.started_at = cp.last_updated_at
        tmp = CHECKPOINT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(cp), indent=2))
        tmp.replace(CHECKPOINT_PATH)
    except Exception:
        # Never let a checkpoint failure crash a running stream.
        pass


def load_checkpoint() -> Checkpoint | None:
    if not CHECKPOINT_PATH.exists():
        return None
    try:
        data = json.loads(CHECKPOINT_PATH.read_text())
        return Checkpoint(**data)
    except Exception:
        return None


def clear_checkpoint() -> None:
    try:
        if CHECKPOINT_PATH.exists():
            CHECKPOINT_PATH.unlink()
    except Exception:
        pass


def find_resume_line(gcode: str, target_line: int) -> int:
    """Find the safest line ≤ ``target_line`` to resume from.

    Resume must start at a chain boundary so the bit's at safe Z and can rapid
    to its first XY before the next plunge. Scans backwards for the most
    recent ``G0 X<...> Y<...>`` (rapid-to-start of a chain), which always
    follows a ``G0 Z<safe>`` lift. Falls back to 0 if nothing found.
    """
    lines = gcode.splitlines()
    target = min(target_line, len(lines) - 1)
    target = max(target, 0)
    for i in range(target, -1, -1):
        line = lines[i].strip()
        # Match a chain-start rapid: G0 with X and Y but no Z (the lift would
        # be a separate `G0 Z<safe>` line).
        if line.startswith("G0 X") and " Y" in line and " Z" not in line.split("(", 1)[0]:
            return i
    return 0
