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


def vbit_lateral_reach_mm(
    angle_deg: float,
    depth_mm: float,
    shank_diameter_mm: float = 0.0,
) -> float:
    """How far the V-bit's outer cutting edge sits from its centerline at
    a given depth.

    The cone math ``d × tan(half_angle)`` is only valid up to the depth
    where the cone has widened to match the shank diameter — beyond
    that the bit is a cylindrical shank, not a cone, and the cutting
    edge sits at a fixed ``shank_radius`` from the centerline.

    For a tapered V-bit (no engraving flat tip), the cone-to-shank
    transition depth is geometrically determined by:

        cone_height = shank_diameter / (2 × tan(half_angle))

    Examples (verified against Amana / IDC Woodcraft / Whiteside specs):
      * 30° V-bit, 1/4" (6.35 mm) shank → cone height ≈ 11.85 mm
      * 30° V-bit, 1/8" (3.175 mm) shank → cone height ≈ 5.92 mm
      * 60° V-bit, 1/4" (6.35 mm) shank → cone height ≈ 5.50 mm
      * 90° V-bit, 1/4" (6.35 mm) shank → cone height ≈ 3.18 mm

    Pass ``shank_diameter_mm = 0`` to disable the transition cap (legacy
    behaviour — pure cone math, useful in tests).
    """
    if angle_deg <= 0 or angle_deg >= 180 or depth_mm <= 0:
        return 0.0
    half_angle_rad = math.radians(angle_deg / 2.0)
    tan_half = math.tan(half_angle_rad)
    if tan_half <= 0:
        return 0.0
    cone_reach = depth_mm * tan_half
    if shank_diameter_mm <= 0:
        return cone_reach
    shank_radius = shank_diameter_mm / 2.0
    # Once the cone reach catches up to the shank radius, the bit is the
    # shank — cylindrical, not conical — and the cutting edge stops
    # widening with depth. Cap the lateral reach there.
    return min(cone_reach, shank_radius)


def first_contact_depth_mm(
    angle_deg: float,
    depth_mm: float,
    prev_bit_diameter_mm: float,
) -> float:
    """Depth at which the V-bit's growing cutting circle first reaches
    wood that the previous (smallest endmill) pass left uncut.

    The V-bit's centerline sits at lateral_reach = depth × tan(half) from
    the original wall. The previous endmill cleared a vertical-walled
    cavity inset from the original wall by ``prev_bit_radius``. While
    the V-bit is shallower than this depth, its cutting circle at the
    surface — radius |z|×tan(half) — fits entirely inside the cleared
    region and the bit cuts pure air. Once it grows past that, it starts
    chewing into the wedge of wood between the cleared edge and the
    original wall.

        first_contact = depth − prev_bit_radius / tan(half_angle)

    Returns 0 when the formula would go negative (V-bit makes contact
    immediately) or when angle_deg / prev_bit_diameter_mm aren't usable.
    """
    if angle_deg <= 0 or angle_deg >= 180 or prev_bit_diameter_mm <= 0:
        return 0.0
    half_rad = math.radians(angle_deg / 2.0)
    t = math.tan(half_rad)
    if t <= 0:
        return 0.0
    contact = depth_mm - (prev_bit_diameter_mm / 2.0) / t
    return max(0.0, contact)


def _split_passes_starting_at(
    chains: list[Chain], pass_depth_mm: float, start_depth_mm: float,
) -> list[Chain]:
    """Like split_into_passes but skip the first ``start_depth_mm`` of
    travel — the bit goes straight down to that depth on the first pass
    instead of stepping there ``pass_depth_mm`` at a time.

    Used by the V-bit pass to skip the air-cutting passes at the top of
    the cone where the bit's diameter is too narrow to reach uncleared
    wood. ``start_depth_mm`` is positive (mm below the surface).
    """
    import math as _math
    if pass_depth_mm <= 0:
        return chains
    output: list[Chain] = []
    for chain in chains:
        deepest = max(-z for _, _, z in chain)  # positive
        # If the chain doesn't go deeper than where we want to start,
        # there's nothing to cut — drop it entirely (caller's bug if so).
        if deepest <= start_depth_mm + 1e-9:
            continue
        # Remaining depth to cover after the initial dive to start_depth.
        remaining = deepest - start_depth_mm
        # First pass goes to ``start_depth_mm + pass_depth`` (or to the
        # full depth if that overshoots). Subsequent passes step by
        # pass_depth. Final pass uses each point's true Z.
        n_passes = max(1, _math.ceil(remaining / pass_depth_mm))
        for i in range(1, n_passes + 1):
            ceiling = start_depth_mm + pass_depth_mm * i  # positive
            if i == n_passes:
                output.append(chain)
            else:
                clipped = [(x, y, max(z, -ceiling)) for x, y, z in chain]
                output.append(clipped)
    return output


def compute_vbit_wall_chains(
    geom: Polygon | MultiPolygon,
    angle_deg: float,
    depth_mm: float,
    pass_depth_mm: float = 0.0,
    prev_bit_diameter_mm: float = 0.0,
    is_plug: bool = False,
    shank_diameter_mm: float = 0.0,
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
        prev_bit_diameter_mm: the smallest endmill that ran before the
            V-bit (the finishing bit). Used to compute the depth at
            which the V-bit first contacts wood — passes shallower than
            that are pure air-cuts and wasted machine time, so we skip
            straight down to first contact instead of stepping there
            ``pass_depth_mm`` at a time. 0 disables the optimisation.
        is_plug: True when emitting the plug-side bevel pass. Flips the
            centerline offset so the V-bit traces a path OUTSIDE the
            plug feature (in the cleared complement) rather than inside
            it. With centerline outside, the bit's cone tip plunges
            into the cleared floor and its flank sweeps the wedge of
            wood OUTSIDE the plug boundary — producing a wall that
            slopes outward going down (wider at the floor, narrower at
            the surface). When the plug stock is then flipped face-down
            and pressed into the pocket, those walls become inward-
            sloping from the top, mating exactly with the pocket's
            inward-sloping V-walls.

    Returns:
        List of closed-loop chains following the design boundary
        offset by the V-bit's lateral reach (inward for pocket, outward
        for plug). Empty when the offset operation fails (e.g. pocket
        narrower than 2 × lateral_reach).
    """
    if depth_mm <= 0:
        return []
    lateral_reach = vbit_lateral_reach_mm(
        angle_deg, depth_mm, shank_diameter_mm=shank_diameter_mm,
    )
    if lateral_reach <= 0:
        return []

    # The V-bit's centerline path is the design boundary offset by
    # ``lateral_reach``. The DIRECTION of the offset depends on which
    # side we're cutting:
    #
    #   * Pocket — design polygon represents the cavity. We INSET (negative
    #     offset) so the centerline is inside the cleared cavity (in air).
    #     The cone's flank sweeps the wedge of wood OUTSIDE the design
    #     boundary, creating an inward-sloping cavity wall.
    #
    #   * Plug — design polygon represents the plug feature itself. We
    #     OUTSET (positive offset) so the centerline is in the cleared
    #     complement (in air, outside the plug). The cone's flank sweeps
    #     the wedge of wood OUTSIDE the plug boundary (which is wood
    #     that gets discarded as waste anyway), but more importantly
    #     the cone's profile leaves the plug with an outward-sloping
    #     wall that mates correctly with the pocket after the stock is
    #     flipped.
    offset = +lateral_reach if is_plug else -lateral_reach
    centerline = offset_polygon(geom, offset)
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
        # Skip air-cutting passes when we know what bit ran before.
        # 0.10 mm safety margin so the first real pass takes a tiny
        # nibble rather than landing exactly at the wood surface
        # (which would give the bit no engagement to break the chip).
        if prev_bit_diameter_mm > 0:
            start_depth = max(
                0.0,
                first_contact_depth_mm(angle_deg, depth_mm, prev_bit_diameter_mm)
                - 0.10,
            )
            if start_depth > 0:
                chains = _split_passes_starting_at(
                    chains, pass_depth_mm, start_depth
                )
            else:
                chains = split_into_passes(chains, pass_depth_mm)
        else:
            chains = split_into_passes(chains, pass_depth_mm)
    return chains
