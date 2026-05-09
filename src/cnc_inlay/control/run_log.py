"""Per-Connect-session timestamped log of every user action and every
byte exchanged with GRBL. Lives at:

    ~/Library/Logs/CNC Inlay Studio/run-YYYY-MM-DD_HH-MM-SS.log

Format (one event per line, sorted by time):

    HH:MM:SS.mmm [tag] message

Tags:
    * ``[user]`` — operator-initiated UI actions (Connect, Jog, Set Zero,
      Run, Pause, Resume, Abort, Disconnect, etc.).
    * ``[tx]``   — every G-code line written to the serial port. One per
      command; long programs can produce thousands of these.
    * ``[tx-rt]`` — real-time bytes (single-byte commands like ``?``,
      ``!``, ``~``, ``\\x18``) — logged as a hex literal so non-printable
      bytes are visible.
    * ``[rx]``   — every line GRBL writes back. Status reports, ``ok``,
      ``error:N``, ``ALARM:N``, ``[MSG:...]``, etc.
    * ``[event]`` — internal milestones (port opened, stream started,
      tool-change required, stream completed, etc.).
    * ``[error]`` — exceptions and worker-error messages.

The writer is thread-safe (a `threading.Lock` guards the file handle) so
the worker QThread and main UI thread can both call into the same
singleton without coordinating themselves. File I/O failures are
swallowed silently — losing a log line must never crash a running cut.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

# Module-level singleton so both Controller (main thread) and Worker
# (QThread) reach the same file without having to thread the instance
# through every signal.
_log_lock = threading.Lock()
_log_file = None
_log_path: Path | None = None


def _log_dir() -> Path:
    """Standard macOS logs folder — survives app-bundle reinstalls and
    is what users instinctively share via Console.app."""
    p = Path.home() / "Library" / "Logs" / "CNC Inlay Studio"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def open_session() -> Path | None:
    """Begin a new log file, named with the current local timestamp.
    If a file is already open, closes it first so we always get one
    file per Connect → Disconnect cycle. Returns the new path (or
    None on failure)."""
    global _log_file, _log_path
    close_session()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = _log_dir() / f"run-{stamp}.log"
    try:
        # Line-buffered so a crash mid-cut doesn't lose the most recent
        # actions — the worker writes constantly, so the buffer-flush
        # cost is dwarfed by the serial I/O it's logging anyway.
        f = open(path, "w", encoding="utf-8", buffering=1)
    except Exception:
        return None
    with _log_lock:
        _log_file = f
        _log_path = path
    log_event(f"session opened — {path}")
    return path


def close_session() -> None:
    """Flush and close the current log. Safe to call when nothing's
    open. Called on Disconnect and at app shutdown."""
    global _log_file, _log_path
    with _log_lock:
        f = _log_file
        _log_file = None
        _log_path = None
    if f is not None:
        try:
            f.write(f"{_now()} [event] session closed\n")
            f.flush()
            f.close()
        except Exception:
            pass


def current_path() -> Path | None:
    """Path to the active log file, or None if no session is open."""
    with _log_lock:
        return _log_path


def _now() -> str:
    """High-resolution local timestamp. Includes milliseconds — useful
    for correlating GRBL response latency with command timing."""
    t = time.time()
    s = time.strftime("%H:%M:%S", time.localtime(t))
    ms = int((t - int(t)) * 1000)
    return f"{s}.{ms:03d}"


def _write(line: str) -> None:
    """Append one line to the log if a session is active. Best-effort —
    swallows all file-IO errors so a logging failure can never crash a
    running cut."""
    with _log_lock:
        f = _log_file
        if f is None:
            return
        try:
            f.write(line)
            if not line.endswith("\n"):
                f.write("\n")
        except Exception:
            pass


def log_user(action: str) -> None:
    """An operator-initiated UI action (Connect, Jog, Set Zero, Run,
    Pause, etc.). Use a short imperative phrase + relevant args."""
    _write(f"{_now()} [user] {action}")


def log_tx(line: str) -> None:
    """A G-code line we wrote to the serial port."""
    _write(f"{_now()} [tx] {line.rstrip()}")


def log_tx_realtime(data: bytes) -> None:
    """A real-time byte/byte-sequence written to the serial port. We
    hex-encode because most real-time commands are non-printable
    (0x18 = soft reset, 0x84 = jog cancel, etc.)."""
    hex_repr = data.hex(" ")
    label = {
        b"?": "status query",
        b"!": "feed hold",
        b"~": "cycle start",
        b"\x18": "soft reset",
        b"\x84": "jog cancel",
    }.get(data, "")
    suffix = f" ({label})" if label else ""
    _write(f"{_now()} [tx-rt] {hex_repr}{suffix}")


def log_rx(line: str) -> None:
    """A line GRBL wrote back to us."""
    _write(f"{_now()} [rx] {line.rstrip()}")


def log_event(message: str) -> None:
    """Internal milestone — port opened/closed, stream started/completed,
    tool-change banner shown, etc."""
    _write(f"{_now()} [event] {message}")


def log_error(message: str) -> None:
    """An exception or worker-side error string."""
    _write(f"{_now()} [error] {message}")
