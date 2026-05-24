"""Character-counting G-code streamer for GRBL.

GRBL has a 128-byte serial input buffer. The streamer keeps track of how many
bytes are currently *in flight* (sent but not yet ack'd with ``ok``) and only
sends a new line when it fits without overflowing the buffer. This lets us
saturate the link instead of waiting line-by-line, giving roughly 5–10× faster
cuts than the naïve send-and-wait protocol.
"""
from __future__ import annotations

from collections import deque
from typing import Callable

from cnc_inlay.control.grbl import RX_BUFFER_SIZE


class Streamer:
    """State machine for streaming a G-code program with buffer accounting."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.cursor: int = 0
        self.pending: deque[int] = deque()
        self.pending_bytes: int = 0
        self.is_paused: bool = False
        self.is_aborted: bool = False
        # Map line-index → bit name for every M0 in the program. Populated by
        # load() from the source comments. Lets the UI show "Install <bit>"
        # without the streamer having to keep comment lines around.
        self.tool_change_at: dict[int, str] = {}
        # Number of synthetic preamble lines load() prepended when a
        # non-zero start_line was given (modal-state restore + safe
        # rapid-then-plunge resume). Zero for a fresh-from-line-0 run.
        # MainWindow reads this to map streamer-line space back to
        # original-program-line space for the scrub bar.
        self.preamble_len: int = 0

    @property
    def total(self) -> int:
        return len(self.lines)

    @property
    def is_done(self) -> bool:
        return self.cursor >= self.total and not self.pending

    def load(self, gcode: str, start_line: int = 0) -> None:
        """Reset state and parse a G-code program into streamable lines.

        ``start_line`` is an index into the parsed (comment-stripped) line list
        — pass a non-zero value to resume from mid-program. The cursor jumps
        ahead, and a small modal-state preamble is prepended so GRBL is in
        absolute / mm / WCS-1 mode and the bit is at safe Z before the next
        cut command runs.
        """
        out: list[str] = []
        tool_change_at: dict[int, str] = {}
        # Walk source lines, stripping comments but remembering the most
        # recent TOOL CHANGE comment so we can attach its bit name to the
        # next M0 we keep.
        import re
        tc_pattern = re.compile(r"TOOL\s+CHANGE:\s*install\s+([^\[(]+?)(?:\s*\[|$)", re.IGNORECASE)
        last_tool_change: str | None = None
        for raw in gcode.splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            if stripped.startswith("("):
                m = tc_pattern.search(stripped)
                if m:
                    last_tool_change = m.group(1).strip()
                # Drop comment line — don't waste buffer slots on it.
                continue
            # M0 = programmed pause for tool change. Attach the upcoming
            # bit name so the UI can prompt the user with it.
            if stripped.upper().startswith("M0") and last_tool_change is not None:
                tool_change_at[len(out)] = last_tool_change
                last_tool_change = None  # consumed
            out.append(stripped)
        if start_line > 0 and start_line < len(out):
            # Re-establish modal state and lift to safe Z before resuming.
            # Critically, also restart the spindle and re-set the feed
            # rate: the next move out of the preamble is typically a
            # plunge, and without a fresh M3 + ramp-up dwell the bit
            # plunges into wood with the spindle off (snapped a user's
            # bit), and without a modal F set the first G1 errors out
            # with GRBL error 22 ("feed rate undefined") because the
            # emitter only writes the F-word on the first G1 of each
            # chain — subsequent G1s rely on F being modal from earlier
            # in the file, which Run-from-cursor skips past. Scan
            # backward from the resume point for the most recent M3
            # (carries the right RPM for the current bit) and the most
            # recent F-word (carries the right cut feed for the current
            # section). Defaults are conservative.
            import re
            m3_line = "M3 S10000"
            for j in range(min(start_line, len(out)) - 1, -1, -1):
                if out[j].upper().startswith("M3"):
                    m3_line = out[j]
                    break
            f_line: str | None = None
            f_pattern = re.compile(r"\bF(\d+(?:\.\d+)?)\b", re.IGNORECASE)
            for j in range(min(start_line, len(out)) - 1, -1, -1):
                m = f_pattern.search(out[j])
                if m:
                    # Standalone F-word is a valid GRBL line — sets the
                    # modal feed rate without commanding any motion.
                    f_line = f"F{m.group(1)}"
                    break
            preamble = [
                "G21", "G90", "G17", "G54", "G0 Z5",
                m3_line,
                "G4 P3",  # spindle ramp-up dwell — same as fresh-start emit
            ]
            if f_line is not None:
                preamble.append(f_line)

            # Resume must NEVER move XYZ together — a diagonal move from
            # safe Z down to cut depth would plunge the bit through the
            # wood at an angle and ruin the piece. Walk forward from the
            # resume cursor to find the first absolute X / Y / Z values,
            # then add: (1) a horizontal rapid at safe Z to the target
            # XY, (2) a slow vertical plunge straight down to the cut
            # depth. The original gcode at start_line then continues
            # from the right position with no further dual-axis motion.
            xyz_pat = re.compile(
                r"(?:^|\s|[GMgm]\d+\s*)([XYZxyz])(-?\d+(?:\.\d+)?)"
            )
            target_x = target_y = target_z = None

            def _scan_for_xyz(indices):
                nonlocal target_x, target_y, target_z
                for k in indices:
                    if k < 0 or k >= len(out):
                        continue
                    for m in xyz_pat.finditer(out[k]):
                        axis = m.group(1).upper()
                        val = float(m.group(2))
                        if axis == "X" and target_x is None:
                            target_x = val
                        elif axis == "Y" and target_y is None:
                            target_y = val
                        elif axis == "Z" and target_z is None:
                            target_z = val
                    if (target_x is not None and target_y is not None
                            and target_z is not None):
                        return

            # Look ahead first — the resume target is the next position
            # the program wants. Cap at 200 lines to keep load() fast.
            _scan_for_xyz(range(start_line, min(start_line + 200, len(out))))
            # Anything still unset: fall back to the most-recent prior
            # value (modal sticky state from before the cursor).
            if target_x is None or target_y is None or target_z is None:
                _scan_for_xyz(range(start_line - 1, max(-1, start_line - 400), -1))

            safe_z = 5.0
            if target_x is not None and target_y is not None:
                # Make absolutely sure we're at safe Z before the XY
                # rapid (the operator may have jogged Z down during
                # re-zero), then rapid horizontally only.
                preamble.append(f"G0 Z{safe_z:.4f}")
                preamble.append(f"G0 X{target_x:.4f} Y{target_y:.4f}")
                if target_z is not None and target_z < safe_z - 0.01:
                    # Vertical plunge at a conservative feed — slower
                    # than the cut feed because the bit might have to
                    # plough through a tiny amount of residual at the
                    # cursor's depth before the program's own feed
                    # takes over.
                    preamble.append(f"G1 Z{target_z:.4f} F100")

            # Tool-change indices need to shift by len(preamble) - start_line.
            shift = len(preamble) - start_line
            self.tool_change_at = {
                i + shift: n for i, n in tool_change_at.items()
                if i >= start_line
            }
            self.lines = preamble + out[start_line:]
            self.preamble_len = len(preamble)
        else:
            self.lines = out
            self.tool_change_at = tool_change_at
            self.preamble_len = 0
        self.cursor = 0
        self.pending.clear()
        self.pending_bytes = 0
        self.is_paused = False
        self.is_aborted = False

    @property
    def at_tool_change(self) -> bool:
        """True when the cursor is parked on a programmed-M0 line (the line
        is *not* sent to GRBL — see ``feed``)."""
        return self.cursor in self.tool_change_at

    def feed(self, write_fn: Callable[[bytes], None]) -> int:
        """Send as many lines as fit in GRBL's remaining serial buffer.

        Returns the number of lines written this call.

        A programmed ``M0`` tool-change line is *never* sent to GRBL: it
        would put GRBL into the ``Hold`` state, where ``$J=`` jog and
        ``G10`` re-zero commands are both rejected — exactly the controls
        the operator needs during a bit swap. Instead the streamer parks
        itself one line before the M0 (``is_paused = True``); GRBL drains
        its buffer and settles to ``Idle``, leaving jog and re-zero fully
        available. ``resume_past_tool_change`` skips the M0 and continues.
        """
        if self.is_paused or self.is_aborted:
            return 0
        sent = 0
        while self.cursor < self.total:
            if self.cursor in self.tool_change_at:
                # Park before the M0 — don't send it. The controller
                # waits for GRBL to reach Idle, then prompts the swap.
                self.is_paused = True
                break
            line = self.lines[self.cursor]
            payload = (line + "\n").encode("ascii", errors="replace")
            n = len(payload)
            if self.pending_bytes + n > RX_BUFFER_SIZE:
                break
            write_fn(payload)
            self.pending.append(n)
            self.pending_bytes += n
            self.cursor += 1
            sent += 1
        return sent

    def on_ok(self) -> None:
        if self.pending:
            n = self.pending.popleft()
            self.pending_bytes -= n

    def on_error(self) -> None:
        self.is_aborted = True

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False

    def resume_past_tool_change(self) -> None:
        """Resume after a programmed-M0 halt: skip the M0 line (it was never
        sent to GRBL) and continue feeding from the next bit's spindle-on.
        Falls back to a plain ``resume`` if the cursor isn't on an M0."""
        if self.cursor in self.tool_change_at:
            self.cursor += 1
        self.is_paused = False

    def abort(self) -> None:
        self.is_aborted = True
        self.cursor = self.total
        self.pending.clear()
        self.pending_bytes = 0
