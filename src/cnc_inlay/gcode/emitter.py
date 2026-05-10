"""Render a sequence of toolpath sections (one per bit) into GRBL G-code.

Each ``Section`` represents a contiguous block of cuts performed with one bit.
Between sections we emit a tool-change block (`M5` → safe Z → comment → `M0`)
so the user can swap bits manually before resuming.
"""
from __future__ import annotations

from dataclasses import dataclass

from cnc_inlay.core.job import Bit, InlayParams, Material
from cnc_inlay.core.units import MM_PER_INCH, Units, feed_to_display
from cnc_inlay.gcode.header import comment, footer, header
from cnc_inlay.toolpath.vcarve import Chain


@dataclass
class Section:
    bit: Bit
    material: Material
    chains: list[Chain]
    label: str  # human-readable description (e.g. "V-carve", "Endmill clearance + boundary")


def _fmt(value: float) -> str:
    s = f"{value:.4f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
        if not s or s == "-":
            s = "0"
    return s


def _emit_section_body(section: Section, params: InlayParams) -> list[str]:
    """Render one bit's section. Coordinates are converted to the chosen unit
    (mm for G21, inches for G20) at emission time. Internal chain data is mm.

    Position-tracking optimisation: when consecutive chains start at the
    same XY (typical of multi-pass progressively deeper passes — every
    pass shares the same closed loop), we skip the lift-to-safe-Z +
    rapid-back-to-XY + plunge-from-safe-Z sequence and just plunge
    directly from the current depth to the next pass depth. On a 5-pass
    cut into 3.75 mm of wood, this saves ~5 × (lift + rapid + plunge ≈
    11 mm of travel) per chain — significant cumulative time saving.
    The lift-then-traverse path is preserved any time XY actually
    changes, so the bit never moves laterally through uncut wood.
    """
    lines: list[str] = []
    units = params.gcode_units
    inch = units == Units.IMPERIAL
    scale = 1.0 / MM_PER_INCH if inch else 1.0
    safe_z = params.safe_z_mm * scale
    feed = feed_to_display(section.material.feed_mm_min, units)
    plunge = feed_to_display(section.material.plunge_mm_min, units)

    lines.append(comment(f"--- {section.label} : {section.bit.name} ---"))
    lines.append(f"M3 S{section.material.rpm}")
    # Spindle ramp-up dwell. Without this, the very next move (rapid +
    # plunge) starts before the spindle reaches commanded RPM. A 1 mm
    # bit hitting wood at half-speed has snapped users' bits — the bit's
    # tip can't shed chips fast enough at low RPM, so cutting force
    # spikes. 3 s is enough for a Lunyee 500 W spindle to reach 10000
    # RPM cold-start.
    lines.append("G4 P3")
    # Unconditional lift to safe Z BEFORE any lateral motion. After a
    # tool-change M0 the operator typically re-zeroes Z by jogging the
    # new bit's tip down to the wood surface — so when the streamer
    # resumes here, the bit is sitting at work-Z=0 with the spindle
    # spinning up. Without this lift, the section's first move
    # (G0 X Y to the chain start) would drag the spinning bit
    # laterally across the wood at surface level, gouging a slanted
    # cut INTO the work as it traverses to the first chain. The
    # subsequent same-Z optimisation in the chain loop still applies
    # for adjacent chains within this section — only the very first
    # move of the section unconditionally lifts.
    lines.append(f"G0 Z{_fmt(safe_z)}")

    # State: where the bit currently is. cur_z=safe_z reflects the
    # G0 Z{safe_z} lift we just emitted above; cur_x/cur_y unknown
    # until the first rapid lands somewhere.
    cur_x: float | None = None
    cur_y: float | None = None
    cur_z: float = safe_z
    xy_tol = 0.001  # mm (post-scale; trivially tighter than any cut precision)

    for chain in section.chains:
        if not chain:
            continue
        x0_mm, y0_mm, z0_mm = chain[0]
        x0 = x0_mm * scale
        y0 = y0_mm * scale
        z0 = z0_mm * scale

        same_xy = (
            cur_x is not None
            and abs(cur_x - x0) < xy_tol
            and abs(cur_y - y0) < xy_tol
        )

        if same_xy and cur_z < safe_z - 1e-6:
            # Bit is already over the start XY and currently at depth.
            # Skip the lift+rapid+plunge — go straight from current Z
            # to the new pass Z. Safe because we haven't moved laterally
            # since clearing this spot on the previous pass.
            if abs(cur_z - z0) > 1e-6:
                lines.append(f"G1 Z{_fmt(z0)} F{plunge:g}")
        else:
            # XY changes (or first chain): lift to safe Z first if we're
            # at depth, then rapid laterally, then plunge.
            if cur_z < safe_z - 1e-6:
                lines.append(f"G0 Z{_fmt(safe_z)}")
                cur_z = safe_z
            if not same_xy:
                lines.append(f"G0 X{_fmt(x0)} Y{_fmt(y0)}")
            lines.append(f"G1 Z{_fmt(z0)} F{plunge:g}")

        cur_x, cur_y, cur_z = x0, y0, z0

        # Cut along the chain. First feed-rate line is full G1 with F=cut
        # feed; subsequent lines drop the F (modal) and any axis word
        # whose value didn't change.
        first_feed_emitted = False
        for x_mm, y_mm, z_mm in chain[1:]:
            xs = x_mm * scale
            ys = y_mm * scale
            zs = z_mm * scale
            if not first_feed_emitted:
                lines.append(f"G1 X{_fmt(xs)} Y{_fmt(ys)} Z{_fmt(zs)} F{feed:g}")
                first_feed_emitted = True
            else:
                lines.append(f"G1 X{_fmt(xs)} Y{_fmt(ys)} Z{_fmt(zs)}")
            cur_x, cur_y, cur_z = xs, ys, zs

    # End-of-section lift so the bit is clear before any tool change /
    # M5 / next-section transition.
    if cur_z < safe_z - 1e-6:
        lines.append(f"G0 Z{_fmt(safe_z)}")

    return lines


def emit_gcode(
    sections: list[Section],
    *,
    title: str,
    params: InlayParams,
    is_plug: bool,
    tool_change_park_xy_mm: tuple[float, float] | None = None,
) -> str:
    """Render a multi-section toolpath program to a complete GRBL G-code string.

    ``tool_change_park_xy_mm`` is the (x, y) position the bit should move to
    before each ``M0`` tool-change pause. Choosing a spot over uncut wood
    lets the operator re-zero Z against a real surface during the swap.
    Defaults to (0, 0) if not provided.
    """
    sections = [s for s in sections if any(s.chains)]
    if not sections:
        return ""

    from cnc_inlay.core.units import fmt_length

    units = params.gcode_units
    bit_summary = [
        comment(
            f"- {s.label}: {s.bit.name} [{fmt_length(s.bit.diameter_mm, units, decimals=3)}]"
        )
        for s in sections
    ]
    primary_material = sections[0].material

    lines: list[str] = header(
        title=title,
        bit_summary_lines=bit_summary,
        material=primary_material,
        params=params,
        is_plug=is_plug,
    )

    # mm → emit-unit scale, used for both park-XY and the tool-change Z lift.
    units = params.gcode_units
    scale = 1.0 / MM_PER_INCH if units == Units.IMPERIAL else 1.0
    if tool_change_park_xy_mm is not None:
        park_x = tool_change_park_xy_mm[0] * scale
        park_y = tool_change_park_xy_mm[1] * scale
    else:
        park_x = 0.0
        park_y = 0.0
    # Tool-change Z lift — much higher than safe_z so the operator can
    # remove the dust shroud and access the collet. Defaults to 60 mm.
    tc_lift = params.tool_change_z_lift_mm * scale

    for i, section in enumerate(sections):
        if i > 0:
            lines.extend(
                [
                    "M5",
                    f"G0 Z{tc_lift:.3f}",
                    # Park over a known uncut spot so the operator can re-zero
                    # Z against a real wood surface for the next bit.
                    f"G0 X{park_x:.3f} Y{park_y:.3f}",
                    comment(
                        f"TOOL CHANGE: install {section.bit.name} "
                        f"[{section.bit.diameter_mm:.2f} mm]"
                    ),
                    "M0",  # programmed pause for manual tool change
                ]
            )
        lines.extend(_emit_section_body(section, params))

    lines.extend(footer(params))
    return "\n".join(lines) + "\n"
