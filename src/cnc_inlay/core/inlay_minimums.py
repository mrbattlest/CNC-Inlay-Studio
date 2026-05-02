"""Quality-inlay minimums + a plug-fragility check.

The pocket can be cut with confidence whenever the endmill physically fits.
The *plug* — the contrasting wood that drops into the pocket — is the limit:
thin filigree-like plug features snap during glue-up, plane-down, or even
just handling. This module encodes the rule of thumb the wood-inlay
community settles on (≥ 3× bit diameter, with a 4 mm absolute floor) and
runs a morphological-opening check to find features that violate it.

The check is intentionally conservative. It refuses to lie that a fragile
plug is fine; the user can override or auto-simplify, but they have to
make that choice deliberately.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import MultiPolygon, Polygon


# Absolute floor regardless of bit size — wood grain still wants to break
# anything thinner than this in a plug.
ABSOLUTE_MIN_FEATURE_MM = 4.0


def min_feature_width_mm(endmill_diameter_mm: float) -> float:
    """Recommended minimum plug feature width for the given endmill.

    Industry rule of thumb is ≥ 3× the bit diameter — the plug needs that much
    cross-grain mass to survive being lifted out of the cut and glued in.
    """
    return max(3.0 * endmill_diameter_mm, ABSOLUTE_MIN_FEATURE_MM)


@dataclass
class FragilityReport:
    """Plug-fragility verdict for a design.

    Computed via morphological opening: erode the design by ``min_feature/2``
    then dilate back by the same amount. Anything thinner than ``min_feature``
    drops out. The lost area is what would break.
    """
    min_feature_mm: float = 0.0      # threshold actually checked against
    fragile_area_mm2: float = 0.0    # total area of features below the threshold
    total_area_mm2: float = 0.0      # area of the full design
    is_fragile: bool = False         # True if any meaningful fragile area exists

    @property
    def fragile_fraction(self) -> float:
        if self.total_area_mm2 <= 0:
            return 0.0
        return self.fragile_area_mm2 / self.total_area_mm2


def _opening(geom: Polygon | MultiPolygon, radius_mm: float) -> Polygon | MultiPolygon:
    """Morphological opening: erode by r, then dilate by r. Removes features
    narrower than 2r. ``buffer(0)`` after each step keeps shapely happy on
    inputs with self-intersections.
    """
    eroded = geom.buffer(-radius_mm).buffer(0)
    if eroded.is_empty:
        return eroded
    return eroded.buffer(radius_mm).buffer(0)


def check_plug_fragility(
    design: Polygon | MultiPolygon,
    endmill_diameter_mm: float,
    *,
    min_feature_mm: float | None = None,
    threshold_fraction: float = 0.005,
) -> FragilityReport:
    """Run the plug-fragility check on ``design``.

    A small ``threshold_fraction`` of lost area is tolerated as numerical
    noise from the buffer round-trip — only larger losses count as real
    fragility. Default 0.5% of total area.
    """
    if design.is_empty:
        return FragilityReport()
    threshold = (
        min_feature_mm if min_feature_mm is not None
        else min_feature_width_mm(endmill_diameter_mm)
    )
    radius = threshold / 2.0
    opened = _opening(design, radius)
    total = float(design.area)
    if opened.is_empty:
        # The entire design is thinner than the threshold — every feature is fragile.
        return FragilityReport(
            min_feature_mm=threshold,
            fragile_area_mm2=total,
            total_area_mm2=total,
            is_fragile=True,
        )
    lost = float(max(0.0, total - opened.area))
    is_fragile = lost > total * threshold_fraction
    return FragilityReport(
        min_feature_mm=threshold,
        fragile_area_mm2=lost,
        total_area_mm2=total,
        is_fragile=is_fragile,
    )


def auto_simplify(
    design: Polygon | MultiPolygon, endmill_diameter_mm: float
) -> Polygon | MultiPolygon:
    """Drop features narrower than the recommended minimum, returning the
    robust skeleton of the design. Equivalent to a morphological opening at
    the minimum-feature radius."""
    radius = min_feature_width_mm(endmill_diameter_mm) / 2.0
    return _opening(design, radius)
