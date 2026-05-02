"""GRBL protocol constants, response parsers, and command builders.

Pure-Python with no Qt dependency so it can be unit-tested independently.
References: https://github.com/gnea/grbl/wiki
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Real-time GRBL command bytes — single-byte, sent without a newline.
STATUS_QUERY = b"?"
FEED_HOLD = b"!"
CYCLE_START = b"~"
SOFT_RESET = b"\x18"
JOG_CANCEL = b"\x85"

# Connection settings.
BAUD = 115200
RX_BUFFER_SIZE = 128  # GRBL serial input buffer in bytes.


class MachineState(str, Enum):
    IDLE = "Idle"
    RUN = "Run"
    HOLD = "Hold"
    JOG = "Jog"
    ALARM = "Alarm"
    DOOR = "Door"
    CHECK = "Check"
    HOME = "Home"
    SLEEP = "Sleep"
    UNKNOWN = "?"


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    z: float

    def __sub__(self, other: "Position") -> "Position":
        return Position(self.x - other.x, self.y - other.y, self.z - other.z)


@dataclass(frozen=True)
class Status:
    state: MachineState
    mpos: Position | None = None
    wpos: Position | None = None
    wco: Position | None = None        # work-coordinate offset; wpos = mpos - wco
    feed: float | None = None
    spindle: float | None = None
    raw: str = ""

    @property
    def effective_wpos(self) -> Position | None:
        """Return work-coordinate position, computing it from MPos+WCO if needed."""
        if self.wpos is not None:
            return self.wpos
        if self.mpos is not None and self.wco is not None:
            return self.mpos - self.wco
        return None


class ResponseKind(str, Enum):
    OK = "ok"
    ERROR = "error"
    ALARM = "alarm"
    WELCOME = "welcome"
    MESSAGE = "message"
    SETTING = "setting"
    OTHER = "other"


@dataclass(frozen=True)
class Response:
    kind: ResponseKind
    code: int = 0
    text: str = ""
    raw: str = ""


def _parse_xyz(value: str) -> Position | None:
    parts = value.split(",")
    if len(parts) < 3:
        return None
    try:
        return Position(float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError:
        return None


def parse_status(line: str) -> Status | None:
    """Parse a GRBL status report like ``<Idle|MPos:0.000,0.000,0.000|FS:0,0>``.

    Returns None for lines that don't match the status format.
    """
    line = line.strip()
    if not (line.startswith("<") and line.endswith(">")):
        return None
    body = line[1:-1]
    parts = body.split("|")
    if not parts:
        return None

    # First field is the state, possibly with a sub-state e.g. "Hold:0".
    state_token = parts[0].split(":", 1)[0].strip()
    try:
        state = MachineState(state_token)
    except ValueError:
        state = MachineState.UNKNOWN

    mpos = wpos = wco = None
    feed: float | None = None
    spindle: float | None = None

    for field in parts[1:]:
        if ":" not in field:
            continue
        key, value = field.split(":", 1)
        if key == "MPos":
            mpos = _parse_xyz(value)
        elif key == "WPos":
            wpos = _parse_xyz(value)
        elif key == "WCO":
            wco = _parse_xyz(value)
        elif key == "FS":
            nums = value.split(",")
            if len(nums) >= 2:
                try:
                    feed = float(nums[0])
                    spindle = float(nums[1])
                except ValueError:
                    pass
        elif key == "F":
            try:
                feed = float(value)
            except ValueError:
                pass

    return Status(
        state=state, mpos=mpos, wpos=wpos, wco=wco,
        feed=feed, spindle=spindle, raw=line,
    )


def parse_response(line: str) -> Response:
    """Classify a single line of GRBL output."""
    s = line.strip()
    if not s:
        return Response(kind=ResponseKind.OTHER, raw=line)
    if s == "ok":
        return Response(kind=ResponseKind.OK, raw=line)
    if s.startswith("error:"):
        try:
            code = int(s[6:])
        except ValueError:
            code = 0
        return Response(kind=ResponseKind.ERROR, code=code, raw=line)
    if s.startswith("ALARM:"):
        try:
            code = int(s[6:])
        except ValueError:
            code = 0
        return Response(kind=ResponseKind.ALARM, code=code, raw=line)
    if s.lower().startswith("grbl"):
        return Response(kind=ResponseKind.WELCOME, text=s, raw=line)
    if s.startswith("[") and s.endswith("]"):
        return Response(kind=ResponseKind.MESSAGE, text=s, raw=line)
    if s.startswith("$"):
        return Response(kind=ResponseKind.SETTING, text=s, raw=line)
    return Response(kind=ResponseKind.OTHER, text=s, raw=line)


# Command-string builders.

def cmd_set_work_zero_all() -> str:
    """Set the current machine position as X=Y=Z=0 in WCS 1 (G54)."""
    return "G10 L20 P1 X0 Y0 Z0"


def cmd_set_work_zero_xy() -> str:
    return "G10 L20 P1 X0 Y0"


def cmd_set_work_zero_z() -> str:
    return "G10 L20 P1 Z0"


def cmd_goto_zero_xy() -> str:
    return "G90 G0 X0 Y0"


def cmd_goto_zero_z() -> str:
    return "G90 G0 Z0"


def cmd_jog(axis: str, distance_mm: float, feed_mm_min: float) -> str:
    """Build a GRBL 1.1+ jog command (incremental, mm)."""
    return f"$J=G91 G21 {axis}{distance_mm:.3f} F{feed_mm_min:g}"


# Human-readable error/alarm names — full GRBL list.

ERROR_MESSAGES = {
    1: "Expected command letter",
    2: "Bad number format",
    3: "Invalid statement",
    4: "Negative value",
    5: "Setting disabled",
    6: "Setting step pulse min",
    7: "Setting read fail",
    8: "Idle error",
    9: "G-code lock",
    10: "Soft limits not enabled",
    11: "Line overflow",
    12: "Step rate too high",
    13: "Check door",
    14: "Line length exceeded",
    15: "Travel exceeded",
    16: "Invalid jog command",
    17: "Setting disabled (laser)",
    20: "Unsupported command",
    21: "Modal group violation",
    22: "Undefined feed rate",
    23: "Invalid g-code ID:23",
    24: "Invalid g-code ID:24",
    25: "Invalid g-code ID:25",
    26: "Invalid g-code ID:26",
    27: "Invalid g-code ID:27",
    28: "Invalid g-code ID:28",
    29: "Invalid g-code ID:29",
    30: "Invalid g-code ID:30",
    31: "Invalid g-code ID:31",
    32: "Invalid g-code ID:32",
    33: "Invalid g-code ID:33",
    34: "Invalid g-code ID:34",
    35: "Invalid g-code ID:35",
    36: "Invalid g-code ID:36",
    37: "Invalid g-code ID:37",
    38: "Invalid g-code ID:38",
}

ALARM_MESSAGES = {
    1: "Hard limit triggered",
    2: "Soft limit alarm — motion target outside machine envelope",
    3: "Reset while in motion",
    4: "Probe fail (initial state)",
    5: "Probe fail (no contact)",
    6: "Homing fail (reset)",
    7: "Homing fail (door)",
    8: "Homing fail (limit not reached)",
    9: "Homing fail (limit pressed at start)",
}


def error_text(code: int) -> str:
    return ERROR_MESSAGES.get(code, f"GRBL error {code}")


def alarm_text(code: int) -> str:
    return ALARM_MESSAGES.get(code, f"GRBL alarm {code}")
