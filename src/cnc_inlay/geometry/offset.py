"""Polygon offset utilities — thin wrappers over shapely's buffer().

shapely.buffer is mature, handles MultiPolygons + holes correctly, and uses
the same Clipper engine under the hood for polygon dilation. We use it
directly instead of pyclipper to avoid hand-rolling the orientation/hole
re-attachment logic that complex SVGs (e.g. nested even-odd designs) trip up.
"""
from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

# Round joins both inward and outward, with enough subdivisions per
# quadrant to faithfully follow tight concave details. Earlier code used
# mitre joins on negative (inward) offsets — they're cheaper but they
# truncate sharp concave notches with a straight shortcut. On a design
# with hanging-ear features (e.g. dachshund silhouettes), the bit then
# carves across the notch and removes wood that should stay (the
# underside of the ear). Round joins produce enough vertices around
# concave corners that no shortcut happens.
_RESOLUTION = 32  # subdivision count for round joins / quadrant


def _ensure_polygonal(g: BaseGeometry) -> Polygon | MultiPolygon | None:
    if g.is_empty:
        return None
    if isinstance(g, (Polygon, MultiPolygon)):
        return g
    # GeometryCollection or LineString fallout — keep only polygonal parts.
    if hasattr(g, "geoms"):
        polys = [x for x in g.geoms if isinstance(x, Polygon) and not x.is_empty]
        if not polys:
            return None
        return polys[0] if len(polys) == 1 else MultiPolygon(polys)
    return None


def offset_polygon(
    geom: Polygon | MultiPolygon, delta_mm: float
) -> Polygon | MultiPolygon | None:
    """Offset a polygon by ``delta_mm``.

    Positive grows the polygon outward; negative shrinks inward.
    Returns None if the result is empty.

    Always uses round joins with high resolution (see _RESOLUTION).
    Mitre joins on negative offsets shortcut concave notches with a flat
    edge — the bit then cuts across the notch and removes design detail
    (e.g. the underside of a hanging ear). Round joins follow the
    concave contour faithfully.
    """
    if geom.is_empty:
        return None
    if delta_mm == 0:
        return geom
    try:
        result = geom.buffer(
            delta_mm,
            resolution=_RESOLUTION,
            join_style="round",
        )
    except Exception:
        return None
    polygonal = _ensure_polygonal(result)
    # Defensive containment check on inward offsets. The buffer should
    # mathematically be a subset of the original (Minkowski erosion), but
    # tiny FP slop can produce a result that overshoots the original by
    # microns at tight concaves. Re-intersect with the original to clip
    # any such overshoot — guarantees the bit's centerline path stays
    # inside the design.
    if delta_mm < 0 and polygonal is not None:
        try:
            clipped = polygonal.intersection(geom)
            polygonal = _ensure_polygonal(clipped)
        except Exception:
            pass
    return polygonal


def pocket_offset_rings(
    geom: Polygon | MultiPolygon,
    bit_diameter_mm: float,
    step_over_frac: float = 0.5,
    max_passes: int = 200,
    progress_cb: "Callable[[float, str], None] | None" = None,
) -> list[Polygon | MultiPolygon]:
    """Concentric inward offsets for endmill flat-bottom pocket clearing.

    First ring is offset by ``-bit_radius`` (so the bit's flute kisses the
    pocket wall); each subsequent ring steps further in by ``step_over``
    until the polygon empties.

    ``progress_cb(fraction, msg)`` (optional) is invoked once per ring with
    a 0..1 fraction estimated from the geometry's shorter bounding-box
    dimension. The actual ring count isn't known upfront (we loop until the
    polygon empties), so the fraction is a soft estimate — clamped to 0.99
    until we actually finish so we never report ``done`` early.
    """
    if bit_diameter_mm <= 0:
        return []
    radius = bit_diameter_mm / 2.0
    step_over = bit_diameter_mm * step_over_frac
    if step_over <= 0:
        return []

    # Estimate how many rings we expect so we can report a meaningful 0..1
    # fraction during the loop. Rings advance from the boundary inward by
    # `step_over` each iteration, so a rough upper bound is the inradius —
    # which we approximate by half the shorter side of the AABB. This will
    # over-estimate for narrow non-rectangular shapes (so the bar moves a
    # little fast at first) but never under-estimates by enough to cause a
    # late-stage stall.
    try:
        minx, miny, maxx, maxy = geom.bounds
        shorter_side = min(maxx - minx, maxy - miny)
        est_rings = max(1, int(shorter_side / (2.0 * step_over)))
    except Exception:
        est_rings = 50  # generic fallback if bounds aren't available

    rings: list[Polygon | MultiPolygon] = []
    delta = -radius
    for i in range(max_passes):
        if i > 0:
            delta -= step_over
        ring = offset_polygon(geom, delta)
        if ring is None or ring.is_empty:
            break
        rings.append(ring)
        if progress_cb is not None:
            # Cap fraction at 0.99 so we never claim done from inside the
            # loop — the caller emits 1.0 when the loop terminates.
            frac = min(0.99, (i + 1) / est_rings)
            try:
                progress_cb(frac, f"offset ring {i + 1}")
            except Exception:
                pass
    return rings


def polygon_to_rings(
    geom: Polygon | MultiPolygon,
) -> list[list[tuple[float, float]]]:
    """Flatten a (Multi)Polygon into a list of closed rings (exterior + interiors)."""
    polys: list[Polygon] = (
        list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    )
    rings: list[list[tuple[float, float]]] = []
    for poly in polys:
        if poly.is_empty:
            continue
        ext = list(poly.exterior.coords)
        if len(ext) >= 3:
            rings.append(ext)
        for interior in poly.interiors:
            ring = list(interior.coords)
            if len(ring) >= 3:
                rings.append(ring)
    return rings
