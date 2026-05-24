- **Witness-mark divot at the start of every job.** Each program now
  cuts a small 3 mm-wide, 1 mm-deep divot in stock waste BEFORE the
  design — at a deterministic location (8 mm past the design's
  top-right corner). The divot acts as a precise physical re-zero
  reference: after a USB drop you can jog the bit-tip into the
  divot and the Recovery Wizard re-applies the original work-zero by
  setting the current position to the divot's known XYZ. The cut is
  ~1 second of overhead, uses the first bit (no tool change), and
  lives entirely in waste so it never interferes with the design.
  Enabled by default. Disable per-project by clearing
  ``witness_mark_enabled`` in the project file if needed.

- **Settings → Recover work zero…** — a new wizard with three
  recovery paths in tabs:

  1. **Homing + restore WCO** — for machines with limit switches.
     Soft-reset → unlock → ``$H`` → re-apply the WCO that the
     checkpoint saved during the last run. Best accuracy. The
     checkpoint now saves the **work-coordinate offset** on every
     status tick (alongside WPos), so this restore is exact.

  2. **Witness divot** — jog the bit-tip into the divot the program
     cut, pick which side you were running (Pocket / Plug), click
     Restore. Wizard sends ``G21 G10 L20 P1 X<wx> Y<wy> Z<-depth>``.
     Sub-0.1 mm with care, no hardware required.

  3. **Design feature** — for when there's no divot and no limit
     switches. Type the design coordinates of any visible feature on
     the partly-cut work (corner, centre, intersection), jog the
     bit-tip exactly onto that physical spot, click Restore. Wizard
     applies the matching G10 L20 line so the current bit-tip
     position maps to those design coords. ~0.1–0.2 mm depending on
     how cleanly you can pin the feature visually.
