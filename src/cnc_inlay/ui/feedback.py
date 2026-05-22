"""Build a clipboard-friendly markdown snippet describing a selected area
on a preview canvas.

The snippet is what the user pastes back to the developer (or another LLM
agent) with their observation. It carries enough context — project path,
app version, active tab, selection coordinates, plus a localised summary
of the toolpaths actually inside the selection — that the recipient can
diagnose without needing to re-load the project and triangulate.

Pure functions only: no Qt, no I/O beyond a hash of the project file.
The dialog at ``feedback_dialog.py`` wires this to the UI.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Polygon, Point, box


@dataclass(frozen=True)
class FeedbackContext:
    """All the static state the snippet needs (no per-selection data)."""

    project_path: Path | None
    app_version: str
    tab_name: str          # "Pocket", "Plug", "Surfacing", "Finished"
    view_mode: str         # "2D" or "3D"
    design_w_mm: float
    design_h_mm: float
    design_rotation_quarters: int
    pocket_depth_mm: float
    glue_gap_mm: float
    xy_tolerance_mm: float
    pass_depth_mm: float
    skip_pocket_vbit: bool
    pocket_material: str
    plug_material: str
    endmill_name: str
    vbit_name: str | None
    pocket_roughing_bits: list[str]
    plug_roughing_bits: list[str]


def _hash_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except Exception:
        return None


def _bits_in_rect(
    bit_chains: list,
    rect: tuple[float, float, float, float],
) -> list[tuple[str, int, int, float, float]]:
    """For each bit, count how many chains have at least one point inside
    the rect and the total number of chain points inside. Returns a list of
    (bit_name, chains_in_rect, points_in_rect, min_z, max_z).
    """
    minx, miny, maxx, maxy = rect
    out: list[tuple[str, int, int, float, float]] = []
    for entry in bit_chains or []:
        # bit_chains may be either (bit, chains, role, duration) tuples
        # (live PipelineResult) or {"bit_name", "chains", ...} dicts
        # (deserialised from project cache). Support both.
        if isinstance(entry, tuple):
            bit, chains, _role, _dur = entry
            bit_name = bit.name
        else:
            bit_name = entry.get("bit_name", "?")
            chains = entry.get("chains", [])
        n_chains_in = 0
        n_points_in = 0
        z_min = None
        z_max = None
        for c in chains or []:
            chain_had_point = False
            for pt in c:
                x, y = float(pt[0]), float(pt[1])
                if minx <= x <= maxx and miny <= y <= maxy:
                    n_points_in += 1
                    chain_had_point = True
                    z = float(pt[2]) if len(pt) > 2 else 0.0
                    z_min = z if z_min is None else min(z_min, z)
                    z_max = z if z_max is None else max(z_max, z)
            if chain_had_point:
                n_chains_in += 1
        if n_chains_in > 0:
            out.append((bit_name, n_chains_in, n_points_in,
                        z_min if z_min is not None else 0.0,
                        z_max if z_max is not None else 0.0))
    return out


def _local_feature_width_mm(
    design_geom: Polygon | None,
    rect: tuple[float, float, float, float],
    samples: int = 64,
) -> tuple[float, float] | None:
    """Sample points inside ``rect`` and report the design's local
    half-width (= distance from point to design boundary). Returns
    (min_half_width, max_half_width) over the rect's interior or None
    if the design isn't available / the rect lies outside it.

    Smaller half-widths mean the smallest bit that can fit there has
    half that diameter, so the user can immediately tell whether their
    bit lineup can reach the selected region.
    """
    if design_geom is None or design_geom.is_empty:
        return None
    minx, miny, maxx, maxy = rect
    n = max(4, int(samples ** 0.5))
    dx = (maxx - minx) / max(1, n - 1)
    dy = (maxy - miny) / max(1, n - 1)
    boundary = design_geom.boundary
    widths: list[float] = []
    for i in range(n):
        for j in range(n):
            P = Point(minx + i * dx, miny + j * dy)
            if design_geom.contains(P):
                widths.append(P.distance(boundary))
    if not widths:
        return None
    return (min(widths), max(widths))


def build_feedback_snippet(
    ctx: FeedbackContext,
    rect_mm: tuple[float, float, float, float],
    *,
    side_bit_chains: list | None = None,
    design_geom: Polygon | None = None,
    comment: str = "",
    extra_sides: list | None = None,
) -> str:
    """Build the markdown snippet for one selection rectangle.

    ``rect_mm`` is (minx, miny, maxx, maxy) in design work-coordinate mm
    (Y up). ``side_bit_chains`` is the active side's ``bit_chains`` list
    from the PipelineResult (so we can summarise what bits touch the
    rect). ``design_geom`` is the centred design polygon (so we can
    report the local feature half-width).

    ``extra_sides`` — used for the Finished tab, where one selection
    spans both sides — is a list of ``(label, bit_chains, rect_tuple)``.
    Each entry gets its own "Toolpaths touching this area" subsection.
    The per-side rect differs because the plug toolpaths live in the
    X-mirrored frame, so the caller passes a mirrored rect for the plug.
    When ``extra_sides`` is given it replaces the single-side summary.
    """
    minx, miny, maxx, maxy = rect_mm
    w = maxx - minx
    h = maxy - miny
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0

    proj_hash = (
        _hash_file(ctx.project_path) if ctx.project_path is not None else None
    )
    proj_label = (
        f"{ctx.project_path}" if ctx.project_path is not None else "(unsaved project)"
    )
    if proj_hash:
        proj_label += f"  (sha256: {proj_hash}…)"

    lines: list[str] = []
    lines.append("```")
    lines.append("## CNC Inlay Studio — area feedback")
    lines.append(f"- Project:     {proj_label}")
    lines.append(f"- App version: {ctx.app_version}")
    rot_note = (
        f" (rotated {ctx.design_rotation_quarters}×CW)"
        if ctx.design_rotation_quarters else ""
    )
    lines.append(
        f"- Design:      {ctx.design_w_mm:.2f} × {ctx.design_h_mm:.2f} mm{rot_note}"
    )
    lines.append(f"- Tab / view:  {ctx.tab_name}  ({ctx.view_mode})")
    lines.append("")
    lines.append("### Selected area (work-coord mm, Y up)")
    lines.append(
        f"- rect:   x=[{minx:.2f}, {maxx:.2f}]   "
        f"y=[{miny:.2f}, {maxy:.2f}]   "
        f"(w={w:.2f} × h={h:.2f}, centre ({cx:.2f}, {cy:.2f}))"
    )

    # Local feature width inside the selection — tells the reader at a
    # glance whether the bit lineup can reach this region at all.
    fw = _local_feature_width_mm(design_geom, rect_mm) if design_geom is not None else None
    if fw is not None:
        min_hw, max_hw = fw
        lines.append(
            f"- Design half-width inside selection: min {min_hw:.2f} mm, "
            f"max {max_hw:.2f} mm  (smallest bit-radius that fits: ≥ {min_hw:.2f} mm)"
        )

    # Toolpath summary localised to the selection. The Finished tab
    # passes ``extra_sides`` (both pocket and plug); the Pocket/Plug
    # tabs pass a single ``side_bit_chains``.
    def _emit_side_summary(label: str, bit_chains, side_rect) -> None:
        per_bit = _bits_in_rect(bit_chains, side_rect)
        lines.append("")
        if per_bit:
            lines.append(f"### Toolpaths touching this area on the {label} side")
            for bit_name, n_ch, n_pts, z_min, z_max in per_bit:
                lines.append(
                    f"- {bit_name}: {n_ch} chains, {n_pts} pts, "
                    f"Z=[{z_min:.2f}, {z_max:.2f}]"
                )
        else:
            lines.append(
                f"### No toolpath chains touch this area on the {label} side."
            )
            lines.append(
                "  (= this region is uncut — either the bits can't reach it "
                "or the design has no feature there)"
            )

    if extra_sides:
        for label, bit_chains, side_rect in extra_sides:
            _emit_side_summary(label, bit_chains, side_rect)
    elif side_bit_chains is not None:
        _emit_side_summary(ctx.tab_name, side_bit_chains, rect_mm)

    lines.append("")
    lines.append("### Job parameters")
    lines.append(
        f"- Pocket depth {ctx.pocket_depth_mm} mm, glue gap {ctx.glue_gap_mm} mm, "
        f"pass depth {ctx.pass_depth_mm} mm, xy_tolerance {ctx.xy_tolerance_mm} mm"
    )
    lines.append(
        f"- skip_pocket_vbit: {ctx.skip_pocket_vbit}    "
        f"materials: pocket {ctx.pocket_material} / plug {ctx.plug_material}"
    )
    lines.append(f"- Endmill (finisher): {ctx.endmill_name}")
    lines.append(f"- V-bit: {ctx.vbit_name or '(none)'}")
    if ctx.pocket_roughing_bits:
        lines.append(
            "- Pocket roughing: " + ", ".join(ctx.pocket_roughing_bits)
        )
    if ctx.plug_roughing_bits:
        lines.append(
            "- Plug roughing:   " + ", ".join(ctx.plug_roughing_bits)
        )

    if comment.strip():
        lines.append("")
        lines.append("### Observation")
        lines.append(comment.strip())

    lines.append("```")
    return "\n".join(lines)
