"""GRBL-compatible G-code header and footer for multi-bit programs.

All comments are sanitised to plain ASCII with no nested parentheses, since
many G-code parsers (FreeCAD's Path post, gcode_pre, GRBL itself) treat the
first ``)`` as comment-end and reject anything else inside.
"""
from __future__ import annotations

from cnc_inlay.core.job import InlayParams, Material
from cnc_inlay.core.units import Units, fmt_feed, fmt_length


# Replacements for common non-ASCII glyphs we emit in metadata. Anything not
# covered here gets stripped to '?' by the final ASCII coercion.
_GLYPH_REPLACEMENTS = {
    "°": " deg",
    "→": "->",
    "←": "<-",
    "•": "*",
    "×": "x",
    "≤": "<=",
    "≥": ">=",
    "⚠": "!",
    "—": "-",
    "–": "-",
    '"': "in",  # bit names like 1/8"
    "“": "in",
    "”": "in",
}


def _safe_comment_text(text: str) -> str:
    out = str(text)
    for k, v in _GLYPH_REPLACEMENTS.items():
        out = out.replace(k, v)
    # Replace nested parentheses with brackets so the comment doesn't terminate early.
    out = out.replace("(", "[").replace(")", "]")
    # Drop anything still non-ASCII; CAM parsers vary in what they accept.
    return out.encode("ascii", errors="replace").decode("ascii").replace("?", "")


def comment(text: str) -> str:
    """Wrap a string as a parser-safe G-code line comment."""
    return f"( {_safe_comment_text(text).strip()} )"


def header(
    title: str,
    bit_summary_lines: list[str],
    material: Material,
    params: InlayParams,
    is_plug: bool,
) -> list[str]:
    units = params.gcode_units
    role = "plug [mirrored - flip stock face-down to assemble]" if is_plug else "pocket"
    if is_plug:
        depth_label = (
            f"{fmt_length(params.pocket_depth_mm + params.glue_gap_mm, units, decimals=3)} "
            f"[incl {fmt_length(params.glue_gap_mm, units, decimals=3)} glue gap]"
        )
    else:
        depth_label = fmt_length(params.pocket_depth_mm, units, decimals=3)
    lines = [
        comment(title),
        comment(f"role: {role}"),
    ]
    lines.extend(bit_summary_lines)
    lines.extend(
        [
            comment(f"material: {material.name}"),
            comment(f"max depth: {depth_label}"),
            comment(f"pass depth: {fmt_length(params.pass_depth_mm, units, decimals=3)}"),
            comment(f"resolution: {fmt_length(params.resolution_mm, units, decimals=3)}"),
            comment(f"origin: {params.origin.value}"),
            comment(f"units: {units.length_label}"),
        ]
    )
    overrides: list[str] = []
    if params.spindle_rpm_override is not None:
        overrides.append(f"RPM={params.spindle_rpm_override}")
    if params.feed_override_mm_min is not None:
        overrides.append(f"feed={fmt_feed(params.feed_override_mm_min, units)}")
    if params.plunge_override_mm_min is not None:
        overrides.append(f"plunge={fmt_feed(params.plunge_override_mm_min, units)}")
    if overrides:
        lines.append(comment("[overrides] " + ", ".join(overrides)))
    safe_z = params.safe_z_mm if units == Units.METRIC else params.safe_z_mm / 25.4
    lines.extend(
        [
            units.gcode_unit_command,  # G21 (mm) or G20 (in)
            "G90",  # absolute
            "G17",  # XY plane
            "G54",  # work coord 1
            f"G0 Z{safe_z:.3f}",
        ]
    )
    return lines


def footer(params: InlayParams) -> list[str]:
    return [
        f"G0 Z{params.safe_z_mm:.3f}",
        "M5",
        "M30",
    ]
