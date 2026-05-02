"""Place chevron-direction markers along toolpath chains.

Both 2D and 3D canvases use this helper so the spacing/midpoint logic lives
in one place. Returns a list of (position, direction) tuples; the canvas is
responsible for drawing the actual chevron geometry in its native API.
"""
from __future__ import annotations

import math
from typing import Sequence

from cnc_inlay.toolpath.vcarve import Chain


def _segment_length(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _chain_length(chain: Chain) -> float:
    return sum(_segment_length(a, b) for a, b in zip(chain[:-1], chain[1:]))


def _interpolate(
    chain: Chain, target_distance: float
) -> tuple[tuple[float, float, float], tuple[float, float]] | None:
    """Walk the chain until ``target_distance`` is reached. Return
    (point, direction_xy_unit) or None if the chain is shorter than target.
    """
    if len(chain) < 2:
        return None
    remaining = target_distance
    for a, b in zip(chain[:-1], chain[1:]):
        seg_len = _segment_length(a, b)
        if seg_len <= 0:
            continue
        if remaining <= seg_len:
            t = remaining / seg_len
            x = a[0] + t * (b[0] - a[0])
            y = a[1] + t * (b[1] - a[1])
            z = a[2] + t * (b[2] - a[2])
            dx = (b[0] - a[0]) / seg_len
            dy = (b[1] - a[1]) / seg_len
            return ((x, y, z), (dx, dy))
        remaining -= seg_len
    # Fallthrough: target past chain end — use last segment direction at endpoint.
    a, b = chain[-2], chain[-1]
    seg_len = _segment_length(a, b)
    if seg_len <= 0:
        return None
    return (b, ((b[0] - a[0]) / seg_len, (b[1] - a[1]) / seg_len))


def chevron_markers(
    chain: Chain,
    spacing_mm: float = 15.0,
    min_chain_length_mm: float = 4.0,
) -> list[tuple[tuple[float, float, float], tuple[float, float]]]:
    """Pick chevron positions + directions along ``chain``.

    Always emits at least one marker at the chain's midpoint (if the chain is
    long enough), then additional markers every ``spacing_mm`` thereafter.
    Returns an empty list for trivially short chains.
    """
    total = _chain_length(chain)
    if total < min_chain_length_mm or len(chain) < 2:
        return []
    markers: list[tuple[tuple[float, float, float], tuple[float, float]]] = []
    # Anchor at the chain midpoint.
    mid_pt = _interpolate(chain, total / 2.0)
    if mid_pt is not None:
        markers.append(mid_pt)
    # Add additional markers stepping out from the midpoint in both directions.
    if spacing_mm > 0:
        d = total / 2.0 + spacing_mm
        while d < total:
            pt = _interpolate(chain, d)
            if pt is not None:
                markers.append(pt)
            d += spacing_mm
        d = total / 2.0 - spacing_mm
        while d > 0:
            pt = _interpolate(chain, d)
            if pt is not None:
                markers.append(pt)
            d -= spacing_mm
    return markers


def chevron_polyline_2d(
    point: tuple[float, float, float],
    direction: tuple[float, float],
    size_mm: float = 1.5,
) -> list[tuple[float, float]]:
    """Three-point polyline approximating a `>` chevron pointing in ``direction``.

    Caller draws this as a stroke in the canvas's native pen API.
    """
    px, py, _ = point
    dx, dy = direction
    # Perpendicular to the direction (90° CCW).
    nx, ny = -dy, dx
    tip = (px + dx * size_mm, py + dy * size_mm)
    tail_left = (px - dx * size_mm * 0.4 + nx * size_mm * 0.7,
                 py - dy * size_mm * 0.4 + ny * size_mm * 0.7)
    tail_right = (px - dx * size_mm * 0.4 - nx * size_mm * 0.7,
                  py - dy * size_mm * 0.4 - ny * size_mm * 0.7)
    return [tail_left, tip, tail_right]


def chevron_polyline_3d(
    point: tuple[float, float, float],
    direction: tuple[float, float],
    size_mm: float = 1.5,
) -> list[tuple[float, float, float]]:
    """3D chevron polyline at the chain's local Z."""
    flat = chevron_polyline_2d(point, direction, size_mm)
    z = point[2]
    return [(x, y, z) for x, y in flat]
