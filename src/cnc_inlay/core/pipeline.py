"""End-to-end inlay generation for the straight-wall pocket-inlay technique.

For each side, we run a single endmill clearing pass:

  - **Pocket** (carved into the base wood) clears the *inside* of the design
    polygon to depth ``pocket_depth``. The result is a set of cavities matching
    the design shape (frame + each letter / region).

  - **Plug** (carved into the contrasting wood) clears the *complement* — a
    bounding rectangle around the design, minus the design itself — to depth
    ``pocket_depth + glue_gap``. The result is the design left raised above a
    cleared backer area. The plug is glued on top of the pocket and the backer
    wood planed off to reveal a flush inlay.

No mirroring, no flipping. Same orientation throughout. The boundary offset
controls the size of the bounding rectangle around the design.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from shapely.affinity import translate as shp_translate
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.validation import make_valid

from cnc_inlay.core.job import Bit, Design, InlayParams, Job, Material, Origin
from cnc_inlay.core.recommend import Recommendation, recommend_bits
from cnc_inlay.gcode.emitter import Section, emit_gcode
from cnc_inlay.geometry.medial_axis import Skeleton
from cnc_inlay.geometry.shape import RasterTransform
from cnc_inlay.toolpath.optimize import (
    merge_close_loops,
    order_chains,
    total_air_length_mm,
    total_cut_length_mm,
)
from cnc_inlay.toolpath.passes import split_into_passes
from cnc_inlay.geometry.offset import offset_polygon, pocket_offset_rings
from cnc_inlay.toolpath.pocket import compute_endmill_pocket_clear
from cnc_inlay.core.inlay_minimums import FragilityReport, check_plug_fragility
from cnc_inlay.sim.simulator import build_segments, total_duration_seconds
from cnc_inlay.toolpath.vcarve import Chain


def _max_inset_mm(geom: Polygon | MultiPolygon, probe_steps_mm: float = 0.25) -> float:
    """Largest inward offset that still leaves a non-empty polygon.

    Approximates the thinnest dimension of the polygon; useful for deciding
    whether an endmill can clear a region.
    """
    last_good = 0.0
    delta = probe_steps_mm
    # Coarse sweep first to bound, then refine.
    for delta in [0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0]:
        if offset_polygon(geom, -delta) is not None:
            last_good = delta
        else:
            break
    # Refine in a tighter loop near the boundary.
    lo, hi = last_good, last_good + (delta - last_good if last_good < delta else 1.0)
    for _ in range(8):
        mid = (lo + hi) / 2
        if offset_polygon(geom, -mid) is not None:
            lo = mid
        else:
            hi = mid
    return lo


def _coverage_report(
    geom: Polygon | MultiPolygon,
    endmill_diameter_mm: float,
    step_over_frac: float = 0.5,
) -> CoverageReport:
    polys: list[Polygon] = (
        list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    )
    radius = endmill_diameter_mm / 2.0
    full = 0
    partial = 0
    uncleared = 0
    thinnest = float("inf")
    for poly in polys:
        if poly.is_empty:
            continue
        thin = _max_inset_mm(poly)
        thinnest = min(thinnest, thin)
        rings = pocket_offset_rings(poly, endmill_diameter_mm, step_over_frac)
        n = len(rings)
        if n == 0 or thin < radius:
            uncleared += 1
        elif n <= 2:
            partial += 1
        else:
            full += 1
    if thinnest == float("inf"):
        thinnest = 0.0
    # Recommend a bit small enough to fit ≥3 rings in the thinnest region:
    # 3 rings means radius + 2 * step_over < thinnest. Solve for diameter.
    # diameter*(1/2 + 2*frac) < thinnest  → diameter < thinnest / (0.5 + 2*frac)
    denom = 0.5 + 2 * step_over_frac
    recommended = max(0.5, thinnest / max(denom, 0.1))
    return CoverageReport(
        total_regions=len(polys),
        fully_cleared=full,
        partially_cleared=partial,
        uncleared=uncleared,
        thinnest_region_max_inset_mm=thinnest,
        recommended_max_diameter_mm=recommended,
    )


def _shift_chains(chains: list[Chain], dx: float, dy: float) -> list[Chain]:
    return [[(x + dx, y + dy, z) for x, y, z in c] for c in chains]


def _apply_speed_overrides(
    material: Material, params: InlayParams, bit: Bit | None = None
) -> Material:
    """Return a Material with effective speeds for ``bit`` applied.

    Resolution order: per-bit override → global override → material default.
    Falls back gracefully when ``bit`` is None or unknown.
    """
    bit_override = (
        params.bit_speed_overrides.get(bit.name) if bit is not None else None
    )

    def pick(bit_val, global_val, default_val):
        if bit_val is not None:
            return bit_val
        if global_val is not None:
            return global_val
        return default_val

    return Material(
        name=material.name,
        feed_mm_min=pick(
            bit_override.feed_mm_min if bit_override else None,
            params.feed_override_mm_min,
            material.feed_mm_min,
        ),
        plunge_mm_min=pick(
            bit_override.plunge_mm_min if bit_override else None,
            params.plunge_override_mm_min,
            material.plunge_mm_min,
        ),
        rpm=pick(
            bit_override.rpm if bit_override else None,
            params.spindle_rpm_override,
            material.rpm,
        ),
        max_doc_mm=material.max_doc_mm,
    )


def _center_polygon(geom: Polygon | MultiPolygon) -> Polygon | MultiPolygon:
    minx, miny, maxx, maxy = geom.bounds
    cx = (minx + maxx) / 2
    cy = (miny + maxy) / 2
    return shp_translate(geom, xoff=-cx, yoff=-cy)


def _mirror_x(geom: Polygon | MultiPolygon) -> Polygon | MultiPolygon:
    """Mirror centred geometry across the Y axis (negate X)."""
    from shapely.affinity import scale as shp_scale
    return shp_scale(geom, xfact=-1, yfact=1, origin=(0, 0))


def _design_complement(
    geom: Polygon | MultiPolygon,
    padding_mm: float,
    *,
    stock_width_mm: float = 0.0,
    stock_height_mm: float = 0.0,
) -> Polygon | MultiPolygon | None:
    """The waste region to clear around the plug feature, minus the feature.

    When the user's actual plug-stock dimensions are known
    (``stock_width_mm`` and ``stock_height_mm`` both > 0) the cleared
    region is the **whole stock** (centered on the origin, same as the
    centered design) minus the feature. The freeing cut runs all the way
    to the wood edge — no fixed margin around the design — so the plug
    plate comes off the machine fully routed with nothing but the plug
    feature standing, and there's no leftover border of contrasting wood
    to saw or plane away. The SVG/design bounding box does not bound the
    cut in this case.

    When stock dimensions aren't set, fall back to the design's bounding
    box expanded by ``padding_mm`` on every side.
    """
    if stock_width_mm > 0 and stock_height_mm > 0:
        # Stock is centered on origin (the design is already centered).
        sx, sy = stock_width_mm / 2.0, stock_height_mm / 2.0
        bounding = box(-sx, -sy, sx, sy)
    else:
        minx, miny, maxx, maxy = geom.bounds
        bounding = box(
            minx - padding_mm, miny - padding_mm,
            maxx + padding_mm, maxy + padding_mm,
        )
    complement = bounding.difference(geom)
    if not complement.is_valid:
        complement = make_valid(complement)
    if complement.is_empty:
        return None
    if isinstance(complement, (Polygon, MultiPolygon)):
        return complement
    return None


@dataclass
class CoverageReport:
    """How well the chosen endmill clears each design region."""
    total_regions: int = 0
    fully_cleared: int = 0
    partially_cleared: int = 0   # 1 or 2 rings — incomplete
    uncleared: int = 0           # too thin for the bit
    thinnest_region_max_inset_mm: float = 0.0
    recommended_max_diameter_mm: float = 0.0  # so the thinnest region clears with ≥3 rings

    @property
    def has_warning(self) -> bool:
        return self.uncleared > 0 or self.partially_cleared > 0


@dataclass
class Side:
    """Computed result for one side of the inlay (pocket or plug)."""
    clear_preview: list[Chain] = field(default_factory=list)  # endmill clearing chains
    chains_full: list[Chain] = field(default_factory=list)
    gcode: str = ""
    cut_length_mm: float = 0.0
    air_length_mm: float = 0.0
    skeleton: Skeleton | None = None
    transform: RasterTransform | None = None
    # Multi-bit breakdown: list of (bit, chains, role, duration_s) tuples in
    # cut order. ``role`` is "Roughing" for all but the last entry,
    # "Finishing" for the last. ``duration_s`` is the estimated wall-clock
    # cut time for that bit, computed from chain length / feed rate. Empty
    # list for empty Sides. Used by the UI to show colour-coded legends with
    # per-bit times and a stacked-bar timeline.
    bit_chains: list[tuple[Bit, list[Chain], str, float]] = field(default_factory=list)


@dataclass
class StockFitReport:
    """Per-side check: does the design (and any boundary offset) fit on the
    user's wood? Empty list of issues = everything fits."""
    pocket_issues: list[str] = field(default_factory=list)
    plug_issues: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.pocket_issues or self.plug_issues)


@dataclass
class SurfacingPlan:
    """Generated surfacing toolpath + G-code for removing the plug backing
    after glue-up. Computed on demand by ``compute_surfacing_plan`` once
    the user has chosen a surfacing bit + depth. Lives outside the
    main pocket/plug pipeline because surfacing is a post-glue-up step
    that the user runs separately, after assembling the inlay."""
    chains: list[Chain] = field(default_factory=list)
    gcode: str = ""
    cut_length_mm: float = 0.0
    total_depth_mm: float = 0.0
    bit_name: str = ""
    bit_diameter_mm: float = 0.0


@dataclass
class PreflightSpot:
    """A region of the design where the local feature width is below the
    smallest flat-endmill diameter. The bit can't fit there, so the spot
    stays uncut — the matching plug-feature point will stand proud."""
    cx_mm: float
    cy_mm: float
    area_mm2: float
    max_feature_width_mm: float  # 2× max distance-to-boundary in the spot


@dataclass
class FeaturePreflight:
    """Pre-cut analysis of where the smallest bit physically can't fit.

    Reports the total area and the worst N hot-spots so the operator can
    decide BEFORE running: live with the artifact, add a smaller bit, or
    clean up the SVG. Computed in run_pipeline by rasterising the design
    and thresholding the distance transform at ``smallest_bit_radius``.
    """
    smallest_bit_diameter_mm: float
    smallest_bit_name: str
    sub_bit_total_area_mm2: float
    sub_bit_count: int
    top_spots: list[PreflightSpot]   # largest-area-first, capped at 20


@dataclass
class PipelineResult:
    pocket: Side
    plug: Side
    coverage: CoverageReport | None = None
    recommendation: Recommendation | None = None
    fragility: "FragilityReport | None" = None
    stock_fit: StockFitReport | None = None
    preflight: FeaturePreflight | None = None
    # Geometry of what will actually be visible as plug wood in the assembled
    # inlay, accounting for which features the bits can physically reach.
    # Thin features narrower than the smallest pocket bit's diameter remain
    # uncut (so they show as pocket wood); narrow gaps in the design narrower
    # than the smallest plug bit cannot be carved out around the plug, so they
    # merge into the surrounding plug. xy_tolerance further shrinks the plug.
    finished_geometry: Polygon | MultiPolygon | None = None
    # Pocket cavity + plug feature, BOTH in the pocket (un-mirrored)
    # coordinate frame, derived from the real swept toolpaths. The
    # Finished tab overlays them: pocket_cavity − plug_feature is the
    # tolerable glue gap (green); plug_feature − pocket_cavity is the
    # interference where the plug is oversize and won't seat (red).
    pocket_cavity_geometry: Polygon | MultiPolygon | None = None
    plug_feature_geometry: Polygon | MultiPolygon | None = None


def _emit_side(
    chains_ordered: list[Chain],
    chains_passes: list[Chain],
    gcode_chains: list[Chain],
    *,
    design: Design,
    endmill: Bit,
    material: Material,
    params: InlayParams,
    is_plug: bool,
    title_prefix: str,
) -> Side:
    role = "plug" if is_plug else "pocket"
    title = f"{title_prefix} - {role}"
    section_label = (
        "Plug clearance (around design)"
        if is_plug
        else "Pocket clearance (inside design)"
    )
    effective_material = _apply_speed_overrides(material, params, endmill)
    section = Section(
        bit=endmill, material=effective_material, chains=gcode_chains, label=section_label
    )
    gcode = emit_gcode([section], title=title, params=params, is_plug=is_plug)
    # Single-bit duration estimate, mirroring the multi-bit path.
    try:
        single_duration_s = total_duration_seconds(build_segments(
            chains_passes,
            feed_mm_min=effective_material.feed_mm_min,
            plunge_mm_min=effective_material.plunge_mm_min,
            safe_z_mm=params.safe_z_mm,
        )) if chains_passes else 0.0
    except Exception:
        single_duration_s = 0.0
    # Fixed per-bit overhead: 3 s spindle ramp-up dwell + ~5 s of
    # bit-engagement settling before the planner reaches steady
    # state. Same value the multi-bit path uses.
    if chains_passes:
        single_duration_s += 8.0
    return Side(
        clear_preview=chains_ordered,
        chains_full=chains_passes,
        gcode=gcode,
        cut_length_mm=total_cut_length_mm(chains_passes),
        air_length_mm=total_air_length_mm(chains_passes),
        bit_chains=(
            [(endmill, chains_passes, "Finishing", single_duration_s)]
            if chains_passes else []
        ),
    )


def _tool_change_park_xy_mm(
    design: Design, params: InlayParams, is_plug: bool
) -> tuple[float, float]:
    """Pick a parking position in the work-coordinate frame that sits over
    UNCUT wood, so the operator can re-zero Z against a real surface during a
    tool change.

    - Pocket: the bit cuts INSIDE the design polygon. Park just past the
      design's bounding box on the +X side (typically uncut stock).
    - Plug: the bit cuts the COMPLEMENT around the design. The design polygon
      itself is left intact (it becomes the inlay piece). Park at (0, 0) —
      the centre of the design — which is uncut.
    """
    half_w = design.width_mm / 2.0
    half_h = design.height_mm / 2.0
    if is_plug:
        # Centre of the (centred) design = guaranteed uncut for the plug side.
        return (0.0, 0.0)
    # Pocket: park 10 mm past the design's right edge, mid-height.
    park_x = half_w + 10.0
    park_y = 0.0
    # If the user gave pocket stock dimensions, clamp the park inside them so
    # we don't drive the bit off the wood.
    if params.pocket_stock_width_mm > 0:
        max_x = params.pocket_stock_width_mm / 2.0 - 5.0
        park_x = min(park_x, max_x)
    if params.origin == Origin.BOTTOM_LEFT:
        # In bottom-left mode the chains were shifted by (half_w, half_h).
        park_x += half_w
        park_y += half_h
    return (park_x, park_y)


def witness_mark_position_mm(
    design: Design, params: InlayParams, is_plug: bool
) -> tuple[float, float]:
    """Witness-divot location in work-coordinate mm.

    Per-side deterministic so the Recovery Wizard can recompute the same
    coordinate without storing it. Placed in the stock waste area:
    nominally 5 mm past the design's top-right corner, then clamped to
    stay at least 5 mm inside the stock edges so the divot can never
    plunge into clamps or run off the wood. Falls back to the
    design-relative position when stock dimensions aren't supplied.
    """
    design_margin = 5.0
    stock_margin = 5.0
    half_w = design.width_mm / 2.0
    half_h = design.height_mm / 2.0

    wx = half_w + design_margin
    wy = half_h + design_margin

    stock_w = (
        params.plug_stock_width_mm if is_plug else params.pocket_stock_width_mm
    )
    stock_h = (
        params.plug_stock_height_mm if is_plug else params.pocket_stock_height_mm
    )
    if stock_w > 0:
        max_x = stock_w / 2.0 - stock_margin
        if max_x < wx:
            # Stock is too narrow for the nominal placement — pull the
            # witness closer to the design corner but never inside it.
            wx = max(half_w + 2.0, max_x)
    if stock_h > 0:
        max_y = stock_h / 2.0 - stock_margin
        if max_y < wy:
            wy = max(half_h + 2.0, max_y)
    if is_plug:
        # Plug toolpaths live in the mirrored frame.
        wx = -wx
    return (wx, wy)


def _emit_multi_bit_side(
    bit_chains: list[tuple[Bit, list[Chain]]],
    *,
    design: Design,
    material: Material,
    params: InlayParams,
    is_plug: bool,
    title_prefix: str,
) -> Side:
    """Emit one side's G-code from a sequence of (bit, chains) pairs.

    Falls back to an empty Side if the sequence is empty (e.g. plug
    complement was None). The emitter inserts the M0 + tool-change comments
    between bits automatically.
    """
    role = "plug" if is_plug else "pocket"
    title = f"{title_prefix} - {role}"
    label_root = (
        "Plug clearance (around design)" if is_plug
        else "Pocket clearance (inside design)"
    )
    if not bit_chains:
        return Side(clear_preview=[], chains_full=[], gcode="", cut_length_mm=0, air_length_mm=0)

    sections: list[Section] = []
    all_preview: list[Chain] = []
    all_passes: list[Chain] = []
    bit_chain_breakdown: list[tuple[Bit, list[Chain], str]] = []
    for i, (bit, chains) in enumerate(bit_chains):
        # Each bit gets its own per-bit speed override resolved here so a
        # roughing bit can run faster than the finishing bit on the same side.
        bit_material = _apply_speed_overrides(material, params, bit)
        # Each bit gets its own labelled section. Multi-bit runs label by step
        # ("Roughing 1/3: 3.0 mm endmill") so the user can match the M0
        # tool-change comments to the actual cut order.
        if len(bit_chains) > 1:
            step = f"{i + 1}/{len(bit_chains)}"
            kind = "Finish" if i == len(bit_chains) - 1 else "Rough"
            section_label = f"{label_root} — {kind} {step}: {bit.name}"
        else:
            section_label = label_root
        sections.append(Section(
            bit=bit, material=bit_material, chains=chains, label=section_label,
        ))
        all_preview.extend(chains)
        all_passes.extend(chains)
        role = "Finishing" if i == len(bit_chains) - 1 else "Roughing"
        # Per-bit duration estimate uses each bit's own effective speeds.
        try:
            segs = build_segments(
                chains,
                feed_mm_min=bit_material.feed_mm_min,
                plunge_mm_min=bit_material.plunge_mm_min,
                safe_z_mm=params.safe_z_mm,
            )
            duration_s = total_duration_seconds(segs)
        except Exception:
            duration_s = 0.0
        # Fixed per-bit overhead: 3 s spindle ramp-up dwell (G4 P3
        # emitted after every M3) + ~5 s of bit-engagement settling
        # before the planner reaches steady state. Plus 90 s of
        # tool-change time for any non-first bit (operator manually
        # swaps the bit and re-zeros Z at the M0 pause). Without
        # this, multi-bit estimates were 15-30% optimistic.
        duration_s += 8.0
        if i > 0:
            duration_s += 90.0
        bit_chain_breakdown.append((bit, chains, role, duration_s))

    park_xy = _tool_change_park_xy_mm(design, params, is_plug)
    witness_xy = witness_mark_position_mm(design, params, is_plug) if (
        params.witness_mark_enabled and params.witness_mark_depth_mm > 0
    ) else None
    gcode = emit_gcode(
        sections, title=title, params=params, is_plug=is_plug,
        tool_change_park_xy_mm=park_xy,
        witness_mark_xy_mm=witness_xy,
    )
    return Side(
        clear_preview=all_preview,
        chains_full=all_passes,
        gcode=gcode,
        cut_length_mm=total_cut_length_mm(all_passes),
        air_length_mm=total_air_length_mm(all_passes),
        bit_chains=bit_chain_breakdown,
    )


def _resolve_bit_sequence(
    finishing: Bit, roughing_names: list[str], bit_library: list[Bit] | None
) -> list[Bit]:
    """Build the largest-first ordered bit sequence for a side.

    The finishing bit is always last (it does the fine detail / boundary
    finish). Roughing bits come first, sorted by diameter descending. Bits
    whose diameter is ≤ the finishing bit's are silently dropped — they'd be
    redundant with the finishing pass.
    """
    if not roughing_names or not bit_library:
        return [finishing]
    by_name = {b.name: b for b in bit_library}
    rough_bits: list[Bit] = []
    for name in roughing_names:
        b = by_name.get(name)
        if b is not None and b.diameter_mm > finishing.diameter_mm:
            rough_bits.append(b)
    rough_bits.sort(key=lambda b: -b.diameter_mm)
    return rough_bits + [finishing]


def _medial_axis_cleanup_chains(
    region: Polygon | MultiPolygon,
    bit_diameter_mm: float,
    depth_mm: float,
    *,
    resolution_mm: float = 0.15,
    progress_cb: Callable[[float, str], None] | None = None,
) -> list[Chain]:
    """Generate cleanup chains for a residual region the offset-rings clearer
    can't process — typically thin crescents along curved boundaries where
    ``region.buffer(-bit_radius)`` is empty so no offset rings can be drawn.

    Strategy: rasterise each residual piece, compute its medial-axis skeleton,
    return one chain per skeleton branch. The bit's center follows the
    centerline; its disk sweeps the full residual width because the residual's
    local width is at most ``bit_diameter`` (otherwise the standard offset-
    rings algorithm would have handled it).

    Filters applied to keep chain count manageable:
      * Skip residual pieces whose area is below ``bit_diameter²/2`` — the bit
        can't sweep enough new ground to justify a tool change.
      * Skip skeleton chains shorter than ``bit_diameter`` — same reason.
      * Simplify each chain (Douglas-Peucker at ¼ bit_radius) so pixel-stepped
        zig-zag becomes a clean polyline with one tenth the points.

    Returns chains in (x, y, z) format with z = -depth_mm (single-pass; the
    caller's ``split_into_passes`` will divide as needed).
    """
    if region.is_empty or region.area <= 0:
        return []
    bit_r = bit_diameter_mm / 2.0
    min_piece_area = (bit_diameter_mm ** 2) / 2.0
    min_chain_len_mm = bit_diameter_mm  # 1× diameter, anything shorter is wasted plunge
    simplify_tol = bit_r * 0.25
    pieces = list(region.geoms) if isinstance(region, MultiPolygon) else [region]

    def chain_length(coords: list[tuple[float, float]]) -> float:
        L = 0.0
        for a, b in zip(coords[:-1], coords[1:]):
            L += ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        return L

    from shapely.geometry import LineString

    # Worker that processes one residual piece. Pulled out into its own
    # function so we can run pieces in parallel — each piece's work
    # (rasterize → skeleton → extract chains → simplify) is totally
    # independent, and the heavy compute steps (numpy, skimage,
    # shapely.simplify) all release the GIL, so threading actually
    # delivers wall-clock speedup. AI-generated designs commonly
    # produce 10–50 residual pieces; on an 8-core machine this cuts the
    # cleanup phase from 1–3s sequential to 200–500ms parallel.
    # Smallest distance-transform value (in pixels) at which the bit's disk
    # still fits without spilling past the design boundary. Skeleton points
    # below this threshold lie in a feature narrower than the bit — running
    # the bit there would gouge the wall (e.g. the bit going down the
    # centerline of a 4 mm-wide letter stroke with a 6.35 mm bit overhangs
    # 1+ mm on each side and eats the letter). At those points we split
    # the chain instead; the material stays uncut, which is the correct
    # signal that this bit is too coarse for the feature.
    min_fit_px = bit_r / resolution_mm

    def _process_piece(piece) -> list[Chain]:
        if piece.area < min_piece_area:
            return []
        try:
            from cnc_inlay.geometry.medial_axis import compute_skeleton
            from cnc_inlay.geometry.shape import rasterize
            from cnc_inlay.toolpath.vcarve import extract_chains_px
            mask, transform = rasterize(piece, resolution_mm=resolution_mm, pad_px=2)
            if not mask.any():
                return []
            skel = compute_skeleton(mask, prune_spurs=False)
            px_chains = extract_chains_px(skel.skeleton)
        except Exception:
            return []
        # rasterize() returns a transform whose (0,0) is the PIECE's geometric
        # centre — not the design's. We need world-frame coords, so re-add the
        # piece's centre. (This was the bug that made cleanup chains land at
        # the origin instead of where the residual lives.)
        piece_minx, piece_miny, piece_maxx, piece_maxy = piece.bounds
        cx_world = (piece_minx + piece_maxx) / 2.0
        cy_world = (piece_miny + piece_maxy) / 2.0
        import numpy as _np
        dist_px = skel.distance_px
        rows_max, cols_max = dist_px.shape
        local_out: list[Chain] = []
        for px_chain in px_chains:
            if len(px_chain) < 2:
                continue
            # Split the pixel chain into maximal runs where the bit fits
            # without overcutting the wall. dist_px[r,c] is the distance
            # from skeleton pixel (r,c) to the nearest residual boundary;
            # the bit (radius bit_r) only fits where that distance is at
            # least bit_r. A 6.35 mm bit on a 4 mm-wide letter stroke
            # fails this test along the whole stroke, so the whole chain
            # is dropped — far better than gouging the stroke wider.
            runs: list[list[tuple[int, int]]] = []
            current: list[tuple[int, int]] = []
            for (r, c) in px_chain:
                if 0 <= r < rows_max and 0 <= c < cols_max:
                    fits = float(dist_px[r, c]) >= min_fit_px
                else:
                    fits = True  # bounds-edge skeleton pixel; trust it
                if fits:
                    current.append((r, c))
                elif len(current) >= 2:
                    runs.append(current)
                    current = []
                else:
                    current = []
            if len(current) >= 2:
                runs.append(current)
            for run in runs:
                rows = _np.array([p[0] for p in run], dtype=_np.float64)
                cols = _np.array([p[1] for p in run], dtype=_np.float64)
                xs, ys = transform.pixel_to_mm(rows, cols)
                coords = [
                    (float(x + cx_world), float(y + cy_world))
                    for x, y in zip(xs, ys)
                ]
                # Douglas-Peucker: collapses pixel-stepped runs to straight
                # lines without losing fidelity at the bit's resolution.
                # Drops chain point count by ~10× on typical residuals.
                try:
                    ls = LineString(coords).simplify(simplify_tol, preserve_topology=False)
                    if ls.is_empty:
                        continue
                    if ls.geom_type == "LineString":
                        coords = list(ls.coords)
                    elif ls.geom_type == "MultiLineString":
                        # take the longest segment if simplify split it
                        longest = max(ls.geoms, key=lambda g: g.length)
                        coords = list(longest.coords)
                except Exception:
                    pass  # keep raw coords if simplify fails
                if chain_length(coords) < min_chain_len_mm:
                    continue
                local_out.append([(x, y, float(-depth_mm)) for x, y in coords])
        return local_out

    # Pick the parallelism level. We're running inside the pipeline
    # subprocess that already has the GIL held by Python-level code most
    # of the time — but the heavy phases here (rasterize, skeletonize,
    # simplify) drop into C/numpy and release the GIL, so a small thread
    # pool gives us real wall-clock speedup. Cap at 4 to avoid
    # oversubscribing the user's machine if they have many cores; below
    # 2 pieces, threading overhead outweighs the benefit.
    out: list[Chain] = []
    n_pieces = len(pieces)
    if n_pieces <= 1:
        for i, piece in enumerate(pieces):
            out.extend(_process_piece(piece))
            if progress_cb is not None:
                try:
                    progress_cb((i + 1) / max(n_pieces, 1), f"cleanup {i + 1}/{n_pieces}")
                except Exception:
                    pass
    else:
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed
        max_workers = min(4, max(1, (os.cpu_count() or 2) // 2), n_pieces)
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_process_piece, p) for p in pieces]
            for fut in as_completed(futures):
                try:
                    out.extend(fut.result())
                except Exception:
                    pass  # one piece's failure shouldn't sink the whole batch
                completed += 1
                if progress_cb is not None:
                    try:
                        progress_cb(
                            completed / n_pieces,
                            f"cleanup {completed}/{n_pieces}",
                        )
                    except Exception:
                        pass
    return out


def _chains_swept_area(
    chains: list[Chain], bit_radius_mm: float
) -> Polygon | MultiPolygon | None:
    """Return the actual XY area a bit's disk sweeps as it traverses ``chains``.

    Each chain is approximated as a LineString and dilated by ``bit_radius_mm``
    so the result is the union of the bit's footprint along every cut path.
    Used by rest-machining to compute residual = region - what-was-actually-swept,
    so the next (smaller) bit catches the thin crescents that the larger bit's
    offset-ring chains didn't fully cover along curved boundaries.

    Unions in batches (chunks of 200, snapped to a 5 µm grid) — running one
    big union over the thousands of segments a complex design produces makes
    GEOS throw TopologyException about half the time.
    """
    if not chains:
        return None
    from shapely.geometry import LineString
    import shapely

    swept: list = []
    for chain in chains:
        if len(chain) < 2:
            continue
        try:
            ls = LineString([(x, y) for (x, y, _z) in chain])
            swept.append(ls.buffer(bit_radius_mm, resolution=4))
        except Exception:
            pass
    if not swept:
        return None
    chunks: list = []
    for i in range(0, len(swept), 200):
        try:
            chunks.append(shapely.union_all(swept[i:i + 200], grid_size=0.005))
        except Exception:
            for piece in swept[i:i + 200]:
                chunks.append(piece)
    try:
        result = shapely.union_all(chunks, grid_size=0.01)
    except Exception:
        # Fallback: pairwise union via buffer(0). Slower but doesn't blow up.
        result = chunks[0]
        for c in chunks[1:]:
            try:
                result = result.union(c).buffer(0)
            except Exception:
                pass
    if result.is_empty:
        return None
    if isinstance(result, (Polygon, MultiPolygon)):
        return result
    return None


def _rest_machine_chains(
    region: Polygon | MultiPolygon,
    sequence: list[Bit],
    *,
    depth_mm: float,
    step_over_frac: float,
    pass_depth_mm: float,
    priority_region: Polygon | MultiPolygon | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    progress_lo: float = 0.0,
    progress_hi: float = 100.0,
) -> list[tuple[Bit, list[Chain]]]:
    """Run rest machining on ``region`` with the given largest-first bit sequence.

    Residual passed to each bit = ``region - union of every previous bit's
    actual swept disks along its chains``. The previous-generation code used
    the morphological opening of ``region`` with the previous bit's radius,
    which assumes the offset-ring chains perfectly covered every reachable
    point. In practice they leave thin crescent-shaped gaps along curved
    boundaries (because adjacent rings don't fully overlap there), and those
    gaps then weren't seen as residual — so the next bit didn't pick them up.
    Computing residual from the actual swept geometry catches those crescents.

    ``priority_region`` (optional): when set, every bit AFTER the first/
    largest one only cleans up residual that falls inside this region.
    Used by the plug side to skip "decorative" cleanup of rectangular-
    stock corners — those slivers are well outside the actual plug
    feature and don't justify a tool change to a smaller bit. The
    largest bit still clears the entire ``region`` so the bulk pass
    isn't affected.

    Bits that contribute no new chains are dropped from the returned list, so
    we never pause for a tool change that does nothing.
    """
    out: list[tuple[Bit, list[Chain]]] = []
    cumulative_cleared: Polygon | MultiPolygon | None = None
    n_bits = max(len(sequence), 1)
    span = progress_hi - progress_lo

    def _report(bit_idx: int, sub_pct: float, msg: str) -> None:
        if progress_cb is None:
            return
        # Each bit gets an equal slice of (lo..hi). sub_pct is 0..1 within
        # that slice. So overall pct = lo + (bit_idx + sub_pct) / n_bits * span.
        overall = progress_lo + (bit_idx + sub_pct) / n_bits * span
        try:
            progress_cb(overall, msg)
        except Exception:
            pass

    for idx, bit in enumerate(sequence):
        is_first_bit = (idx == 0)
        is_final_bit = (idx == len(sequence) - 1)
        radius = bit.diameter_mm / 2.0
        _report(idx, 0.05, f"{bit.name}: computing residual region")
        # What this bit needs to clear: everything not already swept by a
        # larger bit. If first in sequence, that's the full original region.
        if cumulative_cleared is None or cumulative_cleared.is_empty:
            target = region
        else:
            try:
                # Rest-machining seam overlap: grow this bit's residual
                # back INTO the already-cleared zone by one full bit
                # diameter before computing its offset rings. Without the
                # overlap, the residual's outer edge IS the scalloped
                # seam of the previous (larger) bit's sweep, and this
                # bit's first offset ring sits one radius inside that —
                # leaving a thin uncut crescent / triangle right at the
                # bit-to-bit boundary. Letting the smaller bit re-trace
                # a diameter-wide band of already-milled (flat-bottomed)
                # wood is harmless and closes the seam completely.
                overlap = bit.diameter_mm
                eroded = cumulative_cleared.buffer(-overlap)
                if eroded.is_empty:
                    target = region
                else:
                    target = region.difference(eroded).buffer(0)
            except Exception:
                try:
                    target = region.difference(cumulative_cleared).buffer(0)
                except Exception:
                    target = region
        # Smaller-bit cleanup is restricted to the priority region (e.g.
        # the band around the plug feature). The largest bit still does
        # its full bulk pass on ``region`` so nothing gets skipped on the
        # primary clearance — only the cosmetic outer-corner residual that
        # would otherwise force a tool change to a 1 mm bit just to clean
        # up a stock corner that isn't part of the actual inlay.
        if not is_first_bit and priority_region is not None and not priority_region.is_empty:
            try:
                target = target.intersection(priority_region).buffer(0)
            except Exception:
                pass
        if target.is_empty:
            continue

        _report(idx, 0.10, f"{bit.name}: computing offset rings")
        # Forward per-ring progress into the bit's 0.10–0.50 sub-slice so
        # the bar moves smoothly while we compute the (often dozens of)
        # offset rings. Without this the bar sits at the same percentage
        # for the whole ring loop, which is the long pole on big designs.
        def _ring_progress(frac: float, msg: str) -> None:
            sub = 0.10 + max(0.0, min(1.0, frac)) * 0.40
            _report(idx, sub, f"{bit.name}: {msg}")

        try:
            raw = compute_endmill_pocket_clear(
                target,
                endmill_diameter_mm=bit.diameter_mm,
                depth_mm=depth_mm,
                step_over_frac=step_over_frac,
                progress_cb=_ring_progress,
            )
        except Exception:
            raw = []

        _report(idx, 0.50, f"{bit.name}: ordering chains")
        ordered_main = order_chains(raw) if raw else []
        # Pass `region` to the merge so connectors that would draw a
        # straight cut through uncut wood (the design feature on the
        # plug side) are refused. Without this guard the largest plug
        # bit would happily merge two rings that sit on opposite sides
        # of a JMU letter, ruining the inlay.
        ordered_main = (
            merge_close_loops(
                ordered_main, bit.diameter_mm * 1.05, inside_region=region,
            )
            if ordered_main else []
        )

        cleanup_chains: list[Chain] = []
        if is_final_bit:
            _report(idx, 0.65, f"{bit.name}: cleaning up thin residuals")
            rings_swept = _chains_swept_area(ordered_main, radius)
            try:
                leftover = (
                    target.difference(rings_swept).buffer(0)
                    if rings_swept is not None else target
                )
            except Exception:
                leftover = target
            if not leftover.is_empty and leftover.area > (radius ** 2):
                # Cleanup runs in parallel across residual pieces and
                # reports per-piece progress into the 0.65–0.80 slice,
                # which on AI designs (10–50 residual pieces) is another
                # silent block we can fill with motion.
                def _cleanup_progress(frac: float, msg: str) -> None:
                    sub = 0.65 + max(0.0, min(1.0, frac)) * 0.15
                    _report(idx, sub, f"{bit.name}: {msg}")

                try:
                    cleanup_chains = _medial_axis_cleanup_chains(
                        leftover, bit.diameter_mm, depth_mm,
                        progress_cb=_cleanup_progress,
                    )
                except Exception:
                    cleanup_chains = []

        if not ordered_main and not cleanup_chains:
            # This bit can't fit anywhere new — skip it (no tool change needed).
            continue
        _report(idx, 0.80, f"{bit.name}: optimizing toolpath order")
        combined = ordered_main + cleanup_chains
        combined = order_chains(combined)
        combined = merge_close_loops(
            combined, bit.diameter_mm * 2.5, inside_region=region,
        )
        full = split_into_passes(combined, pass_depth_mm)
        out.append((bit, full))

        _report(idx, 0.92, f"{bit.name}: computing swept area")
        swept = _chains_swept_area(combined, radius)
        if swept is None:
            continue
        if cumulative_cleared is None:
            cumulative_cleared = swept
        else:
            try:
                cumulative_cleared = cumulative_cleared.union(swept).buffer(0)
            except Exception:
                pass  # keep prior cumulative; worst case = some overlap next pass
    return out


def _generate_pocket_side(
    design: Design,
    centered_geom: Polygon | MultiPolygon,
    endmill: Bit,
    material: Material,
    params: InlayParams,
    title_prefix: str,
    bit_library: list[Bit] | None = None,
    vbit: Bit | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    progress_lo: float = 0.0,
    progress_hi: float = 100.0,
) -> Side:
    sequence = _resolve_bit_sequence(
        endmill, params.pocket_roughing_bits, bit_library
    )

    # V-bit inlay geometry on the POCKET side:
    #
    # For the V-bit's cone to actually CARVE the cavity wall (not just
    # kiss it), the flat-endmill clearance must leave the wall sitting
    # ``L`` mm INSIDE the design line, where L = pocket_depth ×
    # tan(half_angle) (capped at the bit's shank radius). The V-bit
    # then follows that same inset line at full depth, and its cone —
    # spanning ±L at the surface — sweeps the wedge of wall wood out to
    # the original design line. Final cavity: the design line at the
    # surface (so the visible inlay matches the user's design exactly)
    # and (design − L) at the floor, sloped at the bit angle.
    #
    # The PLUG side needs no such shrink: there the endmill clears the
    # complement (leaving the plug feature at the full design size on
    # the cleared floor) and the V-bit follows the full design boundary
    # — its cone removes a wedge from the plug feature's top, leaving it
    # (design − L) at the cut face and (design) at the cleared floor,
    # which after the flip mates the pocket cavity exactly.
    #
    # ``skip_pocket_vbit`` opts out of the pocket V-bit entirely (and
    # therefore the shrink) — use it for designs with features narrower
    # than 2L, which the shrink would erase.
    vbit_active_pocket = (
        vbit is not None and vbit.type == "vbit"
        and not getattr(params, "skip_pocket_vbit", False)
    )
    pocket_geom = centered_geom
    if vbit_active_pocket:
        from cnc_inlay.toolpath.vbit_walls import vbit_lateral_reach_mm
        L = vbit_lateral_reach_mm(
            vbit.angle_deg, params.pocket_depth_mm,
            shank_diameter_mm=vbit.shank_mm,
        )
        if L > 0:
            try:
                shrunk = centered_geom.buffer(-L).buffer(0)
                if not shrunk.is_empty:
                    pocket_geom = shrunk
                else:
                    # The whole design vanished at this inset — every
                    # feature is narrower than 2L. Fall back to the
                    # un-shrunk geometry; the V-bit pass below will
                    # then just kiss the walls (harmless), and the
                    # operator should really enable Skip pocket V-bit.
                    pocket_geom = centered_geom
            except Exception:
                pocket_geom = centered_geom

    bit_chains = _rest_machine_chains(
        pocket_geom, sequence,
        depth_mm=params.pocket_depth_mm,
        step_over_frac=params.pocket_step_over_frac,
        pass_depth_mm=params.pass_depth_mm,
        progress_cb=progress_cb,
        progress_lo=progress_lo,
        progress_hi=progress_hi,
    )

    if vbit_active_pocket:
        from cnc_inlay.toolpath.vbit_walls import compute_vbit_wall_chains
        # V-bit follows ``pocket_geom``'s boundary directly (offset 0):
        # since pocket_geom is already the design inset by L, the V-bit
        # tip path is (design − L). Its cone reaches the original design
        # line at the surface and carves the wall wedge.
        vchains = compute_vbit_wall_chains(
            pocket_geom,
            angle_deg=vbit.angle_deg,
            depth_mm=params.pocket_depth_mm,
            pass_depth_mm=params.pass_depth_mm,
            shank_diameter_mm=vbit.shank_mm,
        )
        if vchains:
            bit_chains = list(bit_chains) + [(vbit, vchains)]

    if params.origin == Origin.BOTTOM_LEFT:
        dx = design.width_mm / 2
        dy = design.height_mm / 2
        bit_chains = [
            (b, _shift_chains(c, dx, dy)) for b, c in bit_chains
        ]

    return _emit_multi_bit_side(
        bit_chains,
        design=design, material=material, params=params,
        is_plug=False, title_prefix=title_prefix,
    )


def _generate_plug_side(
    design: Design,
    centered_geom: Polygon | MultiPolygon,
    endmill: Bit,
    material: Material,
    params: InlayParams,
    title_prefix: str,
    bit_library: list[Bit] | None = None,
    vbit: Bit | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    progress_lo: float = 0.0,
    progress_hi: float = 100.0,
) -> Side:
    mirrored = _mirror_x(centered_geom)
    # XY tolerance: shrink the plug feature by xy_tolerance_mm per side so
    # the plug fits the pocket with a glue layer at every wall. The pocket
    # still gets cut to the design's nominal size; only the plug shrinks.
    # NOTE: when a V-bit is active, xy_tolerance is auto-zeroed by the UI
    # because angled-wall geometry self-aligns; any XY shrink would cause
    # the plug to seat too deep (XY shrink ÷ tan(half-angle) of Z drop).
    if params.xy_tolerance_mm > 0:
        try:
            shrunk = mirrored.buffer(-params.xy_tolerance_mm).buffer(0)
            if not shrunk.is_empty:
                mirrored = shrunk
            # If buffer ate the design entirely, fall back to original (the
            # tolerance was set too large for the design's smallest feature).
        except Exception:
            pass
    complement = _design_complement(
        mirrored, params.boundary_offset_mm,
        stock_width_mm=params.plug_stock_width_mm,
        stock_height_mm=params.plug_stock_height_mm,
    )
    if complement is None:
        return _emit_multi_bit_side(
            [], design=design, material=material, params=params,
            is_plug=True, title_prefix=title_prefix,
        )

    sequence = _resolve_bit_sequence(
        endmill, params.plug_roughing_bits, bit_library
    )
    plug_depth = params.pocket_depth_mm + params.glue_gap_mm
    # Priority region for finer-bit cleanup on the plug side: a band
    # around the mirrored design feature, sized to the LARGEST roughing
    # bit's diameter. After the largest bit clears the complement, the
    # residual it couldn't reach is geometrically bounded — at most one
    # bit-diameter wide along the design boundary (concave corners,
    # tight necks). Smaller bits only need to clean up THAT zone.
    #
    # We previously used `boundary_offset_mm` for this width, but that
    # parameter is meant for the plug-profile margin (extra wood around
    # the feature for clamping) — typically 15 mm or more. On stock
    # that's only a few mm wider than the design, a 15 mm band reaches
    # all the way into the outer stock corners and forces the 1 mm
    # finishing bit to chase residual in pure-waste territory. Tying
    # the priority width to the largest bit's diameter (× 1.5 for a
    # small safety margin) keeps finer bits focused on the design's
    # cleanup zone where they actually matter.
    largest_bit_d = sequence[0].diameter_mm if sequence else 6.0
    # Width = one full largest-bit diameter, no extra multiplier. That
    # corresponds geometrically to the maximum residual width the
    # largest bit can leave behind along the design boundary, and on
    # tight stock (e.g. 50 × 127 mm stock around a 36 × 110 mm design,
    # 7 mm margin) keeps the priority band from reaching into the
    # outer-stock corner zone where finer bits would just be polishing
    # waste material.
    priority_band_width = largest_bit_d
    plug_priority: Polygon | MultiPolygon | None = None
    try:
        envelope = mirrored.buffer(priority_band_width)
        intersected = envelope.intersection(complement).buffer(0)
        if not intersected.is_empty:
            plug_priority = intersected
    except Exception:
        plug_priority = None

    bit_chains = _rest_machine_chains(
        complement, sequence,
        depth_mm=plug_depth,
        step_over_frac=params.pocket_step_over_frac,
        pass_depth_mm=params.pass_depth_mm,
        priority_region=plug_priority,
        progress_cb=progress_cb,
        progress_lo=progress_lo,
        progress_hi=progress_hi,
    )

    # V-bit wall-finish pass on the plug side — same V-bit, same angle as
    # the pocket. Operates on the MIRRORED design polygon (the plug
    # feature itself, not the surrounding-waste complement). After the
    # operator flips the plug stock and presses it into the pocket,
    # these angled walls mate with the pocket's V-walls and wedge tight.
    if vbit is not None and vbit.type == "vbit":
        from cnc_inlay.toolpath.vbit_walls import compute_vbit_wall_chains
        smallest_prev_d = sequence[-1].diameter_mm if sequence else 0.0
        vchains = compute_vbit_wall_chains(
            mirrored,
            angle_deg=vbit.angle_deg,
            depth_mm=params.pocket_depth_mm,
            pass_depth_mm=params.pass_depth_mm,
            prev_bit_diameter_mm=smallest_prev_d,
            is_plug=True,
            shank_diameter_mm=vbit.shank_mm,
        )
        if vchains:
            bit_chains = list(bit_chains) + [(vbit, vchains)]

    if params.origin == Origin.BOTTOM_LEFT:
        dx = design.width_mm / 2
        dy = design.height_mm / 2
        bit_chains = [
            (b, _shift_chains(c, dx, dy)) for b, c in bit_chains
        ]

    return _emit_multi_bit_side(
        bit_chains,
        design=design, material=material, params=params,
        is_plug=True, title_prefix=title_prefix,
    )


def _smallest_bit_diameter_mm(
    endmill: Bit, roughing_names: list[str], bit_library: list[Bit] | None
) -> float:
    """Return the smallest bit diameter actually used on a side.

    Pocket and plug each cut with their own ``endmill`` plus zero or more
    roughing bits picked by name from ``bit_library``. The smallest of those
    is what determines the finest feature that can be reached.
    """
    diameters = [endmill.diameter_mm]
    if bit_library and roughing_names:
        by_name = {b.name: b for b in bit_library}
        for name in roughing_names:
            bit = by_name.get(name)
            if bit is not None:
                diameters.append(bit.diameter_mm)
    return min(diameters)


def _pocket_swept_union(
    pocket_side: "Side", params: InlayParams
) -> Polygon | MultiPolygon | None:
    """Union of every pocket bit's actual swept footprint. None when
    there are no pocket chains to measure."""
    bit_chains = getattr(pocket_side, "bit_chains", None)
    if not bit_chains:
        return None
    swept: Polygon | MultiPolygon | None = None
    for entry in bit_chains:
        bit, chains = entry[0], entry[1]
        if getattr(bit, "type", "endmill") == "vbit":
            # The V-bit cone reaches its full lateral width L only at the
            # surface — that's the footprint that matters for the visible
            # finished face. Buffer the tip path by L.
            try:
                from cnc_inlay.toolpath.vbit_walls import vbit_lateral_reach_mm
                r = max(0.05, vbit_lateral_reach_mm(
                    bit.angle_deg, params.pocket_depth_mm,
                    shank_diameter_mm=getattr(bit, "shank_mm", 0.0),
                ))
            except Exception:
                r = 0.1
        else:
            r = bit.diameter_mm / 2.0
        try:
            s = _chains_swept_area(chains, r)
        except Exception:
            s = None
        if s is None or s.is_empty:
            continue
        swept = s if swept is None else swept.union(s).buffer(0)
    return swept


def _pocket_cavity_from_swept(
    pocket_side: "Side",
    centered: Polygon | MultiPolygon,
    params: InlayParams,
) -> Polygon | MultiPolygon | None:
    """The pocket cavity as it will ACTUALLY be cut: the union of every
    pocket bit's swept footprint, clipped to the design. No xy_tolerance
    shrink — that's the plug side's job. Returns None if unmeasurable."""
    swept = _pocket_swept_union(pocket_side, params)
    if swept is None or swept.is_empty:
        return None
    try:
        cavity = swept.intersection(centered).buffer(0)
    except Exception:
        cavity = swept
    if cavity.is_empty or not isinstance(cavity, (Polygon, MultiPolygon)):
        return None
    return cavity


def _plug_feature_geometry(
    plug_side: "Side",
    plug_input_geom: Polygon | MultiPolygon,
    params: InlayParams,
) -> Polygon | MultiPolygon | None:
    """The plug feature (the inlay lump) the plug toolpaths actually leave
    standing, returned in the POCKET (un-mirrored) coordinate frame so it
    can be compared directly against the pocket cavity.

    The plug bits clear the complement AROUND the mirrored feature; what
    they don't sweep, inside a generous envelope of the feature, is what
    stands as the plug. Where the bits can't carve a tight gap, the uncut
    waste merges into the feature — making the plug locally oversize,
    which is exactly the interference we want the Finished tab to flag.
    """
    bit_chains = getattr(plug_side, "bit_chains", None)
    if not bit_chains or plug_input_geom is None or plug_input_geom.is_empty:
        return None
    swept: Polygon | MultiPolygon | None = None
    for entry in bit_chains:
        bit, chains = entry[0], entry[1]
        if getattr(bit, "type", "endmill") == "vbit":
            continue  # V-bit only bevels the wall; ignore for the footprint
        try:
            s = _chains_swept_area(chains, bit.diameter_mm / 2.0)
        except Exception:
            s = None
        if s is None or s.is_empty:
            continue
        swept = s if swept is None else swept.union(s).buffer(0)
    if swept is None or swept.is_empty:
        return None
    # The plug side mirrors its input and shrinks it by xy_tolerance so a
    # glue layer fits — reproduce that to get the intended feature.
    mirrored = _mirror_x(plug_input_geom)
    if params.xy_tolerance_mm > 0:
        try:
            shr = mirrored.buffer(-params.xy_tolerance_mm).buffer(0)
            if not shr.is_empty:
                mirrored = shr
        except Exception:
            pass
    try:
        envelope = mirrored.buffer(2.0)
        feature = envelope.difference(swept).buffer(0)
    except Exception:
        feature = mirrored
    if feature.is_empty:
        return None
    # Keep only the lump(s) that actually contain the design feature —
    # discard far-field uncut waste out in the stock corners.
    parts = list(feature.geoms) if feature.geom_type.startswith("Multi") else [feature]
    keep = [p for p in parts if p.intersects(mirrored)]
    if not keep:
        return None
    from shapely.ops import unary_union
    feature = unary_union(keep)
    # Back to the pocket frame.
    unmirrored = _mirror_x(feature)
    if unmirrored.is_empty or not isinstance(unmirrored, (Polygon, MultiPolygon)):
        return None
    return unmirrored


def _finished_geometry_from_swept(
    pocket_side: "Side",
    centered: Polygon | MultiPolygon,
    params: InlayParams,
    vbit: Bit | None,
) -> Polygon | MultiPolygon | None:
    """Visible plug area in the finished inlay, derived from the ACTUAL
    pocket toolpaths: the cut cavity, shrunk by xy_tolerance. Unlike the
    morphological-opening estimate this changes whenever the bit lineup,
    V-bit, or step-over changes. Returns None when unmeasurable (caller
    falls back to the opening estimate)."""
    visible = _pocket_cavity_from_swept(pocket_side, centered, params)
    if visible is None:
        return None
    if params.xy_tolerance_mm > 0:
        try:
            shrunk = visible.buffer(-params.xy_tolerance_mm).buffer(0)
            if not shrunk.is_empty:
                visible = shrunk
        except Exception:
            pass
    if visible.is_empty:
        return None
    return visible


def _achievable_finished_geometry(
    centered: Polygon | MultiPolygon,
    pocket_bit_diameter_mm: float,
    plug_bit_diameter_mm: float,
    xy_tolerance_mm: float,
) -> Polygon | MultiPolygon | None:
    """Compute the visible plug area in the assembled inlay.

    Three effects are modelled:
    - Pocket bit can't reach into design features narrower than its diameter,
      so those areas stay as pocket wood (morphological opening).
    - Plug bit can't carve gaps in the surrounding wood narrower than its
      diameter, so narrow gaps in the design get filled (closing of design).
    - xy_tolerance shrinks the plug everywhere so a glue layer fits.
    """
    r_pocket = pocket_bit_diameter_mm / 2.0
    r_plug = plug_bit_diameter_mm / 2.0
    try:
        pocket_clear = centered.buffer(-r_pocket).buffer(r_pocket).buffer(0)
    except Exception:
        pocket_clear = centered
    try:
        plug_remains = centered.buffer(r_plug).buffer(-r_plug).buffer(0)
    except Exception:
        plug_remains = centered
    try:
        visible = pocket_clear.intersection(plug_remains).buffer(0)
    except Exception:
        visible = pocket_clear
    if xy_tolerance_mm > 0:
        try:
            visible = visible.buffer(-xy_tolerance_mm).buffer(0)
        except Exception:
            pass
    if visible.is_empty:
        return None
    if isinstance(visible, (Polygon, MultiPolygon)):
        return visible
    return None


def run_pipeline(
    job: Job,
    *,
    bit_library: list[Bit] | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> PipelineResult:
    """Generate pocket + plug toolpaths and G-code for a Job.

    ``progress_cb(pct, msg)`` (optional) reports phase progress 0..100 so the
    caller can show a real percent bar instead of an indeterminate spinner.
    Phase budget: 0–2% setup, 2–48% pocket side, 48–94% plug side,
    94–100% coverage / fragility / stock-fit / recommendation reports.
    """
    def _report(pct: float, msg: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    _report(1, "Centring design…")
    title = f"CNC Inlay Studio - {job.design.basename}"
    centered = _center_polygon(job.design.polygon)

    _report(2, "Generating pocket toolpaths…")
    pocket = _generate_pocket_side(
        job.design, centered, job.endmill, job.pocket_material, job.params, title,
        bit_library=bit_library,
        vbit=job.vbit,
        progress_cb=progress_cb, progress_lo=2.0, progress_hi=48.0,
    )

    # The plug must mate with the pocket as it's ACTUALLY cut, not with
    # the ideal design. Wherever a bit couldn't fully reach the pocket —
    # a sub-bit nose tip, a tight concave corner — the cavity falls short
    # of the drawing. If the plug were still cut to the ideal design, its
    # matching feature would stand proud and hit unmilled pocket wood. So
    # the plug side is generated from the pocket's real swept cavity: the
    # plug feature is trimmed to exactly what the pocket can receive.
    # Falls back to the ideal design if the cavity can't be measured.
    plug_geom = centered
    pocket_cavity = _pocket_cavity_from_swept(pocket, centered, job.params)
    if pocket_cavity is not None and not pocket_cavity.is_empty:
        plug_geom = pocket_cavity

    _report(48, "Generating plug toolpaths…")
    plug = _generate_plug_side(
        job.design, plug_geom, job.endmill, job.plug_material, job.params, title,
        bit_library=bit_library,
        vbit=job.vbit,
        progress_cb=progress_cb, progress_lo=48.0, progress_hi=94.0,
    )

    _report(95, "Coverage check…")
    coverage = _coverage_report(
        centered, job.endmill.diameter_mm, job.params.pocket_step_over_frac
    )
    _report(96, "Plug-fragility check…")
    fragility = check_plug_fragility(centered, job.endmill.diameter_mm)
    _report(97, "Stock-fit check…")
    stock_fit = _check_stock_fit(centered, job.params)

    recommendation: Recommendation | None = None
    if bit_library:
        _report(98, "Bit recommendation…")
        from cnc_inlay.geometry.medial_axis import compute_skeleton
        from cnc_inlay.geometry.shape import rasterize

        try:
            mask, _ = rasterize(centered, resolution_mm=job.params.resolution_mm)
            skel = compute_skeleton(mask)
            recommendation = recommend_bits(bit_library, skel, job.params.resolution_mm)
        except Exception:
            recommendation = None

    _report(99, "Finishing up…")
    pocket_bit_d = _smallest_bit_diameter_mm(
        job.endmill, job.params.pocket_roughing_bits, bit_library
    )
    plug_bit_d = _smallest_bit_diameter_mm(
        job.endmill, job.params.plug_roughing_bits, bit_library
    )
    # Finished-inlay geometry. Prefer the swept-area model — it's built
    # from the ACTUAL computed pocket toolpaths, so it reflects every
    # bit choice, the V-bit shrink, the medial-axis bit-fits guard, and
    # the rest-machining residual. Where the pocket genuinely isn't
    # hollowed out, the plug can't seat → that spot shows pocket wood.
    # Fall back to the morphological-opening estimate only when there
    # are no pocket chains to measure (degenerate single-bit edge case).
    finished_geometry = _finished_geometry_from_swept(
        pocket, centered, job.params, job.vbit
    )
    if finished_geometry is None:
        finished_geometry = _achievable_finished_geometry(
            centered, pocket_bit_d, plug_bit_d, job.params.xy_tolerance_mm
        )

    # Preflight: find spots where the smallest flat endmill can't fit.
    # Those areas stay uncut on the pocket; the matching plug-feature
    # points will stand proud. Run on the same centered design at the
    # job's resolution; never raises (returns None on any failure).
    smallest_d, smallest_name = _smallest_flat_bit(
        job.endmill, job.params.pocket_roughing_bits, bit_library
    )
    preflight = _compute_feature_preflight(
        centered, smallest_d, smallest_name, job.params.resolution_mm
    )

    # Pocket cavity + plug feature in the pocket frame, for the Finished
    # tab's green-gap / red-interference overlay.
    pocket_cavity_geometry = _pocket_cavity_from_swept(
        pocket, centered, job.params
    )
    plug_feature_geometry = _plug_feature_geometry(
        plug, plug_geom, job.params
    )

    _report(100, "Done.")
    return PipelineResult(
        pocket=pocket, plug=plug, coverage=coverage,
        recommendation=recommendation, fragility=fragility,
        stock_fit=stock_fit, preflight=preflight,
        finished_geometry=finished_geometry,
        pocket_cavity_geometry=pocket_cavity_geometry,
        plug_feature_geometry=plug_feature_geometry,
    )


def _smallest_flat_bit(
    endmill: Bit, roughing_names: list[str], bit_library: list[Bit] | None
) -> tuple[float, str]:
    """Smallest-diameter flat endmill in the pocket lineup (= the
    finishing endmill OR any smaller roughing endmill — unusual but
    possible). Returns (diameter_mm, name). V-bits don't count."""
    smallest = endmill
    by_name = {b.name: b for b in (bit_library or [])}
    for n in roughing_names or []:
        b = by_name.get(n)
        if b is None or getattr(b, "type", "endmill") == "vbit":
            continue
        if b.diameter_mm > 0 and b.diameter_mm < smallest.diameter_mm:
            smallest = b
    return (smallest.diameter_mm, smallest.name)


def _compute_feature_preflight(
    geom: Polygon | MultiPolygon,
    smallest_bit_diameter_mm: float,
    smallest_bit_name: str,
    resolution_mm: float,
) -> "FeaturePreflight | None":
    """Find features narrower than the smallest flat endmill.

    Uses the medial axis (skeleton) of the design: at each skeleton
    pixel, ``distance_px`` is the local inscribed-circle radius =
    half-feature-width. Skeleton pixels where that's below the bit
    radius mark genuinely-too-narrow features. We deliberately do NOT
    flag the design's boundary strip (= every foreground pixel within
    bit_radius of the wall), because the bit's flute sweeps that strip
    when it traces the outermost offset ring — it isn't actually uncut.

    Approximate footprint of each too-narrow stretch = the skeleton
    pixels dilated by their local half-width × 2. Then connected
    components → one spot per cluster, with centroid + footprint area.
    """
    if smallest_bit_diameter_mm <= 0 or geom is None or geom.is_empty:
        return None
    try:
        from cnc_inlay.geometry.medial_axis import compute_skeleton
        from cnc_inlay.geometry.shape import rasterize
        import numpy as _np
        from scipy import ndimage as _ndi
        # Finer resolution than the toolpath compute — narrow spots are
        # the whole point and need pixel accuracy. 0.05 mm caps the
        # raster at a few-megapixel image even for big designs.
        res = min(resolution_mm, 0.05)
        mask, transform = rasterize(geom, resolution_mm=res, pad_px=2)
        if not mask.any():
            return None
        skel = compute_skeleton(mask, prune_spurs=False)
        dist_px = skel.distance_px
        bit_r_px = smallest_bit_diameter_mm / (2.0 * res)
        # The skeleton's distance value AT a skeleton pixel = the
        # inscribed-circle radius of the feature there. < bit_r ⇒ the
        # bit physically can't fit in that feature.
        narrow_skel = skel.skeleton & (dist_px < bit_r_px)
        if not narrow_skel.any():
            return FeaturePreflight(
                smallest_bit_diameter_mm=smallest_bit_diameter_mm,
                smallest_bit_name=smallest_bit_name,
                sub_bit_total_area_mm2=0.0,
                sub_bit_count=0, top_spots=[],
            )
        # Approximate the FEATURE footprint by dilating each narrow
        # skeleton pixel by its own local distance (the feature width
        # there). This grows the 1-px-wide skeleton trace back out to
        # the feature width, so the area number is meaningful (≈ the
        # uncut area you'll have on the pocket).
        # Cap the dilation kernel at ceil(2 * bit_r_px) since we only
        # care about features below bit width — anything wider isn't in
        # this set.
        kernel_radius = max(1, int(_np.ceil(bit_r_px)))
        struct = _ndi.generate_binary_structure(2, 2)
        footprint = _ndi.binary_dilation(
            narrow_skel, structure=struct, iterations=kernel_radius
        ) & mask
        if not footprint.any():
            return FeaturePreflight(
                smallest_bit_diameter_mm=smallest_bit_diameter_mm,
                smallest_bit_name=smallest_bit_name,
                sub_bit_total_area_mm2=0.0,
                sub_bit_count=0, top_spots=[],
            )
        labels, n_components = _ndi.label(footprint)
        px_area = res * res
        spots: list[PreflightSpot] = []
        sums = _ndi.sum(_np.ones_like(footprint), labels, index=range(1, n_components + 1))
        centroids = _ndi.center_of_mass(footprint, labels, index=range(1, n_components + 1))
        max_dist = _ndi.maximum(
            dist_px * narrow_skel, labels, index=range(1, n_components + 1)
        )
        total_area = 0.0
        for i, count in enumerate(sums):
            area = float(count) * px_area
            if area < 0.10:  # ignore sub-0.10 mm² rasterisation noise
                continue
            cy_px, cx_px = centroids[i]
            cx_mm, cy_mm = transform.pixel_to_mm(
                _np.array([cy_px]), _np.array([cx_px])
            )
            spots.append(PreflightSpot(
                cx_mm=float(cx_mm[0]),
                cy_mm=float(cy_mm[0]),
                area_mm2=area,
                max_feature_width_mm=2.0 * float(max_dist[i]) * res,
            ))
            total_area += area
        spots.sort(key=lambda s: -s.area_mm2)
        return FeaturePreflight(
            smallest_bit_diameter_mm=smallest_bit_diameter_mm,
            smallest_bit_name=smallest_bit_name,
            sub_bit_total_area_mm2=total_area,
            sub_bit_count=len(spots),
            top_spots=spots[:20],
        )
    except Exception:
        return None


def compute_surfacing_plan(
    bit: Bit,
    *,
    stock_width_mm: float,
    stock_height_mm: float,
    pocket_depth_mm: float,
    glue_gap_mm: float,
    plug_thickness_mm: float,
    feed_mm_min: float | None = None,
    plunge_mm_min: float | None = None,
    rpm: int | None = None,
    pass_depth_mm: float | None = None,
    safe_z_mm: float = 5.0,
    extra_depth_mm: float | None = None,
) -> SurfacingPlan:
    """Generate a SurfacingPlan: raster zig-zag G-code to plane the back
    of an assembled inlay, exposing the inlay flush with the pocket-side
    wood.

    Auto-derives the depth to remove from glue-gap + safety margin if
    ``extra_depth_mm`` isn't supplied. The plug stands proud of the
    pocket wood by ``glue_gap_mm`` after assembly; surfacing removes
    that plus a small safety margin (default 0.2 mm) to absorb Z-zero
    variation across the panel.

    Stock dimensions are the assembled-inlay panel's width and height.
    For a typical workflow these match the pocket stock dimensions.
    """
    from cnc_inlay.gcode.emitter import Section, emit_gcode
    from cnc_inlay.toolpath.surfacing import (
        SurfacingParams, compute_surfacing_chains, total_cut_length_mm,
    )

    if extra_depth_mm is None:
        extra_depth_mm = 0.2  # Z-zero variation buffer; user can override.
    total_depth = max(0.05, glue_gap_mm + extra_depth_mm)

    # Conservative defaults for a hobby-CNC surfacing bit on a 500 W
    # spindle. Caller can override per-bit speeds.
    sp = SurfacingParams(
        stock_width_mm=stock_width_mm,
        stock_height_mm=stock_height_mm,
        bit=bit,
        total_depth_mm=total_depth,
        feed_mm_min=feed_mm_min if feed_mm_min is not None else 1000.0,
        plunge_mm_min=plunge_mm_min if plunge_mm_min is not None else 300.0,
        rpm=rpm if rpm is not None else min(10000, bit.max_rpm),
        pass_depth_mm=pass_depth_mm if pass_depth_mm is not None else 0.4,
        safe_z_mm=safe_z_mm,
    )
    chains = compute_surfacing_chains(sp)
    if not chains:
        return SurfacingPlan(
            bit_name=bit.name,
            bit_diameter_mm=bit.diameter_mm,
            total_depth_mm=total_depth,
        )

    # Reuse the inlay G-code emitter so safety machinery (M3+G4 dwell,
    # safe-Z lifts, end-of-section M5) is identical to inlay cuts.
    surfacing_material = Material(
        name="Surfacing pass",
        feed_mm_min=sp.feed_mm_min,
        plunge_mm_min=sp.plunge_mm_min,
        rpm=sp.rpm,
        max_doc_mm=sp.pass_depth_mm,
    )
    section = Section(
        bit=bit,
        material=surfacing_material,
        chains=chains,
        label=f"Surfacing — remove {total_depth:.2f} mm to expose inlay",
    )
    params = InlayParams(safe_z_mm=safe_z_mm)
    gcode = emit_gcode(
        [section],
        title=f"CNC Inlay Studio — surfacing ({bit.name})",
        params=params,
        is_plug=False,
    )
    return SurfacingPlan(
        chains=chains,
        gcode=gcode,
        cut_length_mm=total_cut_length_mm(chains),
        total_depth_mm=total_depth,
        bit_name=bit.name,
        bit_diameter_mm=bit.diameter_mm,
    )


def _check_stock_fit(
    centered: Polygon | MultiPolygon, params: InlayParams
) -> StockFitReport:
    """Verify the design + plug boundary offset both fit on their respective
    pieces of wood. 0-valued stock dimensions mean "skip this check."""
    minx, miny, maxx, maxy = centered.bounds
    design_w = maxx - minx
    design_h = maxy - miny
    issues = StockFitReport()

    if params.pocket_stock_width_mm > 0 and params.pocket_stock_height_mm > 0:
        if design_w > params.pocket_stock_width_mm + 1e-3:
            issues.pocket_issues.append(
                f"Design width ({design_w:.1f} mm) > pocket stock width "
                f"({params.pocket_stock_width_mm:.1f} mm). Resize the design "
                f"or use wider stock."
            )
        if design_h > params.pocket_stock_height_mm + 1e-3:
            issues.pocket_issues.append(
                f"Design height ({design_h:.1f} mm) > pocket stock height "
                f"({params.pocket_stock_height_mm:.1f} mm). Resize the design "
                f"or use taller stock."
            )

    if params.plug_stock_width_mm > 0 and params.plug_stock_height_mm > 0:
        # Plug needs the design + the boundary-offset margin all around.
        plug_w_required = design_w + 2 * params.boundary_offset_mm
        plug_h_required = design_h + 2 * params.boundary_offset_mm
        if plug_w_required > params.plug_stock_width_mm + 1e-3:
            issues.plug_issues.append(
                f"Design ({design_w:.1f} mm) + 2× boundary offset "
                f"({params.boundary_offset_mm:.1f} mm) = {plug_w_required:.1f} mm "
                f"> plug stock width ({params.plug_stock_width_mm:.1f} mm). "
                f"Reduce boundary offset, resize, or use wider plug stock."
            )
        if plug_h_required > params.plug_stock_height_mm + 1e-3:
            issues.plug_issues.append(
                f"Design ({design_h:.1f} mm) + 2× boundary offset "
                f"({params.boundary_offset_mm:.1f} mm) = {plug_h_required:.1f} mm "
                f"> plug stock height ({params.plug_stock_height_mm:.1f} mm). "
                f"Reduce boundary offset, resize, or use taller plug stock."
            )
    return issues
