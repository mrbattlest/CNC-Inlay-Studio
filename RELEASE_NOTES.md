- Run log now records GRBL status reports under a new `[status]` tag
  (previously filtered out as 5 Hz noise). Position-tracking bugs
  are impossible to diagnose without the underlying status frames,
  and the `[status]` tag means they're easy to grep in or out as
  needed.
- New `[event] suspicious wpos jump: …` line written whenever two
  consecutive WPos values move more than 25 mm between status
  reports — faster than any reasonable cut feed and a strong signal
  that WCO has gone briefly stale or GRBL is reporting inconsistent
  frames. When the purple cone in the preview jumps unexpectedly,
  these markers point straight at the offending status pair.
- The purple position cone now uses the diameter of the bit GRBL is
  CURRENTLY cutting with, looked up via the streamer's tool-change
  map — not the finishing-endmill diameter. Multi-bit jobs (e.g. a
  V-bit wall pass after the main endmill clearance) used to draw
  the cone sized for the endmill while the V-bit was actually in
  the spindle, making the indicator visually wrong-sized during
  V-bit cuts.
