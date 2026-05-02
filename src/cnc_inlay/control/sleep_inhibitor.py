"""Prevent macOS from sleeping while a CNC operation is in progress.

We shell out to the macOS-native ``caffeinate`` command, which the OS
recognises as the canonical "keep awake" assertion. The subprocess stays
alive as long as we're holding the lock; killing it releases the
assertion immediately.
"""
from __future__ import annotations

import subprocess


class SleepInhibitor:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    @property
    def is_holding(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def acquire(self) -> None:
        """Tell macOS to stay awake until ``release()``. Idempotent."""
        if self.is_holding:
            return
        try:
            # -d: prevent display sleep
            # -i: prevent idle sleep
            # -s: prevent system sleep on AC
            # -u: simulate user activity (keeps Mac fully awake)
            self._proc = subprocess.Popen(
                ["caffeinate", "-disu"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._proc = None

    def release(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        except Exception:
            pass
        finally:
            self._proc = None
