"""Compute the medial axis (skeleton) and distance transform of a binary mask.

Raster-based skeletonization via skimage produces three artefacts that hurt
toolpath quality:

1. **Spurs** — short branches at every minor boundary irregularity. We prune
   any branch whose length is smaller than ``spur_factor × max_distance_along_branch``.
2. **Diagonal-pixel zigzag** — the skeleton walks the 8-connected pixel grid,
   producing visible kinks every 1–2 px. The chain extractor (downstream)
   simplifies polylines via Douglas-Peucker after pixel→mm conversion.
3. **Edge-bias underestimate** — the Euclidean distance transform from a
   rasterised boundary is biased low by ~0.5 px because the "edge" sits
   between pixels. We add a ``+0.5`` correction to all distance values so the
   V-carve depth reaches the true geometric boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import convolve
from skimage.morphology import medial_axis

_NEIGHBOR_KERNEL = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=int)


@dataclass(frozen=True)
class Skeleton:
    skeleton: np.ndarray  # bool array, True on medial axis
    distance_px: np.ndarray  # full distance transform (foreground → boundary), with edge-bias correction

    @property
    def skeleton_distance_px(self) -> np.ndarray:
        """Distance values restricted to skeleton pixels (0 elsewhere)."""
        return self.distance_px * self.skeleton


def _prune_spurs(skeleton: np.ndarray, distance_px: np.ndarray, spur_factor: float = 2.0) -> np.ndarray:
    """Remove short branches (spurs) from a binary skeleton.

    A branch is a path from an endpoint (degree 1) to a junction (degree ≥ 3).
    If the branch length in pixels is < ``spur_factor × max(distance_along_branch)``,
    drop it. This preserves real medial-axis branches while killing artefacts
    along noisy boundaries.
    """
    sk = skeleton.copy()
    h, w = sk.shape
    NEIGHBORS = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    ]

    for _ in range(8):  # iterate a few times — pruning may expose new spurs
        deg = convolve(sk.astype(int), _NEIGHBOR_KERNEL, mode="constant", cval=0) * sk
        endpoints = list(zip(*np.where(deg == 1)))
        if not endpoints:
            break
        removed_any = False
        for r, c in endpoints:
            if not sk[r, c]:
                continue
            # Walk from this endpoint until we hit a branch point (deg >= 3).
            path = [(r, c)]
            max_d = float(distance_px[r, c])
            cr, cc = r, c
            prev = None
            while True:
                if deg[cr, cc] >= 3:
                    # Stop AT the branch point — don't include it in the path.
                    path.pop()
                    break
                next_pt = None
                for dr, dc in NEIGHBORS:
                    nr, nc = cr + dr, cc + dc
                    if (
                        0 <= nr < h and 0 <= nc < w and sk[nr, nc] and (nr, nc) != prev
                    ):
                        if next_pt is None:
                            next_pt = (nr, nc)
                        else:
                            # Two unvisited neighbours → we've reached a branch.
                            next_pt = None
                            break
                if next_pt is None:
                    break
                path.append(next_pt)
                max_d = max(max_d, float(distance_px[next_pt]))
                prev = (cr, cc)
                cr, cc = next_pt
                if len(path) > 5000:  # safety
                    break
            length = len(path)
            if length > 0 and length < max(2.0, spur_factor * max_d):
                for pr, pc in path:
                    sk[pr, pc] = False
                removed_any = True
        if not removed_any:
            break

    return sk


def compute_skeleton(mask: np.ndarray, prune_spurs: bool = True) -> Skeleton:
    """Compute medial axis + Euclidean distance transform with quality fixes."""
    skeleton, distance = medial_axis(mask, return_distance=True)
    distance = distance.astype(np.float64)
    # Edge-bias correction: rasterised distance is ~0.5 px short of geometric.
    distance = np.where(distance > 0, distance + 0.5, 0.0)
    skeleton = skeleton.astype(bool)
    if prune_spurs:
        skeleton = _prune_spurs(skeleton, distance)
    return Skeleton(skeleton=skeleton, distance_px=distance)

