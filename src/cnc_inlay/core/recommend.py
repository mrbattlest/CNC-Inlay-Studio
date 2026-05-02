"""Recommend a V-bit and clearance endmill for a given design."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cnc_inlay.core.job import Bit
from cnc_inlay.geometry.medial_axis import Skeleton


@dataclass(frozen=True)
class Recommendation:
    vbit: Bit | None
    endmill: Bit | None
    smallest_feature_mm: float
    rationale: str


def smallest_feature_mm(skel: Skeleton, resolution_mm: float) -> float:
    """Smallest local thickness (twice the minimum positive distance-to-boundary)."""
    skel_distances = skel.distance_px[skel.skeleton]
    if skel_distances.size == 0:
        return 0.0
    positive = skel_distances[skel_distances > 0]
    if positive.size == 0:
        return float(skel_distances.min() * 2 * resolution_mm)
    return float(positive.min() * 2 * resolution_mm)


def recommend_bits(
    bits: list[Bit],
    skel: Skeleton,
    resolution_mm: float,
) -> Recommendation:
    """Pick a V-bit (by angle) and a clearance endmill (by diameter) from the library."""
    feature_mm = smallest_feature_mm(skel, resolution_mm)

    vbits = [b for b in bits if b.type == "vbit"]
    endmills = [b for b in bits if b.type == "endmill"]
    if not endmills:
        raise ValueError("No endmills available in the bit library")

    vbits_sorted = sorted(vbits, key=lambda b: b.angle_deg)
    endmills_sorted = sorted(endmills, key=lambda b: b.diameter_mm)

    # V-bit recommendation is optional — only emit if a V-bit library exists.
    vbit: Bit | None = None
    v_why = ""
    if vbits_sorted:
        if feature_mm < 0.8:
            vbit = vbits_sorted[0]
        elif feature_mm < 4.0:
            vbit = min(vbits_sorted, key=lambda b: abs(b.angle_deg - 60))
        else:
            vbit = vbits_sorted[-1]
        v_why = f"smallest feature is {feature_mm:.2f} mm — {vbit.name} suggested. "

    # Endmill: largest bit that fits in the thinnest region with at least
    # ~3 concentric clearing rings. Heuristic: diameter ≤ 0.6 × smallest feature.
    max_diameter = max(0.5, feature_mm * 0.6)
    candidates = [e for e in endmills_sorted if e.diameter_mm <= max_diameter]
    endmill = candidates[-1] if candidates else endmills_sorted[0]
    e_why = (
        f"Recommended endmill: {endmill.name} (Ø {endmill.diameter_mm:.2f} mm) — "
        f"largest that fits the {feature_mm:.2f} mm thinnest feature with multi-ring clearing."
    )

    return Recommendation(
        vbit=vbit,
        endmill=endmill,
        smallest_feature_mm=feature_mm,
        rationale=v_why + e_why,
    )
