"""Project file format — saves the user's current configuration to a JSON file
so they can reopen it later and re-cut the same inlay.

Stored:
- Path to the SVG design (absolute, with a relative fallback)
- All InlayParams values (depths, gaps, offsets, units, RPM/feed/plunge overrides)
- Selected pocket and plug materials by name
- Selected endmill (and V-bit if present) by name
- User-edited flags for the speed overrides

NOT stored: the bit / material library (that's preset data, lives elsewhere),
the generated G-code (regenerated from params), window state.

Files use the ``.inlay`` extension by convention.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cnc_inlay.core.job import BitSpeedOverride, InlayParams, Origin
from cnc_inlay.core.units import Units


PROJECT_VERSION = 8  # v8 added design_rotation_quarters (90° rotation persistence)


@dataclass
class ProjectFile:
    version: int = PROJECT_VERSION
    design_path: str | None = None         # original path, for display only
    design_filename: str | None = None     # original basename (for display when re-loaded from embedded copy)
    svg_content: str | None = None         # full embedded SVG text — makes the file portable
    # User's chosen physical size for the inlay. None means "use whatever the
    # SVG natively parses to" (v1 behaviour). Without this, the user's resize
    # was silently lost every time the project reloaded.
    design_width_mm: float | None = None
    design_height_mm: float | None = None
    pocket_material: str = ""
    plug_material: str = ""
    endmill_name: str = ""
    vbit_name: str | None = None
    params: InlayParams = field(default_factory=InlayParams)
    rpm_user_edited: bool = False
    feed_user_edited: bool = False
    plunge_user_edited: bool = False
    # Cached pipeline result so reopening doesn't re-run the (slow) toolpath
    # computation. Format is opaque to the project file (a JSON-friendly
    # nested dict produced by main_window._serialise_pipeline_result and
    # restored by _deserialise_pipeline_result). ``cache_signature`` is a
    # hash of every input that influences the result; a cache is only
    # restored when the signature still matches the loaded params, so any
    # edit invalidates it cleanly without keeping stale chains.
    cached_result: dict | None = None
    cache_signature: str | None = None
    # Last 3D view orientation the user looked at when the project was
    # saved. Restored on open so the user lands at the same camera angle
    # / zoom they left at. None on legacy files → fall back to the canvas
    # default (X right, Y away, tilted 30° back from top-down).
    view_orientation: dict | None = None
    # Saved surfacing-pass settings — populated by the user clicking
    # Save in Settings → Surface plug backing… so the same bit /
    # depth / feed / RPM are pre-filled the next time they open the
    # dialog. Opaque dict (keys: bit_name, panel_w, panel_h,
    # depth_to_remove, combined_z, rpm, feed, pass_depth). None on
    # legacy files / projects that have never had surfacing saved.
    surfacing_settings: dict | None = None
    # Number of 90°-clockwise rotations applied to the design via the
    # Rotate 90° button. Persisted (v8+) so the rotated design isn't
    # reverted on reload — older files (v7-) silently lost the rotation
    # because design_width_mm/_height_mm were saved post-rotation but
    # the SVG itself was reloaded in its natural orientation, then
    # rescale() squished it to the wrong aspect.
    design_rotation_quarters: int = 0


def _params_to_dict(p: InlayParams) -> dict:
    return {
        "pocket_depth_mm": p.pocket_depth_mm,
        "glue_gap_mm": p.glue_gap_mm,
        "pass_depth_mm": p.pass_depth_mm,
        "safe_z_mm": p.safe_z_mm,
        "resolution_mm": p.resolution_mm,
        "origin": p.origin.value,
        "boundary_offset_mm": p.boundary_offset_mm,
        "pocket_step_over_frac": p.pocket_step_over_frac,
        "tool_change_z_lift_mm": p.tool_change_z_lift_mm,
        "xy_tolerance_mm": p.xy_tolerance_mm,
        "spindle_rpm_override": p.spindle_rpm_override,
        "feed_override_mm_min": p.feed_override_mm_min,
        "plunge_override_mm_min": p.plunge_override_mm_min,
        "gcode_units": p.gcode_units.value,
        "pocket_thickness_mm": p.pocket_thickness_mm,
        "plug_thickness_mm": p.plug_thickness_mm,
        "pocket_stock_width_mm": p.pocket_stock_width_mm,
        "pocket_stock_height_mm": p.pocket_stock_height_mm,
        "plug_stock_width_mm": p.plug_stock_width_mm,
        "plug_stock_height_mm": p.plug_stock_height_mm,
        "pocket_roughing_bits": list(p.pocket_roughing_bits),
        "plug_roughing_bits": list(p.plug_roughing_bits),
        "skip_pocket_vbit": bool(getattr(p, "skip_pocket_vbit", False)),
        "pocket_allowance_mm": float(getattr(p, "pocket_allowance_mm", 0.1)),
        "surface_clearance_mm": float(getattr(p, "surface_clearance_mm", 2.5)),
        "bit_speed_overrides": {
            name: {
                "rpm": ov.rpm,
                "feed_mm_min": ov.feed_mm_min,
                "plunge_mm_min": ov.plunge_mm_min,
            }
            for name, ov in p.bit_speed_overrides.items()
        },
    }


def _params_from_dict(d: dict) -> InlayParams:
    return InlayParams(
        pocket_depth_mm=float(d.get("pocket_depth_mm", 3.0)),
        glue_gap_mm=float(d.get("glue_gap_mm", 0.2)),
        pass_depth_mm=float(d.get("pass_depth_mm", 1.0)),
        safe_z_mm=float(d.get("safe_z_mm", 5.0)),
        resolution_mm=float(d.get("resolution_mm", 0.1)),
        origin=Origin(d.get("origin", "center")),
        boundary_offset_mm=float(d.get("boundary_offset_mm", 5.0)),
        pocket_step_over_frac=float(d.get("pocket_step_over_frac", 0.5)),
        tool_change_z_lift_mm=float(d.get("tool_change_z_lift_mm", 60.0)),
        # v5+ files have xy_tolerance_mm; older files default to 0.0 so the
        # cut behaves exactly as it always did when reopened (no surprise
        # XY shrink applied to a project the user never set this on).
        xy_tolerance_mm=float(d.get("xy_tolerance_mm", 0.0)),
        spindle_rpm_override=(
            int(d["spindle_rpm_override"])
            if d.get("spindle_rpm_override") is not None else None
        ),
        feed_override_mm_min=(
            float(d["feed_override_mm_min"])
            if d.get("feed_override_mm_min") is not None else None
        ),
        plunge_override_mm_min=(
            float(d["plunge_override_mm_min"])
            if d.get("plunge_override_mm_min") is not None else None
        ),
        gcode_units=Units(d.get("gcode_units", "metric")),
        # New per-side fields with backward compat for older projects that
        # stored a single ``stock_thickness_mm``.
        pocket_thickness_mm=float(
            d.get("pocket_thickness_mm", d.get("stock_thickness_mm", 0.0))
        ),
        plug_thickness_mm=float(
            d.get("plug_thickness_mm", d.get("stock_thickness_mm", 0.0))
        ),
        stock_thickness_mm=float(d.get("stock_thickness_mm", 0.0)),
        pocket_stock_width_mm=float(d.get("pocket_stock_width_mm", 0.0)),
        pocket_stock_height_mm=float(d.get("pocket_stock_height_mm", 0.0)),
        plug_stock_width_mm=float(d.get("plug_stock_width_mm", 0.0)),
        plug_stock_height_mm=float(d.get("plug_stock_height_mm", 0.0)),
        pocket_roughing_bits=list(d.get("pocket_roughing_bits", [])),
        plug_roughing_bits=list(d.get("plug_roughing_bits", [])),
        skip_pocket_vbit=bool(d.get("skip_pocket_vbit", True)),
        pocket_allowance_mm=float(d.get("pocket_allowance_mm", 0.1)),
        surface_clearance_mm=float(d.get("surface_clearance_mm", 2.5)),
        bit_speed_overrides={
            name: BitSpeedOverride(
                rpm=int(entry["rpm"]) if entry.get("rpm") is not None else None,
                feed_mm_min=(
                    float(entry["feed_mm_min"])
                    if entry.get("feed_mm_min") is not None else None
                ),
                plunge_mm_min=(
                    float(entry["plunge_mm_min"])
                    if entry.get("plunge_mm_min") is not None else None
                ),
            )
            for name, entry in (d.get("bit_speed_overrides") or {}).items()
        },
    )


def save_project(path: str | Path, project: ProjectFile) -> None:
    data = {
        "version": project.version,
        "design_path": project.design_path,
        "design_filename": project.design_filename,
        "svg_content": project.svg_content,
        "design_width_mm": project.design_width_mm,
        "design_height_mm": project.design_height_mm,
        "pocket_material": project.pocket_material,
        "plug_material": project.plug_material,
        "endmill_name": project.endmill_name,
        "vbit_name": project.vbit_name,
        "params": _params_to_dict(project.params),
        "rpm_user_edited": project.rpm_user_edited,
        "feed_user_edited": project.feed_user_edited,
        "plunge_user_edited": project.plunge_user_edited,
        "cached_result": project.cached_result,
        "cache_signature": project.cache_signature,
        "view_orientation": project.view_orientation,
        "surfacing_settings": project.surfacing_settings,
        "design_rotation_quarters": int(project.design_rotation_quarters) % 4,
    }
    Path(path).write_text(json.dumps(data, indent=2))


def load_project(path: str | Path) -> ProjectFile:
    data = json.loads(Path(path).read_text())
    # design_width_mm / height_mm absent on v1 files — leave as None so the
    # caller falls through to the design's natural SVG-parsed size.
    project = ProjectFile(
        version=int(data.get("version", PROJECT_VERSION)),
        design_path=data.get("design_path"),
        design_filename=data.get("design_filename"),
        svg_content=data.get("svg_content"),
        design_width_mm=(
            float(data["design_width_mm"])
            if data.get("design_width_mm") is not None else None
        ),
        design_height_mm=(
            float(data["design_height_mm"])
            if data.get("design_height_mm") is not None else None
        ),
        pocket_material=data.get("pocket_material", ""),
        plug_material=data.get("plug_material", ""),
        endmill_name=data.get("endmill_name", ""),
        vbit_name=data.get("vbit_name"),
        params=_params_from_dict(data.get("params", {})),
        rpm_user_edited=bool(data.get("rpm_user_edited", False)),
        feed_user_edited=bool(data.get("feed_user_edited", False)),
        plunge_user_edited=bool(data.get("plunge_user_edited", False)),
        cached_result=data.get("cached_result"),
        cache_signature=data.get("cache_signature"),
        view_orientation=data.get("view_orientation"),
        surfacing_settings=data.get("surfacing_settings"),
        design_rotation_quarters=int(data.get("design_rotation_quarters", 0) or 0) % 4,
    )
    # v5 → v6: any global speed override gets attached to the saved finishing
    # bit so the user's prior tuning carries forward into the new per-bit
    # model. Only migrate if no per-bit overrides were saved (i.e. file is v5).
    p = project.params
    has_global = (
        p.spindle_rpm_override is not None
        or p.feed_override_mm_min is not None
        or p.plunge_override_mm_min is not None
    )
    if (
        has_global
        and project.endmill_name
        and project.endmill_name not in p.bit_speed_overrides
    ):
        p.bit_speed_overrides[project.endmill_name] = BitSpeedOverride(
            rpm=p.spindle_rpm_override,
            feed_mm_min=p.feed_override_mm_min,
            plunge_mm_min=p.plunge_override_mm_min,
        )
    return project


def read_svg_content(path: str | Path) -> str | None:
    """Read an SVG file's content as text for embedding in a project file."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return None
