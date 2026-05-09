- New per-Connect run log captures every operator action and every
  byte exchanged with GRBL — perfect for sharing back when something
  goes wrong on the machine. Each Connect opens a fresh timestamped
  file at:
    ~/Library/Logs/CNC Inlay Studio/run-YYYY-MM-DD_HH-MM-SS.log
  Disconnect (or quitting the app) closes it.

  What's logged:
    • [user]  — Connect, Disconnect, Jog, Set Zero (XY/Z/All), Goto 0,
                Run, Run-from-cursor, Pause, Resume, Abort, Soft reset,
                Query settings.
    • [tx]    — every G-code line written to the serial port (including
                each line streamed during a cut).
    • [tx-rt] — real-time bytes (?, !, ~, soft-reset) as hex with a
                human label.
    • [rx]    — every line GRBL writes back (ok / error:N / ALARM:N /
                [MSG:...] / setting responses / welcome banner). Status
                reports (5 Hz polling) are skipped to keep the file
                readable.
    • [event] — internal milestones (port opened, stream started,
                stream completed, stream stopped + reason).
    • [error] — exceptions and worker-side failures.

- New menu item: **Help → Reveal Run Log in Finder**. Opens Finder at
  the active log file when a session is running, otherwise at the logs
  folder so you can pick a previous run.
