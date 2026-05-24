"""Unit tests for the character-counting G-code streamer.

The focus here is the programmed-M0 tool-change behaviour: the streamer must
park *before* the M0 line and never send it to GRBL (sending it puts GRBL into
the Hold state, where jog and G10 re-zero are both rejected — exactly the
controls the operator needs during a bit swap). ``resume_past_tool_change``
skips the M0 and continues with the next bit's spindle-on.
"""
from __future__ import annotations

from cnc_inlay.control.streamer import Streamer


_TWO_BIT_GCODE = """( CNC Inlay Studio - test - pocket )
G21
G90
M3 S10000
G4 P3
G1 X1.0 Y1.0 Z-1.0 F200
G0 Z5
M5
G0 Z40.000
G0 X20.000 Y0.000
( TOOL CHANGE: install 1.0 mm endmill [1.00 mm] )
M0
M3 S12000
G4 P3
G1 X2.0 Y2.0 Z-1.0 F300
M5
"""


def _drain(streamer: Streamer) -> list[str]:
    """Feed until the streamer stops making progress, acking every line
    instantly. Returns the lines actually written to the fake port."""
    sent: list[str] = []
    for _ in range(200):
        n = streamer.feed(lambda p: sent.append(p.decode().strip()))
        while streamer.pending:
            streamer.on_ok()
        if n == 0:
            break
    return sent


def test_m0_is_never_sent_and_streamer_parks_before_it():
    st = Streamer()
    st.load(_TWO_BIT_GCODE)
    # The M0 is at parsed-line index 9 (comments are stripped on load).
    assert st.tool_change_at == {9: "1.0 mm endmill"}

    sent = _drain(st)
    assert "M0" not in sent
    assert sent[-1] == "G0 X20.000 Y0.000"
    assert st.at_tool_change
    assert st.is_paused
    assert not st.is_done


def test_resume_past_tool_change_skips_m0_and_continues():
    st = Streamer()
    st.load(_TWO_BIT_GCODE)
    _drain(st)
    assert st.at_tool_change

    st.resume_past_tool_change()
    assert not st.is_paused
    assert not st.at_tool_change

    sent = _drain(st)
    assert "M0" not in sent
    assert sent[0] == "M3 S12000"
    assert st.is_done


def test_resume_past_tool_change_is_a_noop_when_not_at_m0():
    st = Streamer()
    st.load(_TWO_BIT_GCODE)
    # Not parked at an M0 (cursor still at the top) — behaves like a
    # plain resume: clears the pause flag without skipping a line.
    st.pause()
    cursor = st.cursor
    st.resume_past_tool_change()
    assert not st.is_paused
    assert st.cursor == cursor


def test_resume_preamble_rapids_xy_then_plunges_z_never_diagonal():
    """Resume-from-cursor must never let the bit move XYZ together —
    that would diagonal-plunge into uncut wood. The streamer's preamble
    splits any combined move into a horizontal rapid at safe Z first,
    then a vertical plunge down to cut depth."""
    gcode = (
        "G21\nG90\nG17\nG54\nG0 Z5\nM3 S10000\nG4 P3\n"
        "G0 X10 Y20\n"
        "G1 Z-2 F200\n"
        "G1 X11 Y20 Z-2 F500\n"
        "G1 X12 Y20 Z-2\n"
        "G1 X13 Y22 Z-2\n"
        "G0 Z5\nM5\n"
    )
    # Resume mid-cut on the 4th cut line — the most dangerous case
    # because the line specifies all three axes.
    stripped = [
        ln for ln in gcode.splitlines()
        if ln.strip() and not ln.strip().startswith("(")
    ]
    target_line = stripped.index("G1 X13 Y22 Z-2")

    st = Streamer()
    st.load(gcode, start_line=target_line)

    # Locate the safe-rapid and the vertical plunge in the preamble.
    plunge_idx = None
    rapid_idx = None
    for i, ln in enumerate(st.lines[: st.preamble_len]):
        if ln.startswith("G0 ") and " X" in ln and " Y" in ln and " Z" not in ln:
            rapid_idx = i
        elif ln.startswith("G1 Z") and "F" in ln:
            plunge_idx = i

    assert rapid_idx is not None, "expected a horizontal-only G0 rapid"
    assert plunge_idx is not None, "expected a vertical-only G1 Z plunge"
    assert rapid_idx < plunge_idx, "rapid must come BEFORE the plunge"
    # The plunge target must match the cursor's cut depth.
    assert "Z-2" in st.lines[plunge_idx]
    # The synthetic preamble carries the right X, Y, Z target.
    assert "X13" in st.lines[rapid_idx]
    assert "Y22" in st.lines[rapid_idx]


def test_program_without_m0_streams_straight_through():
    st = Streamer()
    st.load("G21\nG90\nM3 S10000\nG1 X1 Y1 Z-1 F200\nM5\n")
    assert st.tool_change_at == {}
    sent = _drain(st)
    assert sent == ["G21", "G90", "M3 S10000", "G1 X1 Y1 Z-1 F200", "M5"]
    assert st.is_done
