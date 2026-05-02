"""Polygon offset utilities — thin wrappers over shapely's buffer().

shapely.buffer is mature, handles MultiPolygons + holes correctly, and uses
the same Clipper engine under the hood for polygon dilation. We use it
directly instead of pyclipper to avoid hand-rolling the orientation/hole
re-attachment logic that complex SVGs (e.g. nested even-odd designs) trip up.
"""
from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

# A tight join style for inlay work — sharp corners stay sharp at small offsets,
# but rounded for outward offsets to avoid spikes.
_RESOLUTION = 16  # subdivision count for round joins / quadrant


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
    """
    if geom.is_empty:
        return None
    if delta_mm == 0:
        return geom
    join_style = "round" if delta_mm > 0 else "mitre"
    try:
        result = geom.buffer(
            delta_mm,
            resolution=_RESOLUTION,
            join_style=join_style,
            mitre_limit=2.0,
        )
    except Exception:
        return None
    return _ensure_polygonal(result)


def pocket_offset_rings(
    geom: Polygon | MultiPolygon,
    bit_diameter_mm: float,
    step_over_frac: float = 0.5,
    max_passes: int = 200,
) -> list[Polygon | MultiPolygon]:
    """Concentric inward offsets for endmill flat-bottom pocket clearing.

    First ring is offset by ``-bit_radius`` (so the bit's flute kisses the
    pocket wall); each subsequent ring steps further in by ``step_over``
    until the polygon empties.
    """
    if bit_diameter_mm <= 0:
        return []
    radius = bit_diameter_mm / 2.0
    step_over = bit_diameter_mm * step_over_frac
    if step_over <= 0:
        return []

    rings: list[Polygon | MultiPolygon] = []
    delta = -radius
    for i in range(max_passes):
        if i > 0:
            delta -= step_over
        ring = offset_polygon(geom, delta)
        if ring is None or ring.is_empty:
            break
        rings.append(ring)
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
