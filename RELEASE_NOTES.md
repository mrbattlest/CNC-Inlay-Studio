- **Critical follow-up to v1.1.12:** the cached toolpath restored
  on project open didn't honour pipeline bugfixes between releases.
  Cache signatures hashed inputs (design / params / materials / bits)
  but not the app version, so a cache produced by buggy v1.1.11 was
  cleanly restored by fixed v1.1.12 — same inputs, same hash, no
  invalidation. Users who never clicked Preview after the v1.1.12
  update would keep streaming the stale (still-cuts-through-the-
  letter) plug G-code.
- The cache signature now includes ``cnc_inlay.__version__``, so
  any future pipeline change (bugfix or otherwise) auto-invalidates
  every prior cache. Updating the app forces a Preview re-run on
  next open.
- When a saved cache is rejected (because of an app update or any
  changed input), the user now gets an explicit dialog telling them
  to click Preview Toolpaths before Run on Machine — instead of the
  silent status-bar nudge that v1.1.12 used.
