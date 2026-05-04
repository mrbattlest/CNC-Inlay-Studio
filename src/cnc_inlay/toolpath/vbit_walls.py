"""V-bit wall-finish toolpath for angled-wall inlay.

When the user picks a V-bit as the wall-finish tool, the pipeline calls
:func:`compute_vbit_wall_chains` to get a perimeter pass that the V-bit
runs AFTER the flat-endmill clearing is done. The V-bit's centerline
follows the design boundary inset by the bit's lateral reach at full
depth; the bit's outer cutting edge at the surface (Z=0) traces the
original design boundary, carving a V-shaped bevel from the surface
down to the pocket floor.

The same flip-and-mirror plug workflow that already works for flat
endmills handles the V-bit angles automatically — angled walls cut on
the plug stock end up as the inverse angle once the stock is flipped,
so the plug's V-walls mate exactly with the pocket's V-walls. No new
fixturing step required.
"""
from __future__ import annotations

import math

from shapely.geometry import MultiPolygon, Polygon

from cnc_inlay.geometry.offset import offset_polygon
from cnc_inlay.toolpath.passes import split_into_passes
from cnc_inlay.toolpath.vcarve import Chain


def vbit_lateral_reach_mm(angle_deg: float, depth_mm: float) -> float:
    """How far the V-bit's outer cutting edge sits from its centerline at
    a given depth. ``angle_deg`` is the bit's full included angle; the
    half-angle drives the trig.
    """
    if angle_deg <= 0 or angle_deg >= 180 or depth_mm <= 0:
        return 0.0
    half_angle_rad = math.radians(angle_deg / 2.0)
    return depth_mm * math.tan(half_angle_rad)


def compute_vbit_wall_chains(
    geom: Polygon | MultiPolygon,
    angle_deg: float,
    depth_mm: float,
    pass_depth_mm: float = 0.0,
) -> list[Chain]:
    """Generate V-bit chains for the angled-wall finish pass.

    Args:
        geom: the design polygon (centred mm coords). For the plug side
            the caller passes the mirrored polygon — same as for the
            flat-endmill flow.
        angle_deg: bit's full included angle (e.g. 60 for a "60° V-bit").
        depth_mm: pocket depth (positive number; the chain Z values are
            written as ``-depth_mm``).
        pass_depth_mm: max depth removed per pass. 0 means single-pass
            (V-bits handle deeper passes than flat endmills, but
            multi-pass is still safer in hardwood).

    Returns:
        List of closed-loop chains following the design boundary inset
        by the V-bit's lateral reach. Empty when the bit can't reach
        the floor without self-overlap (i.e. pocket narrower than
        2 × lateral_reach).
    """
    if depth_mm <= 0:
        return []
    lateral_reach = vbit_lateral_reach_mm(angle_deg, depth_mm)
    if lateral_reach <= 0:
        return []

    # The V-bit's centerline path is the design boundary inset by the
    # lateral reach: at that offset, the bit's outer edge at Z=0 just
    # kisses the original boundary while the tip plunges to -depth_mm.
    centerline = offset_polygon(geom, -lateral_reach)
    if centerline is None or centerline.is_empty:
        return []

    chains: list[Chain] = []
    z = -depth_mm
    polys = (
        list(centerline.geoms)
        if isinstance(centerline, MultiPolygon) else [centerline]
    )
    for poly in polys:
        if poly.is_empty:
            continue
        ext = list(poly.exterior.coords)
        if len(ext) >= 3:
            chains.append([(x, y, z) for x, y in ext])
        for interior in poly.interiors:
            ring = list(interior.coords)
            if len(ring) >= 3:
                chains.append([(x, y, z) for x, y in ring])

    if pass_depth_mm > 0:
        chains = split_into_passes(chains, pass_depth_mm)
    return chains
