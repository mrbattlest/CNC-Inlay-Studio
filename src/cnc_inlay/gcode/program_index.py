"""Index a G-code program for the scrub bar + 3D position preview.

Three pieces of derived data the UI needs once a Preview is generated:

  * **Bit segments** — for each tool (M3 / M0 / M3 cycle), the contiguous
    range of streamable line indices, the bit name, and the bit's index
    in the running order (used to colour the segment).
  * **Per-line XYZ position** — current absolute machine position after
    each streamable line, so the scrub bar can drive a live 3D-canvas
    bit marker as the user drags.
  * **Cumulative seconds** — running total of estimated cut time after
    each line, so a "Run from cursor" estimate can be recomputed as
    ``cumulative_seconds[-1] - cumulative_seconds[start_line]`` instead
    of always using the full-program estimate.

"Streamable lines" are the same ones the GRBL streamer keeps — comments
and blank lines are dropped. M0 / M3 stay in the list so positional
indexing matches what the streamer sends and what
`MachineController.start_stream(start_line=N)` expects.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from cnc_inlay.sim.simulator import (
    _DEFAULT_ACCEL_XY_MM_PER_S2,
    _DEFAULT_ACCEL_Z_MM_PER_S2,
    _REALWORLD_FUDGE,
    _trapezoidal_seconds,
)


_TOOL_CHANGE_RE = re.compile(
    r"TOOL\s+CHANGE:\s*install\s+([^\[(]+?)(?:\s*\[|$)", re.IGNORECASE
)
# Default rapid feed (mm/min) for G0 moves. GRBL's max rapid is set per
# axis via $110/$111/$112, but for time estimation a single representative
# rate is good enough — Lunyee 3018 ships with ~3000 mm/min defaults.
_RAPID_MM_MIN = 3000.0


@dataclass
class ProgramIndex:
    """Parsed view of a G-code program for UI consumption."""
    total_lines: int
    # (start_line, end_line_exclusive, bit_name)
    segments: list[tuple[int, int, str]]
    # Absolute (x, y, z) after each line — same length as total_lines.
    positions: list[tuple[float, float, float]]
    # Cumulative seconds of estimated cut time AFTER each line. Same
    # length as total_lines; same accel model + realworld fudge that the
    # simulator uses, so the numbers match the side-total estimate the
    # user sees in the Simulate panel.
    cumulative_seconds: list[float]


def index_program(gcode: str, first_bit_name: str | None = None) -> ProgramIndex:
    """Walk the program once, tracking comments → bit names, M0 boundaries,
    and modal absolute X/Y/Z. Returns a :class:`ProgramIndex` whose line
    indices match what the streamer sends to GRBL.

    ``first_bit_name`` is used as the bit label for the segment that
    starts at line 0 — the G-code only labels bits at tool-change comments,
    which sit before *subsequent* M0 pauses, never before the first cut.
    Falls back to "(unknown)" if the caller doesn't pass one.
    """
    lines: list[str] = []
    tool_change_lines: list[tuple[int, str]] = []  # (line_index, bit_name)

    last_tc_name: str | None = None
    for raw in gcode.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("("):
            m = _TOOL_CHANGE_RE.search(s)
            if m:
                last_tc_name = m.group(1).strip()
            continue
        if s.upper().startswith("M0") and last_tc_name is not None:
            tool_change_lines.append((len(lines), last_tc_name))
            last_tc_name = None
        lines.append(s)

    total = len(lines)
    if total == 0:
        return ProgramIndex(
            total_lines=0, segments=[], positions=[], cumulative_seconds=[]
        )

    # Build segments. Each tool change at line N closes the current
    # segment at N+1 (M0 inclusive) and opens the next at N+1.
    segments: list[tuple[int, int, str]] = []
    cursor = 0
    current_bit = first_bit_name or "(unknown)"
    for tc_line, tc_name in tool_change_lines:
        end = tc_line + 1  # include the M0 line in the closing segment
        segments.append((cursor, end, current_bit))
        cursor = end
        current_bit = tc_name
    segments.append((cursor, total, current_bit))

    # Walk lines, tracking absolute X/Y/Z + modal feed rate + cumulative
    # time. The emitter only uses absolute mode (G90) for streamable
    # lines, so we don't worry about G91.
    positions: list[tuple[float, float, float]] = []
    cumulative: list[float] = []
    px = py = pz = 0.0
    x = y = z = 0.0
    modal_f = 600.0   # any reasonable starting feed; gets replaced by first F
    accum_s = 0.0

    for ln in lines:
        cmd = ln.upper()
        # Reset to previous position for diff calculation.
        px, py, pz = x, y, z

        # Parse motion + F update.
        is_g0 = cmd.startswith(("G0 ", "G00 ", "G0", "G00"))
        is_g1 = cmd.startswith(("G1 ", "G01 ", "G1", "G01"))
        is_g4 = cmd.startswith(("G4 ", "G04 "))
        new_f: float | None = None

        if cmd[0] in ("G", "X", "Y", "Z"):
            for tok in cmd.split():
                if not tok:
                    continue
                head = tok[0]
                if head not in ("X", "Y", "Z", "F"):
                    continue
                try:
                    v = float(tok[1:])
                except ValueError:
                    continue
                if head == "X":
                    x = v
                elif head == "Y":
                    y = v
                elif head == "Z":
                    z = v
                else:  # F
                    new_f = v

        if new_f is not None:
            modal_f = new_f

        # Compute time contribution.
        dt = 0.0
        if is_g4:
            # G4 P<seconds> — dwell. GRBL uses P in seconds (or P/1000 ms
            # depending on $ setting; default is seconds on hobby builds).
            for tok in cmd.split():
                if tok.startswith("P"):
                    try:
                        dt = float(tok[1:])
                    except ValueError:
                        pass
                    break
        elif is_g0 or is_g1:
            dx = x - px
            dy = y - py
            dz = z - pz
            xy_dist = (dx * dx + dy * dy) ** 0.5
            z_dist = abs(dz)
            if xy_dist > 0 or z_dist > 0:
                # Pure-Z move uses Z accel + (rapid for G0, plunge feed
                # for G1 — assume modal F is the plunge feed in that
                # case). Pure-XY uses XY accel + (rapid for G0, modal F
                # for G1). Mixed moves dominated by XY get XY accel.
                if xy_dist == 0 and z_dist > 0:
                    feed = _RAPID_MM_MIN if is_g0 else modal_f
                    dt = _trapezoidal_seconds(
                        z_dist, feed, _DEFAULT_ACCEL_Z_MM_PER_S2
                    )
                else:
                    feed = _RAPID_MM_MIN if is_g0 else modal_f
                    total_dist = (xy_dist * xy_dist + z_dist * z_dist) ** 0.5
                    dt = _trapezoidal_seconds(
                        total_dist, feed, _DEFAULT_ACCEL_XY_MM_PER_S2
                    )

        accum_s += dt
        positions.append((x, y, z))
        cumulative.append(accum_s * _REALWORLD_FUDGE)

    return ProgramIndex(
        total_lines=total,
        segments=segments,
        positions=positions,
        cumulative_seconds=cumulative,
    )
