- **Critical V-bit / tool-change cut-quality fix.** When a section
  began after a tool change (typical V-bit-after-endmill flow), the
  emitter assumed the bit was already at safe Z because the previous
  section ended with a lift. After M0, though, the operator
  re-zeroes Z by jogging the new bit's tip down to the wood surface
  — so when the streamer resumes, the bit is sitting at work-Z = 0
  with the spindle spinning up. The very first move (G0 X Y to the
  start of the first chain) then dragged the spinning bit
  laterally **across the wood at surface level**, gouging a slanted
  cut into the workpiece on the way to its proper start position.
- Each section now emits an unconditional ``G0 Z<safe_z>`` lift
  immediately after the M3 spindle-on + 3-second ramp-up dwell,
  before any X/Y motion. This guarantees the bit is clear of the
  wood before traversing to the first chain. The same-Z optimisation
  that skips lift+rapid+plunge between adjacent passes within a
  section is unaffected — only the very first move of each section
  unconditionally lifts.
