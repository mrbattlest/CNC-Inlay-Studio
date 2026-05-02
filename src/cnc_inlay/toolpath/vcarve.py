"""Convert a medial-axis skeleton to a list of 3D V-carve toolpath chains."""
from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import convolve

from cnc_inlay.core.job import Bit
from cnc_inlay.geometry.medial_axis import Skeleton
from cnc_inlay.geometry.shape import RasterTransform

# Chain = list of (x_mm, y_mm, z_mm) points. z is negative for cuts (below stock).
Chain = list[tuple[float, float, float]]


_NEIGHBORS = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
]


def _compute_degree(skeleton: np.ndarray) -> np.ndarray:
    kernel = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=int)
    deg = convolve(skeleton.astype(int), kernel, mode="constant", cval=0)
    return deg * skeleton


def extract_chains_px(skeleton: np.ndarray) -> list[list[tuple[int, int]]]:
    """Extract polyline chains from a binary skeleton.

    Each returned chain is a list of (row, col) pixel coordinates walked along
    the skeleton. Branches are split into separate chains; isolated pixels
    become single-point chains (skipped by the caller if undesired).
    """
    h, w = skeleton.shape
    visited = np.zeros_like(skeleton, dtype=bool)
    degree = _compute_degree(skeleton)

    # Visit endpoints first so we maximise contiguous walks.
    rows, cols = np.where(skeleton)
    pixels = list(zip(rows.tolist(), cols.tolist()))
    pixels.sort(key=lambda rc: (degree[rc] != 1, -degree[rc]))

    chains: list[list[tuple[int, int]]] = []

    for start in pixels:
        if visited[start]:
            continue
        chain: list[tuple[int, int]] = []
        r, c = start
        while True:
            chain.append((r, c))
            visited[r, c] = True
            best = None
            best_deg = 99
            for dr, dc in _NEIGHBORS:
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w and skeleton[nr, nc] and not visited[nr, nc]:
                    d = degree[nr, nc]
                    # Prefer continuations (degree-2) over branches (degree>=3) and endpoints (degree-1).
                    score = abs(d - 2)
                    if score < best_deg:
                        best_deg = score
                        best = (nr, nc)
            if best is None:
                break
            r, c = best
        if len(chain) >= 2:
            chains.append(chain)
    return chains


def skeleton_to_chains(
    skel: Skeleton,
    transform: RasterTransform,
    bit: Bit,
    max_depth_mm: float,
    extra_z_mm: float = 0.0,
) -> list[Chain]:
    """Project skeleton pixels into 3D toolpath chains.

    Args:
        skel: Skeleton (mask + pixel-distance transform).
        transform: maps pixel coords back to mm.
        bit: V-bit; only `angle_deg` is used here.
        max_depth_mm: clamp |Z| to at most this value (positive number).
        extra_z_mm: additional depth added to every cut Z (used for plug glue gap).
            Positive number; applied as deeper cut.

    Returns:
        List of chains. Each chain is a list of (X_mm, Y_mm, Z_mm) tuples,
        Z negative for cuts.
    """
    if bit.angle_deg <= 0 or bit.angle_deg >= 180:
        raise ValueError(f"V-bit angle must be in (0, 180), got {bit.angle_deg}")

    half_angle = math.radians(bit.angle_deg / 2)
    # cot(half_angle) = depth-per-radius
    depth_per_radius = 1.0 / math.tan(half_angle)

    px_chains = extract_chains_px(skel.skeleton)
    chains: list[Chain] = []

    for px_chain in px_chains:
        rows = np.array([rc[0] for rc in px_chain])
        cols = np.array([rc[1] for rc in px_chain])
        xs, ys = transform.pixel_to_mm(rows, cols)
        radii_mm = skel.distance_px[rows, cols] * transform.resolution_mm
        depths = np.minimum(radii_mm * depth_per_radius, max_depth_mm) + extra_z_mm
        zs = -depths  # cuts are negative
        chain = list(zip(xs.tolist(), ys.tolist(), zs.tolist()))
        chains.append(chain)

    return chains
