"""Outer boundary cut for the plug — frees the inlay piece from waste stock.

The endmill traces a profile around the (mirrored) design polygon, offset
outward by ``endmill_radius + boundary_offset_mm``. The cut goes deeper than
the plug's lowest carved point so the plug separates cleanly from its
surrounding waste collar.
"""
from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon

from cnc_inlay.geometry.offset import offset_polygon
from cnc_inlay.toolpath.vcarve import Chain


def compute_boundary_cut(
    geom: Polygon | MultiPolygon,
    endmill_diameter_mm: float,
    boundary_offset_mm: float,
    depth_mm: float,
) -> list[Chain]:
    """Single-pass profile cut around the plug perimeter.

    Args:
        geom: (mirrored) design polygon for the plug.
        endmill_diameter_mm: diameter of the clearance endmill.
        boundary_offset_mm: extra XY margin between plug edge and cut.
        depth_mm: positive cut depth (Z = -depth_mm in output).

    Returns:
        List of chains (one per exterior ring after offsetting). Empty if
        offsetting fails.
    """
    if endmill_diameter_mm <= 0 or boundary_offset_mm <= 0:
        return []
    radius = endmill_diameter_mm / 2.0
    profile = offset_polygon(geom, radius + boundary_offset_mm)
    if profile is None or profile.is_empty:
        return []

    polys: list[Polygon] = (
        list(profile.geoms) if isinstance(profile, MultiPolygon) else [profile]
    )
    chains: list[Chain] = []
    z = -depth_mm
    for poly in polys:
        ext = list(poly.exterior.coords)
        if len(ext) >= 3:
            chains.append([(x, y, z) for x, y in ext])
    return chains
