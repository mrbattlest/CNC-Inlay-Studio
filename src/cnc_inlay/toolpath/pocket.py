"""Endmill flat-bottom pocket clearing for the residual region the V-bit can't reach.

A V-bit at full ``pocket_depth`` only clears a strip of width
``2 · pocket_depth · tan(angle/2)`` along the medial axis. Anywhere wider
than that has uncleared material under what would be the floor of the inlay.
This module computes the **residual region** (design polygon insetting by the
V-bit's reach), kerf-compensates it for the endmill, and emits concentric
inward offsets at the step-over distance so the endmill can flat-bottom the
remaining material at ``Z = -pocket_depth``.
"""
from __future__ import annotations

import math

from shapely.geometry import MultiPolygon, Polygon

from cnc_inlay.geometry.offset import pocket_offset_rings
from cnc_inlay.toolpath.vcarve import Chain


def _polygon_to_chains(
    geom: Polygon | MultiPolygon, z_mm: float
) -> list[Chain]:
    chains: list[Chain] = []
    polys: list[Polygon] = (
        list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    )
    for poly in polys:
        if poly.is_empty:
            continue
        ext = list(poly.exterior.coords)
        if len(ext) >= 3:
            chains.append([(x, y, z_mm) for x, y in ext])
        for interior in poly.interiors:
            ring = list(interior.coords)
            if len(ring) >= 3:
                chains.append([(x, y, z_mm) for x, y in ring])
    return chains


def compute_endmill_pocket_clear(
    geom: Polygon | MultiPolygon,
    endmill_diameter_mm: float,
    depth_mm: float,
    step_over_frac: float = 0.5,
) -> list[Chain]:
    """Endmill flat-bottom clearing of an entire polygon (or MultiPolygon).

    Concentric inward offsets at ``step_over``, starting at ``-endmill_radius``
    so the bit's flute kisses the polygon boundary. Each resulting ring is
    emitted as a closed-loop chain at flat ``Z = -depth_mm``.

    Used for both:
      - The pocket (clear the inside of the design polygon)
      - The plug  (clear the complement region around the design polygon)
    """
    if endmill_diameter_mm <= 0 or depth_mm <= 0:
        return []
    rings = pocket_offset_rings(
        geom, endmill_diameter_mm, step_over_frac=step_over_frac
    )
    if not rings:
        return []
    z = -depth_mm
    chains: list[Chain] = []
    for ring in rings:
        chains.extend(_polygon_to_chains(ring, z))
    return chains


# --- legacy V-carve hybrid clearing (kept for the deferred V-carve technique) ---


def compute_pocket_clear(
    geom: Polygon | MultiPolygon,
    vbit_angle_deg: float,
    endmill_diameter_mm: float,
    pocket_depth_mm: float,
    step_over_frac: float = 0.5,
    extra_z_mm: float = 0.0,
) -> list[Chain]:
    """Endmill flat-bottom clearing chains for everything the V-bit can't reach
    at ``pocket_depth_mm``.

    Args:
        geom: design polygon (already in centred mm coordinates).
        vbit_angle_deg: full included angle of the V-bit (e.g. 60).
        endmill_diameter_mm: diameter of the clearance endmill.
        pocket_depth_mm: target flat-bottom depth (positive value).
        step_over_frac: endmill step-over as a fraction of its diameter.
        extra_z_mm: additional cut depth (e.g. glue gap on plug side); positive
            number, applied as deeper Z.

    Returns:
        List of chains, each a list of (x, y, z) tuples. ``z`` is negative.
    """
    if pocket_depth_mm <= 0 or endmill_diameter_mm <= 0:
        return []
    half_angle = math.radians(vbit_angle_deg / 2)
    if half_angle <= 0:
        return []

    # Lateral reach of the V-bit at full pocket depth.
    v_reach_mm = pocket_depth_mm * math.tan(half_angle)
    if v_reach_mm <= 0:
        return []

    # Residual region (where V-bit walls don't reach the floor).
    from cnc_inlay.geometry.offset import offset_polygon

    residual = offset_polygon(geom, -v_reach_mm)
    if residual is None or residual.is_empty:
        return []

    # Concentric inward offsets — first ring is the kerf-compensated boundary,
    # subsequent rings step further in by step_over.
    rings = pocket_offset_rings(
        residual, endmill_diameter_mm, step_over_frac=step_over_frac
    )
    if not rings:
        return []

    z_mm = -(pocket_depth_mm + extra_z_mm)
    chains: list[Chain] = []
    for ring in rings:
        chains.extend(_polygon_to_chains(ring, z_mm))
    return chains
