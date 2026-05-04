"""Surfacing (spoilboard / fly-cutting) toolpath.

Used to remove the plug backing after the inlay is glued together. The
operator clamps the inlaid panel face-down on the bed and runs this
toolpath to plane the back side flush — exposing the inlay flush with
the pocket-side wood.

Different from inlay toolpaths: a single rectangular region, raster
zig-zag pattern for max coverage per pass, uniform shallow depth,
single-bit (no rest machining), no design import.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from cnc_inlay.core.job import Bit
from cnc_inlay.toolpath.passes import split_into_passes
from cnc_inlay.toolpath.vcarve import Chain


# Sane defaults for hobby CNCs (Lunyee 3018 PRO MAX target).
_DEFAULT_STEP_OVER_FRAC = 0.70   # 70% of bit diameter — good chip clearance, smooth finish
_DEFAULT_MAX_DOC_MM = 0.4        # max depth of cut per pass for a 19–25 mm bit on a 500 W spindle
_DEFAULT_FEED_MM_MIN = 1000.0    # hardwood; soften on tougher woods, raise on softer
_DEFAULT_RPM = 10000


@dataclass
class SurfacingParams:
    """Inputs for surfacing toolpath generation. All in mm.

    The pipeline auto-derives ``total_depth_mm`` from ``glue_gap_mm``
    plus a small safety margin so the bit doesn't skip any high spots
    from machine Z-zero variation; the user can override.
    """
    stock_width_mm: float
    stock_height_mm: float
    bit: Bit
    total_depth_mm: float
    feed_mm_min: float = _DEFAULT_FEED_MM_MIN
    plunge_mm_min: float = 300.0
    rpm: int = _DEFAULT_RPM
    pass_depth_mm: float = _DEFAULT_MAX_DOC_MM
    step_over_frac: float = _DEFAULT_STEP_OVER_FRAC
    safe_z_mm: float = 5.0


@dataclass
class SurfacingResult:
    chains: list[Chain] = field(default_factory=list)
    gcode: str = ""
    cut_length_mm: float = 0.0


def compute_surfacing_chains(params: SurfacingParams) -> list[Chain]:
    """Generate raster zig-zag chains over the stock rectangle.

    The bit's first pass starts at one corner; subsequent strokes step
    over by ``step_over_frac × bit.diameter_mm`` perpendicular to the
    cutting direction. Direction alternates each stroke (zig-zag) so
    the bit never lifts mid-row.
    """
    if params.stock_width_mm <= 0 or params.stock_height_mm <= 0:
        return []
    if params.total_depth_mm <= 0:
        return []
    if params.bit.diameter_mm <= 0:
        return []

    diam = params.bit.diameter_mm
    step = max(0.1, diam * params.step_over_frac)
    radius = diam / 2.0

    # Stock is centred on the origin (matching the rest of the app's
    # coord convention). The bit's centerline must stay ``radius``
    # inside the stock edge so the cut envelope sits on the rectangle.
    x_min = -params.stock_width_mm / 2.0 + radius
    x_max =  params.stock_width_mm / 2.0 - radius
    y_min = -params.stock_height_mm / 2.0 + radius
    y_max =  params.stock_height_mm / 2.0 - radius
    if x_max <= x_min or y_max <= y_min:
        # Stock smaller than the bit — return an empty list so callers
        # can refuse the cut with a clear error.
        return []

    z = -params.total_depth_mm

    # Decide the long axis. Cutting along the LONGER dimension means
    # fewer direction reversals (each reversal is a stop+start that
    # slows the cut and adds chatter risk).
    width = x_max - x_min
    height = y_max - y_min
    cut_along_x = width >= height

    # Build a SINGLE connected zig-zag chain instead of one chain per
    # row. The bit cuts to row N's end, then steps over (at depth)
    # straight into row N+1's start, alternating direction each row.
    # This eliminates the lift+rapid+plunge that would otherwise
    # happen between every adjacent row — which on a 100×100 mm panel
    # at 70% step-over with a 19 mm bit is ~7 redundant lift cycles
    # per cut, ~20 mm of wasted Z travel each. The step-over moves at
    # depth are safe because they sweep through wood the next row
    # would cut anyway (the bit's footprint overlaps adjacent rows
    # by 1-step_over of its diameter, ≈30% at the default setting).
    points: list[tuple[float, float, float]] = []
    if cut_along_x:
        y = y_min
        direction = +1  # +1 = X+ stroke, -1 = X- stroke
        while y <= y_max + 1e-6:
            stroke_start = (x_min if direction > 0 else x_max, y, z)
            stroke_end = (x_max if direction > 0 else x_min, y, z)
            if not points:
                points.append(stroke_start)
            else:
                # Step-over connector from previous row's end to this
                # row's start — at depth, sweeps through partially-
                # uncut material that the next stroke covers fully.
                points.append(stroke_start)
            points.append(stroke_end)
            y += step
            direction = -direction
    else:
        x = x_min
        direction = +1
        while x <= x_max + 1e-6:
            stroke_start = (x, y_min if direction > 0 else y_max, z)
            stroke_end = (x, y_max if direction > 0 else y_min, z)
            if not points:
                points.append(stroke_start)
            else:
                points.append(stroke_start)
            points.append(stroke_end)
            x += step
            direction = -direction

    chains: list[Chain] = [points] if len(points) >= 2 else []

    # Each pass goes to its target depth in one shot, but if total depth
    # exceeds the bit's safe DOC, split into multiple raster layers
    # going progressively deeper. Reuses the inlay pipeline's pass
    # splitter so the depth-clipping math is identical.
    if params.pass_depth_mm > 0 and params.total_depth_mm > params.pass_depth_mm:
        chains = split_into_passes(chains, params.pass_depth_mm)
    return chains


def total_cut_length_mm(chains: list[Chain]) -> float:
    total = 0.0
    for ch in chains:
        for a, b in zip(ch[:-1], ch[1:]):
            dx = b[0] - a[0]
            dy = b[1] - a[1]
            dz = b[2] - a[2]
            total += math.sqrt(dx * dx + dy * dy + dz * dz)
    return total
