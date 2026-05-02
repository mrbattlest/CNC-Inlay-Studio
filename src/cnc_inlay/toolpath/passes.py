"""Split deep V-carve cuts into multiple progressive passes."""
from __future__ import annotations

import math

from cnc_inlay.toolpath.vcarve import Chain


def split_into_passes(chains: list[Chain], pass_depth_mm: float) -> list[Chain]:
    """Replace each chain with a sequence of progressively deeper passes.

    Z values in chains are negative (cuts). For a pass that goes to depth `D`,
    each point's Z is clamped to `max(orig_z, -D)` — so pass 1 only cuts the
    top `pass_depth_mm`, pass 2 cuts down to `2 * pass_depth_mm`, etc.

    The deepest pass uses each point's true Z, so the final cut is exact.
    """
    if pass_depth_mm <= 0:
        return chains

    output: list[Chain] = []
    for chain in chains:
        deepest = max(-z for _, _, z in chain)  # positive number
        if deepest <= pass_depth_mm + 1e-9:
            output.append(chain)
            continue
        n_passes = max(1, math.ceil(deepest / pass_depth_mm))
        for i in range(1, n_passes + 1):
            ceiling = pass_depth_mm * i  # positive
            if i == n_passes:
                # Final pass: use true depths.
                output.append(chain)
            else:
                clipped = [(x, y, max(z, -ceiling)) for x, y, z in chain]
                output.append(clipped)
    return output
