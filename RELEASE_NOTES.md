- **Tool-change pause now lets you jog and re-zero Z.** When a multi-bit
  job pauses for a bit swap, the controller no longer sends the `M0` to
  GRBL — sending it put GRBL into the *Hold* state, where jog (`$J=`) and
  Set Z = 0 (`G10`) are both rejected, forcing you to Abort and restart
  from the cursor. Instead the streamer parks itself just before the
  `M0`, GRBL drains its buffer and settles to *Idle* (the spindle is
  already off), so the Jog buttons and **Set Z = 0** work normally. Click
  **Resume cut** and it skips the `M0` and continues with the next bit's
  spindle-on — no Abort, no run-from-cursor needed.

- **Coarse bits no longer gouge thin features.** The medial-axis cleanup
  pass — which mops up the slivers a bit's offset rings can't reach — was
  running the bit down the centerline of features narrower than the bit
  itself (e.g. a 6.35 mm endmill down a 4 mm-wide letter stroke or a 2 mm
  border), cutting 1+ mm past the wall on each side and, on tight
  geometry, plowing a trench straight through the design. It now checks
  the local feature width at every point and only cuts where the bit
  actually fits inside the wall; where it doesn't fit, that material
  stays uncut (and shows up in the coverage report) instead of being
  destroyed. You can now safely experiment with a larger endmill as the
  finishing bit — it just won't touch features it physically can't handle.

- **Live bit-position indicator no longer flickers during a cut.** The
  spindle cone and coordinate readout were jumping to a slightly-ahead
  position and snapping back several times a second. The scrub bar was
  pulling its handle forward to track streaming progress and, as a side
  effect, repainting the cone at the *programmed* coordinates of the
  latest-sent G-code line — which runs ahead of where the bit physically
  is because of GRBL's planner + serial buffer. The cone is now driven
  solely by GRBL's status reports, so it tracks the real bit position
  smoothly.
