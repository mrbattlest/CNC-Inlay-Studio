- **GRBL silence detector + position-preserving recovery.** When the
  CNC controller stops responding mid-cut (USB serial wedges, EMI
  drops the connection, or the controller hangs), the app now
  detects the dead silence within 5 seconds and:

  1. Halts the streamer immediately so it stops shipping G-code into
     a black hole.
  2. Logs the event to the run log (`[event] GRBL silence detected
     after Ns — last known WPos (x, y, z)`).
  3. Saves the bit's most recent work-coordinate position into the
     resume-checkpoint file on disk.
  4. Pops a recovery banner with explicit power-cycle instructions.

- After you power-cycle the controller and reconnect, the app
  notices the saved "silenced session" checkpoint and offers to
  restore the work-coordinate origin to the bit's last known
  position. Since the X/Y/Z stepper motors hold position when GRBL
  freezes (only the spindle keeps spinning), as long as you don't
  manually crank or jog the bit, this lets you skip the imprecise
  manual re-zero step entirely. Click Yes, then Run-from-cursor at
  the saved line number, and the cut continues from exactly where
  it stopped.

- The crash-recovery dialog now distinguishes silence-induced
  failures from generic aborts and surfaces the saved position
  prominently. If you didn't move the bit by hand after the freeze,
  Resume → Connect → restore-position → Run-from-cursor is the full
  recovery path with zero re-alignment work.
