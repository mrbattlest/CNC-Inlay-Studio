"""Bit-saving cutting-parameter advisor.

The single most common reason a small endmill snaps in hardwood is *chip
load* — the thickness of material removed per cutting edge per revolution —
landing outside the safe range. Too high overstresses the carbide; too low
makes the bit rub instead of cut, generating heat that anneals the edge.

This module:
1. Computes the current chip load given the user's bit, feed, and RPM.
2. Looks up the safe chip-load range for that bit diameter, scaled by the
   wood's Janka hardness.
3. Returns a verdict + actionable feed-rate range so the UI can either
   surface it as advice (after Preview) or block a real cut (before Run).

These are guidelines, not laws. Real cuts vary with bit sharpness, runout,
grain direction, and dust extraction — none of which the software can see.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cnc_inlay.core.job import Bit, InlayParams, Material


# Base safe chip-load range by bit diameter, in mm/tooth. Tuned for hobby-CNC
# carbide bits in wood; the upper end leaves some safety margin from the
# absolute breakage edge so a slightly-dull bit or a knot in the wood doesn't
# instantly snap things.
_BASE_CHIP_LOAD_MM = [
    # (max_diameter_mm, low, high)
    (1.0, 0.005, 0.012),
    (2.0, 0.010, 0.025),
    (3.0, 0.020, 0.040),
    (6.0, 0.040, 0.080),
    (float("inf"), 0.075, 0.150),
]


def _hardness_factor(janka_lbf: float) -> float:
    """Scale chip load by wood hardness. Harder woods = lower chip load."""
    if janka_lbf <= 0:
        return 1.0  # unknown — be neutral
    if janka_lbf < 800:           # very soft (redwood, pine, basswood)
        return 1.00
    if janka_lbf < 1300:          # medium (cherry, walnut, mahogany)
        return 0.85
    if janka_lbf < 1800:          # hard (maple, oak, padauk)
        return 0.70
    return 0.55                   # very hard (purpleheart, ipe, jatoba)


def safe_chip_load_range_mm(
    bit_diameter_mm: float, janka_lbf: float
) -> tuple[float, float]:
    """Return (low, high) safe chip load in mm/tooth for this bit + wood."""
    base_lo, base_hi = next(
        (lo, hi) for max_d, lo, hi in _BASE_CHIP_LOAD_MM if bit_diameter_mm < max_d
    )
    factor = _hardness_factor(janka_lbf)
    return base_lo * factor, base_hi * factor


def safe_pass_depth_mm(bit_diameter_mm: float, janka_lbf: float) -> float:
    """Maximum recommended depth-of-cut per pass for this bit + wood."""
    if janka_lbf <= 0 or janka_lbf < 1000:
        return bit_diameter_mm * 1.0
    if janka_lbf < 1500:
        return bit_diameter_mm * 0.75
    return bit_diameter_mm * 0.50


def safe_rpm_range(bit_diameter_mm: float, max_rpm: int) -> tuple[int, int]:
    """Return (low, high) recommended spindle RPM for this bit.

    Hobby spindles like the Lunyee 500W don't have torque to spare at very
    low RPM, and small bits chatter badly at the absolute top. The window
    below stays inside the productive range for the bit size.
    """
    if bit_diameter_mm < 1.5:
        # Small bits like consistent moderate speed; very high RPM heats them.
        lo, hi = 8000, 12000
    elif bit_diameter_mm < 3.0:
        lo, hi = 8000, 14000
    else:
        lo, hi = 10000, 16000
    return min(lo, max_rpm), min(hi, max_rpm)


@dataclass
class CuttingAdvisory:
    """Per-job advisory for the chosen bit + wood + speeds.

    ``severity`` is the highest level of any individual issue:
      - "ok"   — every parameter inside the safe range
      - "warn" — outside but not breakage-imminent (e.g. 25% over feed limit)
      - "block"— breakage-likely (e.g. 50%+ over, RPM/feed badly off)
    """
    severity: Literal["ok", "warn", "block"] = "ok"
    issues: list[str] = None
    chip_load_mm: float = 0.0
    chip_load_safe_lo: float = 0.0
    chip_load_safe_hi: float = 0.0
    feed_safe_lo_mm_min: float = 0.0
    feed_safe_hi_mm_min: float = 0.0
    plunge_safe_max_mm_min: float = 0.0
    pass_depth_safe_max_mm: float = 0.0
    rpm_safe_lo: int = 0
    rpm_safe_hi: int = 0

    def __post_init__(self) -> None:
        if self.issues is None:
            self.issues = []


def advise(
    bit: Bit, pocket_material: Material, params: InlayParams
) -> CuttingAdvisory:
    """Run all of the safety checks and return a structured advisory.

    Pocket material drives the hardness-side calculation — the pocket cut is
    almost always the longer / more punishing of the two sides. Plug
    material is checked separately by the UI when running the plug side.
    """
    diam = bit.diameter_mm
    flutes = max(1, bit.flutes)
    janka = pocket_material.janka_lbf

    # Resolve effective speeds (overrides win over material defaults).
    rpm = params.spindle_rpm_override or pocket_material.rpm
    feed = params.feed_override_mm_min or pocket_material.feed_mm_min
    plunge = params.plunge_override_mm_min or pocket_material.plunge_mm_min
    pass_depth = params.pass_depth_mm

    # Safe ranges.
    cl_lo, cl_hi = safe_chip_load_range_mm(diam, janka)
    feed_lo = cl_lo * rpm * flutes
    feed_hi = cl_hi * rpm * flutes
    plunge_max = feed * 0.50  # plunge should be ≤ 50% of feed for endmills
    pass_max = safe_pass_depth_mm(diam, janka)
    rpm_lo, rpm_hi = safe_rpm_range(diam, bit.max_rpm)

    # Current chip load (the load the bit will actually see).
    chip_load = feed / max(1, rpm * flutes)

    issues: list[str] = []
    severity = "ok"

    def bump(level: str) -> None:
        nonlocal severity
        order = {"ok": 0, "warn": 1, "block": 2}
        if order[level] > order[severity]:
            severity = level

    # 1. Feed / chip load — the breakage-driver.
    if chip_load > cl_hi:
        ratio = chip_load / cl_hi
        level = "block" if ratio >= 1.4 else "warn"
        issues.append(
            f"⚠ Feed {feed:.0f} mm/min is too aggressive for a "
            f"{diam:.1f} mm bit in {pocket_material.name} (Janka {int(janka)}). "
            f"Chip load is {chip_load:.4f} mm/tooth — {ratio*100-100:.0f}% over the "
            f"{cl_hi:.4f} safe ceiling. Slow feed to {feed_lo:.0f}–{feed_hi:.0f} mm/min "
            f"to keep the bit alive."
        )
        bump(level)
    elif chip_load < cl_lo and cl_lo > 0:
        # Too slow → bit rubs → heat → premature dulling
        issues.append(
            f"Feed {feed:.0f} mm/min is too slow for this bit/wood combo. "
            f"Chip load is {chip_load:.4f} mm/tooth (below {cl_lo:.4f}). "
            f"The bit will rub and overheat instead of cutting cleanly. "
            f"Consider raising feed to {feed_lo:.0f}–{feed_hi:.0f} mm/min "
            f"or lowering RPM."
        )
        bump("warn")

    # 2. Plunge rate — straight-down motion stresses the tip more than feed does.
    if plunge > plunge_max:
        ratio = plunge / plunge_max
        level = "block" if ratio >= 1.5 else "warn"
        issues.append(
            f"Plunge {plunge:.0f} mm/min is too fast — should be ≤ "
            f"{plunge_max:.0f} mm/min (50% of feed). Plunging too fast snaps "
            f"small bit tips."
        )
        bump(level)

    # 3. Pass depth — taking too much per pass overloads the flutes.
    if pass_depth > pass_max:
        ratio = pass_depth / pass_max
        level = "block" if ratio >= 1.3 else "warn"
        issues.append(
            f"Pass depth {pass_depth:.2f} mm exceeds the safe maximum of "
            f"{pass_max:.2f} mm for a {diam:.1f} mm bit in {pocket_material.name}. "
            f"Each pass should remove no more than this — break the cut into "
            f"multiple shallower passes."
        )
        bump(level)

    # 4. Spindle RPM out-of-window.
    if rpm < rpm_lo:
        issues.append(
            f"Spindle RPM {rpm} is below the recommended {rpm_lo}–{rpm_hi} for a "
            f"{diam:.1f} mm bit. Low RPM means the bit can't clear chips fast "
            f"enough; chips re-cut and heat the bit."
        )
        bump("warn")
    elif rpm > rpm_hi:
        issues.append(
            f"Spindle RPM {rpm} is above the recommended {rpm_lo}–{rpm_hi} for a "
            f"{diam:.1f} mm bit. Too-high RPM with too-low feed creates "
            f"friction-only contact and heats the bit."
        )
        bump("warn")

    return CuttingAdvisory(
        severity=severity,
        issues=issues,
        chip_load_mm=chip_load,
        chip_load_safe_lo=cl_lo,
        chip_load_safe_hi=cl_hi,
        feed_safe_lo_mm_min=feed_lo,
        feed_safe_hi_mm_min=feed_hi,
        plunge_safe_max_mm_min=plunge_max,
        pass_depth_safe_max_mm=pass_max,
        rpm_safe_lo=rpm_lo,
        rpm_safe_hi=rpm_hi,
    )
