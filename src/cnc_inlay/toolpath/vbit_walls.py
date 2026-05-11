"""V-bit inlay toolpath — true medial-axis V-carve (v1.2.0).

Earlier versions traced the design boundary with a fixed-Z chain. That's
NOT how V-carve inlay works — the canonical algorithm (F-Engrave / Vectric)
puts the V-bit's TIP on the **medial axis** of the design polygon (the
locus of points equidistant from the boundary), with Z varying point by
point so the bit's UPPER cutting edge traces the design boundary at the
surface:

    Z(p) = -r(p) / tan(half_angle)

where r(p) is the distance from medial-axis point p to the nearest
boundary. Wide regions get the bit deeper; narrow regions stay shallower.

For inlay, that surface is shifted up by ``flat_depth`` and clipped at
z=0, producing a flat floor wherever the bit can reach ``-flat_depth``
and a shallower V-cut wherever the local feature width can't accommodate
the cone going that deep:

    Z(p) = max(-flat_depth, -r(p) / tan(half_angle) + flat_depth · ε)
         ≈ min(0, -r(p)/tan(half) + flat_depth)
         clipped to [-flat_depth, 0]

Two side-specific knobs:

* ``pocket_allowance_mm`` (POCKET only) — small NEGATIVE inset that
  artificially widens the pocket walls so the plug doesn't bind on a
  tight angle match. F-Engrave implements this by faking a smaller bit
  diameter; we apply it directly to the polygon (buffer outward by
  ``allowance``) before rasterizing.

* ``surface_clearance_mm`` (PLUG only) — Z offset that shifts the entire
  plug pass up so the plug stock stands proud of the pocket surface
  after pressing. Operator planes/sands flush after gluing to expose a
  clean visible inlay edge.

Both halves are PRISMATIC FRUSTUMS of the same V-angle. The wedge fit
comes from the cone angles matching, NOT from any clever offset trick.
"""
from __future__ import annotations

import math
from collections.abc import Callable

from shapely.geometry import MultiPolygon, Polygon

from cnc_inlay.toolpath.passes import split_into_passes
from cnc_inlay.toolpath.vcarve import Chain


def vbit_lateral_reach_mm(
    angle_deg: float,
    depth_mm: float,
    shank_diameter_mm: float = 0.0,
) -> float:
    """How far the V-bit's outer cutting edge sits from its centerline at
    a given depth. Capped at ``shank_radius`` once the cone has widened
    enough to merge into the cylindrical shank.

    Examples (verified against Amana / IDC Woodcraft / Whiteside specs):
      * 30° V-bit, 1/4" (6.35 mm) shank → cone height ≈ 11.85 mm
      * 30° V-bit, 1/8" (3.175 mm) shank → cone height ≈ 5.92 mm
      * 60° V-bit, 1/4" (6.35 mm) shank → cone height ≈ 5.50 mm
      * 90° V-bit, 1/4" (6.35 mm) shank → cone height ≈ 3.18 mm
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
    return min(cone_reach, shank_radius)


def compute_vbit_inlay_chains(
    geom: Polygon | MultiPolygon,
    angle_deg: float,
    flat_depth_mm: float,
    pass_depth_mm: float = 0.0,
    *,
    pocket_allowance_mm: float = 0.0,
    surface_clearance_mm: float = 0.0,
    shank_diameter_mm: float = 0.0,
    resolution_mm: float = 0.15,
    progress_cb: Callable[[float, str], None] | None = None,
) -> list[Chain]:
    """Generate true medial-axis V-carve inlay chains.

    Args:
        geom: design polygon (centred mm coords). For the plug side the
            caller passes the mirrored polygon, same as flat-endmill.
        angle_deg: V-bit's full included angle (e.g. 30 for "30° V-bit").
        flat_depth_mm: target floor depth (positive). Wherever the cone
            can reach this depth without overshooting the local feature
            width, the floor flattens out at -flat_depth_mm.
        pass_depth_mm: max depth removed per pass (multi-pass split).
            0 means single-pass. Pass-splitting clips Z to a series of
            ceilings, exactly as for flat-endmill chains.
        pocket_allowance_mm: pocket-side wall slack. Buffers the input
            polygon outward by this amount so the medial-axis V-cut
            ends up slightly wider than the design — preventing the
            plug from binding on a tight angle match. Pass 0 on the
            plug side.
        surface_clearance_mm: plug-side Z offset. Shifts the whole pass
            DOWN by this amount (deeper Z values) so the plug stock
            stands proud of the pocket surface after pressing. Operator
            planes flush after gluing. Pass 0 on the pocket side.
        shank_diameter_mm: V-bit shank diameter for cone-to-shank cap.
        resolution_mm: rasterisation pixel size. Smaller = more accurate
            but slower; 0.15 mm matches the cleanup-chain default.
        progress_cb: optional progress reporter (frac 0..1, msg).

    Returns:
        List of variable-Z chains in (x, y, z) format with z negative
        below surface and ≥ -flat_depth_mm. Each chain is one connected
        component of the medial axis. Empty when geometry can't host a
        meaningful V-cut (degenerate polygon, no interior medial axis).
    """
    if flat_depth_mm <= 0:
        return []
    half_rad = math.radians(angle_deg / 2.0)
    tan_half = math.tan(half_rad)
    if tan_half <= 0:
        return 0.0  # type: ignore[return-value]

    # Pocket allowance: buffer the design outward so the medial-axis
    # V-cut produces walls slightly wider than the nominal design.
    # F-Engrave does this by shrinking the effective bit diameter; we
    # apply the same effect directly on the polygon — equivalent
    # geometric result, simpler to reason about.
    if pocket_allowance_mm and pocket_allowance_mm != 0.0:
        try:
            buffered = geom.buffer(pocket_allowance_mm).buffer(0)
            if not buffered.is_empty:
                geom = buffered
        except Exception:
            pass

    if geom.is_empty or geom.area <= 0:
        return []

    # Cone-to-shank transition limits how deep the bit can effectively
    # cut. If flat_depth_mm exceeds the cone height, clip there for the
    # max-depth math.
    effective_max_depth = flat_depth_mm
    if shank_diameter_mm > 0:
        cone_height = (shank_diameter_mm / 2.0) / tan_half
        effective_max_depth = min(flat_depth_mm, cone_height)

    # Process each piece of the polygon independently (a MultiPolygon
    # is just a collection of disjoint design features).
    pieces: list[Polygon] = (
        list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    )

    out: list[Chain] = []
    for piece_idx, piece in enumerate(pieces):
        if piece.is_empty or piece.area <= 0:
            continue
        try:
            chains_for_piece = _vcarve_one_piece(
                piece,
                tan_half=tan_half,
                flat_depth_mm=flat_depth_mm,
                effective_max_depth=effective_max_depth,
                surface_clearance_mm=surface_clearance_mm,
                resolution_mm=resolution_mm,
            )
        except Exception:
            continue
        out.extend(chains_for_piece)
        if progress_cb is not None:
            try:
                progress_cb(
                    (piece_idx + 1) / max(len(pieces), 1),
                    f"v-carve piece {piece_idx + 1}/{len(pieces)}",
                )
            except Exception:
                pass

    if pass_depth_mm > 0 and out:
        # Multi-pass split clips Z to a series of ceilings (inherited
        # from the flat-endmill flow). At each ceiling, points whose
        # native Z is deeper get clamped to that ceiling — so the bit
        # progressively works its way down from surface to flat_depth.
        out = split_into_passes(out, pass_depth_mm)
    return out


def _vcarve_one_piece(
    piece: Polygon,
    *,
    tan_half: float,
    flat_depth_mm: float,
    effective_max_depth: float,
    surface_clearance_mm: float,
    resolution_mm: float,
) -> list[Chain]:
    """Produce variable-Z V-carve chains for ONE design polygon.

    Rasterises the piece, computes the medial axis with per-pixel
    distance-to-boundary, walks each connected branch, converts pixel
    coordinates back to world mm, and emits a chain whose Z varies
    point-by-point per the V-carve formula.
    """
    import numpy as np
    from shapely.geometry import LineString

    from cnc_inlay.geometry.medial_axis import compute_skeleton
    from cnc_inlay.geometry.shape import rasterize
    from cnc_inlay.toolpath.vcarve import extract_chains_px

    mask, transform = rasterize(piece, resolution_mm=resolution_mm, pad_px=2)
    if not mask.any():
        return []
    # Spur pruning is disabled for V-carve: it's tuned for cleanup
    # chains and aggressively trims branches that look "small relative
    # to the local distance transform" — which on smooth shapes
    # (rectangles, circles) can wipe the entire skeleton. The full,
    # un-pruned medial axis is exactly what V-carve needs.
    skel = compute_skeleton(mask, prune_spurs=False)
    px_chains = extract_chains_px(skel.skeleton)
    if not px_chains:
        return []

    distance_px = skel.distance_px

    # rasterize() returns a transform whose origin is the piece's own
    # geometric centre, not the design's. Re-add the piece's world
    # centre so the chain coordinates land at the right XY in design
    # space.
    pminx, pminy, pmaxx, pmaxy = piece.bounds
    cx_world = (pminx + pmaxx) / 2.0
    cy_world = (pminy + pmaxy) / 2.0

    out: list[Chain] = []
    for px_chain in px_chains:
        if len(px_chain) < 2:
            continue
        rows = np.array([p[0] for p in px_chain], dtype=np.float64)
        cols = np.array([p[1] for p in px_chain], dtype=np.float64)
        xs, ys = transform.pixel_to_mm(rows, cols)
        # Per-pixel distance to nearest boundary (mm).
        rs_px = np.array(
            [distance_px[r, c] for r, c in px_chain],
            dtype=np.float64,
        )
        rs_mm = rs_px * resolution_mm

        # V-carve Z per point: -r/tan(half), shifted up by flat_depth
        # then clipped at the surface.
        zs = -rs_mm / tan_half + flat_depth_mm
        zs = np.minimum(zs, 0.0)
        zs = np.maximum(zs, -flat_depth_mm)
        # Ensure we don't exceed the cone-to-shank cap at the deepest
        # points (clipping below where the bit physically can't reach).
        if effective_max_depth < flat_depth_mm:
            zs = np.maximum(zs, -effective_max_depth)
        # Surface clearance shifts the whole pass DOWN by the requested
        # amount (more negative Z) so the plug stock stands proud after
        # assembly. Equivalent to telling the bit the wood surface is
        # ``surface_clearance_mm`` below where it actually is.
        if surface_clearance_mm:
            zs = zs - surface_clearance_mm

        # Filter out trailing all-surface points so each chain starts
        # where there's something to cut. (If every point is at z=0,
        # the chain is a no-op.)
        if not (zs < -1e-6).any():
            continue

        coords = [
            (float(x + cx_world), float(y + cy_world), float(z))
            for x, y, z in zip(xs, ys, zs)
        ]

        # Lightly simplify to drop pixel-stepped collinear runs. Keep
        # the simplification tolerance small enough that the Z-varying
        # profile isn't quantised — Douglas-Peucker on (x, y) preserves
        # the per-vertex Z because we drop based on XY collinearity.
        try:
            xy_only = LineString([(c[0], c[1]) for c in coords])
            simplified_xy = xy_only.simplify(
                resolution_mm * 0.5, preserve_topology=False,
            )
            if simplified_xy.is_empty or simplified_xy.geom_type != "LineString":
                pass  # keep raw coords
            else:
                # Map simplified XY back to (x, y, z) by snapping each
                # simplified vertex to the nearest original vertex (and
                # taking that vertex's Z). This preserves the V-carve
                # depth profile through the simplification.
                from scipy.spatial import cKDTree
                orig_xy = np.array([(c[0], c[1]) for c in coords])
                tree = cKDTree(orig_xy)
                simp_xy = np.array(list(simplified_xy.coords))
                _, idxs = tree.query(simp_xy)
                coords = [
                    (float(simp_xy[i][0]), float(simp_xy[i][1]), float(coords[idxs[i]][2]))
                    for i in range(len(simp_xy))
                ]
        except Exception:
            pass  # keep raw coords if scipy or shapely chokes

        out.append(coords)

    return out


# ----- Backwards-compat shim ------------------------------------------------
# Older pipeline code calls ``compute_vbit_wall_chains`` with a different
# signature. Map it to the new ``compute_vbit_inlay_chains`` so callers
# can migrate incrementally without an immediate API break.

def compute_vbit_wall_chains(
    geom: Polygon | MultiPolygon,
    angle_deg: float,
    depth_mm: float,
    pass_depth_mm: float = 0.0,
    prev_bit_diameter_mm: float = 0.0,  # unused — kept for signature compat
    is_plug: bool = False,               # unused — kept for signature compat
    shank_diameter_mm: float = 0.0,
    pocket_allowance_mm: float = 0.0,
    surface_clearance_mm: float = 0.0,
) -> list[Chain]:
    """Backward-compatible wrapper that forwards to compute_vbit_inlay_chains.

    The legacy ``prev_bit_diameter_mm`` and ``is_plug`` parameters are
    accepted but ignored — the medial-axis algorithm doesn't need them.
    Callers should migrate to ``compute_vbit_inlay_chains`` directly.
    """
    _ = prev_bit_diameter_mm, is_plug  # silence "unused" without import
    return compute_vbit_inlay_chains(
        geom,
        angle_deg=angle_deg,
        flat_depth_mm=depth_mm,
        pass_depth_mm=pass_depth_mm,
        pocket_allowance_mm=pocket_allowance_mm,
        surface_clearance_mm=surface_clearance_mm,
        shank_diameter_mm=shank_diameter_mm,
    )
