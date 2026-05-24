"""CNC machine catalog + active-machine persistence.

The app's canvases (2D top-down + 3D) historically hard-coded the
LUNYEE 3018 PRO MAX bed envelope (270 × 150 mm) and a 24-screw-hole
clamp pattern. To support other hobbyist machines, the bed dimensions
are now sourced from a profile in :file:`cnc_machines.json` and the
user picks one via Settings → My CNC Device. The selection persists
across sessions in :file:`settings.json`.

A "Custom" machine option lets the user enter arbitrary X/Y in cm
when their CNC isn't in the catalog.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files

from cnc_inlay.core.units import load_settings, save_settings


_DEFAULT_MACHINE_KEY = "lunyee_3018_pro_max"


@dataclass(frozen=True)
class CNCMachine:
    key: str
    name: str
    bed_width_mm: float
    bed_height_mm: float
    screw_hole_diameter_mm: float = 0.0
    # List of (x_label, y_label) coordinates for the clamp-hole pattern.
    # Each label N maps to physical (N-1) × 10 mm offset from the bed's
    # lower-left corner. Empty list = no holes drawn.
    screw_hole_label_positions: list[tuple[int, int]] = field(default_factory=list)
    bed_label_x_max: int = 0
    bed_label_y_max: int = 0


def load_catalog() -> list[CNCMachine]:
    """Load the shipped read-only catalog of supported CNC machines."""
    raw = json.loads(
        files("cnc_inlay.presets.data").joinpath("cnc_machines.json").read_text()
    )
    out: list[CNCMachine] = []
    for d in raw:
        out.append(CNCMachine(
            key=d["key"],
            name=d["name"],
            bed_width_mm=float(d["bed_width_mm"]),
            bed_height_mm=float(d["bed_height_mm"]),
            screw_hole_diameter_mm=float(d.get("screw_hole_diameter_mm", 0.0)),
            screw_hole_label_positions=[
                # Lunyee's documented row spacing isn't a whole number
                # (6.2825 / 11.8475 / 17.2825 / 22.8475 mm), so positions
                # must be stored as floats. int() would round 6.2825 to
                # 6 and stack three holes on top of each other.
                (float(p[0]), float(p[1]))
                for p in d.get("screw_hole_label_positions", [])
            ],
            bed_label_x_max=int(d.get("bed_label_x_max", 0)),
            bed_label_y_max=int(d.get("bed_label_y_max", 0)),
        ))
    return out


def get_active_machine() -> CNCMachine:
    """Return the user's chosen machine. Falls back to the LUNYEE
    preset if no choice has been saved or the saved key isn't in the
    catalog (e.g. catalog updated since)."""
    settings = load_settings()
    chosen_key = settings.get("cnc_machine_key", _DEFAULT_MACHINE_KEY)
    custom = settings.get("cnc_machine_custom")
    if chosen_key == "custom" and isinstance(custom, dict):
        return CNCMachine(
            key="custom",
            name=str(custom.get("name", "Custom CNC")),
            bed_width_mm=float(custom.get("bed_width_mm", 300.0)),
            bed_height_mm=float(custom.get("bed_height_mm", 200.0)),
            screw_hole_diameter_mm=0.0,
            screw_hole_label_positions=[],
            bed_label_x_max=int(float(custom.get("bed_width_mm", 300.0)) // 10),
            bed_label_y_max=int(float(custom.get("bed_height_mm", 200.0)) // 10),
        )
    catalog = load_catalog()
    by_key = {m.key: m for m in catalog}
    return by_key.get(chosen_key) or by_key.get(_DEFAULT_MACHINE_KEY) or catalog[0]


def set_active_machine(key: str, custom: dict | None = None) -> None:
    """Persist the user's CNC choice. ``custom`` is only used when
    ``key == "custom"`` and carries the user-entered W/H/name dict."""
    settings = load_settings()
    settings["cnc_machine_key"] = key
    if key == "custom" and custom is not None:
        settings["cnc_machine_custom"] = custom
    save_settings(settings)


# ---------------------------------------------------- safety-switch calibration
#
# The Lunyee (and most hobbyist CNCs) has hard-stop safety switches at the
# +X / +Y / +Z extremes of travel. When the user sets their work zero in a
# repeatable way (e.g. always at the centre of a stock fixture, or at a
# bed-corner reference), the work-coord position at which each switch
# trips is constant for that setup — a permanent physical reference.
# Stored as work-coord mm (NOT machine-coord), independent of GRBL homing.
# The Recovery Wizard's "Switch touch-off" tab uses these to restore each
# axis independently: jog to the switch → set work-coord to the saved
# value via G10 L20 P1.


def load_safety_switch_positions() -> tuple[float, float, float] | None:
    """Return the saved (x, y, z) work-coord positions of the +X / +Y / +Z
    safety switches, or ``None`` if they haven't been calibrated."""
    settings = load_settings()
    sw = settings.get("safety_switches_xyz_mm")
    if not isinstance(sw, dict):
        return None
    try:
        return (float(sw["x"]), float(sw["y"]), float(sw["z"]))
    except (KeyError, TypeError, ValueError):
        return None


def save_safety_switch_positions(x: float, y: float, z: float) -> None:
    """Persist the work-coord positions of the +X / +Y / +Z safety
    switches. Called from the Recovery Wizard's switch-calibration UI."""
    settings = load_settings()
    settings["safety_switches_xyz_mm"] = {
        "x": float(x), "y": float(y), "z": float(z),
    }
    save_settings(settings)
