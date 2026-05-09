- Fixes designs being horizontally squished when reopening a project
  that had a 90° rotation applied. Older project files (≤v7) saved
  the rotated width × height but didn't record the rotation itself,
  so on reload the SVG was parsed in its natural orientation and then
  rescaled to the rotated W×H — forcing a landscape design into a
  portrait box and squishing it. v8 project files now store
  `design_rotation_quarters` explicitly so rotation round-trips
  cleanly. Existing v7 files get auto-detected on first open: when
  the saved aspect is the opposite orientation of the SVG's native
  aspect, one quarter-turn is inferred and applied.
- After opening a v7 project that gets auto-rotated, save it once to
  upgrade it to v8 and the rotation will be persisted explicitly
  going forward.
