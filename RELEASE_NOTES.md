- **Send GRBL command** is now a dropdown-driven dialog instead of a
  raw text prompt. Pick from a list of common commands (M3 spindle on
  with RPM picker, M5 spindle off, $X unlock, $H home, $$ show
  settings, $# show offsets, M0 pause, G4 dwell with seconds picker,
  G0 / G1 with per-axis tickboxes + value spinboxes, G54, M9 coolant
  off, etc.) and the dialog surfaces only the parameters that command
  needs. A live "Will send:" preview shows the literal G-code line
  that's about to go to GRBL, so there are no surprises. Destructive
  commands ($H, G0, G1) get a one-tap confirmation gate before they
  go out.
- For anything not in the dropdown there's still a "(Custom raw
  command…)" entry that takes free text, same as before.
- New **Settings → Work zero → Set custom X / Y / Z…** menu item.
  Three checkbox-gated spinboxes (3-decimal-place precision) let you
  set the work-coordinate origin to arbitrary values rather than
  always zero. Unticked axes keep their existing offset, so a Z-only
  re-zero doesn't disturb XY. Sends ``G21 G10 L20 P1 X… Y… Z…`` and
  honours the same connected / paused-or-not-streaming gates as the
  existing zero actions.
