"""Toolpath simulator — animates the CNC bit's path through a chain list.

Builds a flat sequence of motion segments (rapid travel / plunge / cut), each
with a length and a feed rate. A QTimer steps a virtual cursor through them,
emitting position updates at ~30 Hz. The user controls speed (1× through 100×)
and can pause / resume / stop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal

from cnc_inlay.toolpath.vcarve import Chain


class SegmentKind(str, Enum):
    RAPID = "rapid"   # G0 — air move at machine rapid feed
    PLUNGE = "plunge"  # vertical descent at plunge feed
    CUT = "cut"       # G1 — material removal at feed rate
    LIFT = "lift"     # vertical ascent at machine rapid feed


# Lunyee 3018 PRO MAX-typical accelerations (mm/s²). Intentionally a bit
# lower than the configured GRBL $120/$121/$122 values, because hobby CNCs
# rarely sustain peak acceleration in corner-heavy continuous work like
# inlay pocket clearing — the planner backs off for direction changes,
# small segments never finish accelerating, etc. Using the *configured*
# accel made the sim 30%+ optimistic; these effective values track real
# wall-clock cut time within a few percent on an inlay job.
_DEFAULT_ACCEL_XY_MM_PER_S2 = 150.0
_DEFAULT_ACCEL_Z_MM_PER_S2 = 100.0
# Final fudge applied on top of the per-segment trapezoidal model to
# capture chain-to-chain settling, planner-buffer underfills on bursts of
# tiny segments, and the streamer's character-counting gap. Empirically
# brings the estimate within ~5% on hardwood pocket runs.
_REALWORLD_FUDGE = 1.08


def _trapezoidal_seconds(
    length_mm: float, feed_mm_min: float, accel_mm_per_s2: float,
) -> float:
    """Time for one move that accelerates from rest to ``feed_mm_min``,
    cruises if it can, then decelerates back to rest. Falls back to a
    triangular profile for short moves that never reach commanded feed.
    """
    if length_mm <= 0:
        return 0.0
    feed_mm_per_s = max(feed_mm_min / 60.0, 0.1)
    accel = max(accel_mm_per_s2, 1.0)
    accel_distance = feed_mm_per_s ** 2 / (2.0 * accel)
    if length_mm >= 2.0 * accel_distance:
        cruise_distance = length_mm - 2.0 * accel_distance
        accel_time = feed_mm_per_s / accel
        cruise_time = cruise_distance / feed_mm_per_s
        return 2.0 * accel_time + cruise_time
    # Triangle profile — never reaches commanded feed.
    peak_v = math.sqrt(accel * length_mm)
    return 2.0 * peak_v / accel


@dataclass(frozen=True)
class Segment:
    kind: SegmentKind
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    feed_mm_min: float

    @property
    def length_mm(self) -> float:
        sx, sy, sz = self.start
        ex, ey, ez = self.end
        return math.sqrt((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2)

    @property
    def duration_seconds(self) -> float:
        # Pick the dominant-axis acceleration: XY accel governs cuts and
        # rapids (mostly horizontal), Z accel governs plunge and lift.
        if self.kind in (SegmentKind.PLUNGE, SegmentKind.LIFT):
            accel = _DEFAULT_ACCEL_Z_MM_PER_S2
        else:
            accel = _DEFAULT_ACCEL_XY_MM_PER_S2
        return _trapezoidal_seconds(self.length_mm, self.feed_mm_min, accel)

    def lerp(self, t: float) -> tuple[float, float, float]:
        t = max(0.0, min(1.0, t))
        sx, sy, sz = self.start
        ex, ey, ez = self.end
        return (sx + t * (ex - sx), sy + t * (ey - sy), sz + t * (ez - sz))


def build_segments(
    chains: list[Chain],
    feed_mm_min: float,
    plunge_mm_min: float,
    rapid_mm_min: float = 3000.0,
    safe_z_mm: float = 5.0,
) -> list[Segment]:
    """Convert an ordered list of toolpath chains into motion segments."""
    segments: list[Segment] = []
    last_pos: tuple[float, float, float] = (0.0, 0.0, safe_z_mm)
    for chain in chains:
        if not chain or len(chain) < 1:
            continue
        first = chain[0]

        # Rapid traverse to chain-start XY at safe Z (if not already there).
        traverse_start = (last_pos[0], last_pos[1], safe_z_mm)
        if last_pos[2] != safe_z_mm:
            segments.append(
                Segment(
                    kind=SegmentKind.LIFT,
                    start=last_pos,
                    end=traverse_start,
                    feed_mm_min=rapid_mm_min,
                )
            )
        if (traverse_start[0], traverse_start[1]) != (first[0], first[1]):
            segments.append(
                Segment(
                    kind=SegmentKind.RAPID,
                    start=traverse_start,
                    end=(first[0], first[1], safe_z_mm),
                    feed_mm_min=rapid_mm_min,
                )
            )

        # Plunge to first cut depth.
        if first[2] != safe_z_mm:
            segments.append(
                Segment(
                    kind=SegmentKind.PLUNGE,
                    start=(first[0], first[1], safe_z_mm),
                    end=first,
                    feed_mm_min=plunge_mm_min,
                )
            )

        # Cut along the chain.
        for a, b in zip(chain[:-1], chain[1:]):
            if a == b:
                continue
            segments.append(
                Segment(
                    kind=SegmentKind.CUT,
                    start=a,
                    end=b,
                    feed_mm_min=feed_mm_min,
                )
            )

        last_pt = chain[-1]
        # Lift to safe Z after each chain.
        if last_pt[2] != safe_z_mm:
            segments.append(
                Segment(
                    kind=SegmentKind.LIFT,
                    start=last_pt,
                    end=(last_pt[0], last_pt[1], safe_z_mm),
                    feed_mm_min=rapid_mm_min,
                )
            )
        last_pos = (last_pt[0], last_pt[1], safe_z_mm)
    return segments


def total_duration_seconds(segments: list[Segment]) -> float:
    raw = sum(s.duration_seconds for s in segments)
    return raw * _REALWORLD_FUDGE


class Simulator(QObject):
    """Plays back a list of segments at a user-controlled speed multiplier."""

    positionChanged = Signal(float, float, float)
    progressChanged = Signal(float)              # 0..1
    elapsedChanged = Signal(float, float)         # elapsed_s, total_s
    started = Signal(float)                       # total seconds
    paused = Signal()
    resumed = Signal()
    stopped = Signal()
    finished = Signal()

    _TICK_MS = 33  # ~30 Hz animation tick

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._segments: list[Segment] = []
        self._cumulative_time: list[float] = []  # cumulative time at end of each segment
        self._total_seconds: float = 0.0
        self._elapsed_seconds: float = 0.0
        self._speed: float = 1.0
        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._running = False

    # -------------------------------------------------------- introspection

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def total_seconds(self) -> float:
        return self._total_seconds

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed_seconds

    # ---------------------------------------------------------------- load

    def load(self, segments: list[Segment]) -> None:
        self.stop()
        self._segments = list(segments)
        self._cumulative_time = []
        accum = 0.0
        for s in self._segments:
            accum += s.duration_seconds
            self._cumulative_time.append(accum)
        self._total_seconds = accum
        self._elapsed_seconds = 0.0
        self.elapsedChanged.emit(0.0, self._total_seconds)
        if self._segments:
            self.positionChanged.emit(*self._segments[0].start)

    # -------------------------------------------------------------- control

    def play(self) -> None:
        if not self._segments:
            return
        if self._elapsed_seconds >= self._total_seconds:
            self._elapsed_seconds = 0.0
            self.elapsedChanged.emit(0.0, self._total_seconds)
        if self._running:
            return
        self._running = True
        self._timer.start()
        if self._elapsed_seconds == 0.0:
            self.started.emit(self._total_seconds)
        else:
            self.resumed.emit()

    def pause(self) -> None:
        if not self._running:
            return
        self._running = False
        self._timer.stop()
        self.paused.emit()

    def stop(self) -> None:
        was_running = self._running
        self._running = False
        self._timer.stop()
        self._elapsed_seconds = 0.0
        self.elapsedChanged.emit(0.0, self._total_seconds)
        if self._segments:
            self.positionChanged.emit(*self._segments[0].start)
            self.progressChanged.emit(0.0)
        if was_running:
            self.stopped.emit()

    def set_speed(self, multiplier: float) -> None:
        self._speed = max(0.1, multiplier)

    # -------------------------------------------------------------- internal

    def _tick(self) -> None:
        if not self._segments:
            self.pause()
            return
        dt_seconds = self._TICK_MS / 1000.0 * self._speed
        self._elapsed_seconds = min(self._elapsed_seconds + dt_seconds, self._total_seconds)
        # Find which segment we're in.
        idx = 0
        for i, t_end in enumerate(self._cumulative_time):
            if self._elapsed_seconds <= t_end:
                idx = i
                break
        else:
            idx = len(self._segments) - 1
        seg = self._segments[idx]
        seg_start_time = self._cumulative_time[idx] - seg.duration_seconds
        seg_dur = seg.duration_seconds
        if seg_dur > 0:
            t = (self._elapsed_seconds - seg_start_time) / seg_dur
        else:
            t = 1.0
        x, y, z = seg.lerp(t)
        self.positionChanged.emit(x, y, z)
        self.progressChanged.emit(self._elapsed_seconds / max(self._total_seconds, 1e-9))
        self.elapsedChanged.emit(self._elapsed_seconds, self._total_seconds)
        if self._elapsed_seconds >= self._total_seconds:
            self._running = False
            self._timer.stop()
            self.finished.emit()
