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
            # Tool-change indices need to shift by len(preamble) - start_line.
            shift = len(preamble) - start_line
            self.tool_change_at = {
                i + shift: n for i, n in tool_change_at.items()
                if i >= start_line
            }
            self.lines = preamble + out[start_line:]
        else:
            self.lines = out
            self.tool_change_at = tool_change_at
        self.cursor = 0
        self.pending.clear()
        self.pending_bytes = 0
        self.is_paused = False
        self.is_aborted = False

    def feed(self, write_fn: Callable[[bytes], None]) -> int:
        """Send as many lines as fit in GRBL's remaining serial buffer.

        Returns the number of lines written this call.
        """
        if self.is_paused or self.is_aborted:
            return 0
        sent = 0
        while self.cursor < self.total:
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

    def abort(self) -> None:
        self.is_aborted = True
        self.cursor = self.total
        self.pending.clear()
        self.pending_bytes = 0
