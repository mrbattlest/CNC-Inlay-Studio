"""Order toolpath chains to minimise air-move travel between them, plus a
post-pass that merges adjacent closed-loop rings at the same depth so the bit
cuts continuously through concentric pocket-clearance rings without lifting.
"""
from __future__ import annotations

import math

from cnc_inlay.toolpath.vcarve import Chain


def _dist2(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def _tour_length(chains: list[Chain], start: tuple[float, float] = (0.0, 0.0)) -> float:
    total = 0.0
    prev = (start[0], start[1], 0.0)
    for c in chains:
        total += math.sqrt(_dist2(prev, c[0]))
        prev = c[-1]
    return total


def _reverse(chain: Chain) -> Chain:
    return list(reversed(chain))


def greedy_nn_order(
    chains: list[Chain], start: tuple[float, float] = (0.0, 0.0)
) -> list[Chain]:
    """Greedy nearest-neighbour ordering. Each chain is taken in whichever
    orientation puts its closer endpoint nearest to the previous endpoint.
    """
    if not chains:
        return []
    remaining = list(chains)
    ordered: list[Chain] = []
    cursor = (start[0], start[1], 0.0)
    while remaining:
        best_idx = 0
        best_reverse = False
        best_d = math.inf
        for i, c in enumerate(remaining):
            d_start = _dist2(cursor, c[0])
            d_end = _dist2(cursor, c[-1])
            if d_start < best_d:
                best_d = d_start
                best_idx = i
                best_reverse = False
            if d_end < best_d:
                best_d = d_end
                best_idx = i
                best_reverse = True
        c = remaining.pop(best_idx)
        if best_reverse:
            c = _reverse(c)
        ordered.append(c)
        cursor = c[-1]
    return ordered


def two_opt(
    chains: list[Chain],
    start: tuple[float, float] = (0.0, 0.0),
    max_iterations: int = 50,
) -> list[Chain]:
    """Light 2-opt over chain ordering. Only swaps whole chains (with optional
    reversal) — does not split chains. Bounded iteration count keeps it cheap.
    """
    if len(chains) < 3:
        return chains

    chains = list(chains)
    best_len = _tour_length(chains, start)
    improved = True
    iters = 0
    while improved and iters < max_iterations:
        improved = False
        iters += 1
        for i in range(len(chains) - 1):
            for j in range(i + 1, len(chains)):
                # Reverse the slice [i..j], also flipping each chain's direction
                # so that endpoints align in the new order.
                new_chains = (
                    chains[:i]
                    + [_reverse(c) for c in reversed(chains[i : j + 1])]
                    + chains[j + 1 :]
                )
                new_len = _tour_length(new_chains, start)
                if new_len + 1e-6 < best_len:
                    chains = new_chains
                    best_len = new_len
                    improved = True
                    break
            if improved:
                break
    return chains


def order_chains(
    chains: list[Chain], start: tuple[float, float] = (0.0, 0.0)
) -> list[Chain]:
    """Greedy NN then a bounded 2-opt polish."""
    return two_opt(greedy_nn_order(chains, start), start)


def total_cut_length_mm(chains: list[Chain]) -> float:
    total = 0.0
    for chain in chains:
        for a, b in zip(chain[:-1], chain[1:]):
            dx = b[0] - a[0]
            dy = b[1] - a[1]
            dz = b[2] - a[2]
            total += math.sqrt(dx * dx + dy * dy + dz * dz)
    return total


def total_air_length_mm(
    chains: list[Chain], start: tuple[float, float] = (0.0, 0.0)
) -> float:
    if not chains:
        return 0.0
    total = math.sqrt(_dist2((start[0], start[1], 0.0), chains[0][0]))
    for a, b in zip(chains[:-1], chains[1:]):
        total += math.sqrt(_dist2(a[-1], b[0]))
    return total


# ----------------------------------------------------------------- ring merging


def _xy_dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _is_closed_loop(chain: Chain, tol_mm: float = 0.01) -> bool:
    return len(chain) >= 3 and _xy_dist(chain[0], chain[-1]) < tol_mm


def _rotate_loop(chain: Chain, start_index: int) -> Chain:
    """Rotate a closed-loop chain so it starts at ``start_index``, preserving closure."""
    if not _is_closed_loop(chain):
        return list(chain)
    inner = list(chain[:-1])  # drop closing duplicate
    n = len(inner)
    if n == 0:
        return list(chain)
    start_index %= n
    rotated = inner[start_index:] + inner[:start_index]
    rotated.append(rotated[0])
    return rotated


def _closest_index(chain: Chain, target: tuple[float, float, float]) -> tuple[int, float]:
    best_i = 0
    best_d = float("inf")
    for i, p in enumerate(chain):
        d = _xy_dist(p, target)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i, best_d


def merge_close_loops(
    chains: list[Chain],
    max_jump_mm: float,
    inside_region=None,
) -> list[Chain]:
    """Merge consecutive chains that are close enough to connect at depth.

    For closed-loop chains, the next chain is rotated so its start point is
    closest to the previous chain's end — making the connecting cut as short
    as possible. The merge happens only when:
      * both chains are at the same Z, and
      * the closest-point gap is within ``max_jump_mm``, and
      * (when ``inside_region`` is provided) the straight connector segment
        lies entirely within that region.

    The ``inside_region`` guard exists because the merge produces a literal
    G1 cut at depth between the two endpoints. On the pocket side that's
    safe — adjacent rings sit within bit-diameter of each other and the
    intervening material is already cleared. On the plug side, however,
    the cleared region (the complement) is **interrupted by the design
    feature itself** (the JMU letters, dachshund silhouettes, etc.). Two
    rings on opposite sides of a letter can be within ``max_jump_mm`` of
    each other and the merge would draw a G1 line straight through the
    letter — destroying the inlay. Pass ``inside_region=region`` (the
    same polygon you handed to the rest-machining run) to refuse those
    cross-feature connectors.

    Open chains are merged only if the endpoint-to-startpoint gap is small
    (and likewise inside the region when supplied).
    """
    if not chains:
        return []
    from shapely.geometry import LineString
    # Cap on segment-validation cost: a tight buffer keeps the
    # ``covers`` check from rejecting connectors that hug the cleared
    # region's boundary by sub-µm tolerance from shapely's coordinate
    # rounding.
    region_for_check = None
    if inside_region is not None and not inside_region.is_empty:
        try:
            region_for_check = inside_region.buffer(0.01)
        except Exception:
            region_for_check = inside_region

    def _connector_safe(a_xyz, b_xyz) -> bool:
        if region_for_check is None:
            return True
        try:
            seg = LineString([(a_xyz[0], a_xyz[1]), (b_xyz[0], b_xyz[1])])
            # `covers` includes boundary, which is exactly what we want
            # for a chain endpoint that touches the region edge.
            return region_for_check.covers(seg)
        except Exception:
            # If shapely chokes (rare but possible on degenerate
            # geometry), refuse the merge — the cost of an extra
            # lift+rapid is far less than a destroyed cut.
            return False

    merged: list[list[tuple[float, float, float]]] = [list(chains[0])]
    for nxt in chains[1:]:
        prev = merged[-1]
        if not prev or not nxt:
            merged.append(list(nxt))
            continue
        # Require same depth — the connecting cut happens at flat Z.
        if abs(prev[-1][2] - nxt[0][2]) > 0.01:
            merged.append(list(nxt))
            continue
        if _is_closed_loop(nxt):
            idx, dist = _closest_index(nxt[:-1], prev[-1])
            if dist > max_jump_mm:
                merged.append(list(nxt))
                continue
            connector_target = nxt[idx]
            if not _connector_safe(prev[-1], connector_target):
                merged.append(list(nxt))
                continue
            prev.extend(_rotate_loop(nxt, idx))
        else:
            if _xy_dist(prev[-1], nxt[0]) > max_jump_mm:
                merged.append(list(nxt))
                continue
            if not _connector_safe(prev[-1], nxt[0]):
                merged.append(list(nxt))
                continue
            prev.extend(nxt)
    return merged
