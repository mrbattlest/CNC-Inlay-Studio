- **Safety-switch touch-off recovery.** The Recovery Wizard now has a
  first tab ("Safety switches") built for hobbyist CNCs with hard-stop
  switches at the +X / +Y / +Z extremes of travel (Lunyee, Sainsmart,
  etc.).

  If you always set your work zero in the same way (same stock fixture,
  same origin convention), each switch trips at a constant work-coord
  position — a permanent physical reference, independent of GRBL
  homing. Calibrate once: when work zero is correct, jog each axis to
  its switch, note the WPos, type those three numbers into the wizard,
  click Save.

  After a USB drop or power blip: open the wizard, jog +X until the
  switch trips, click **Restore X from +X switch** → the app sends
  ``G21 G10 L20 P1 X<saved_x>``, pinning the current bit position to
  the saved switch coord. Jog away. Repeat for Y, then Z. Each axis is
  restored independently — no need to commit to a full sequence at
  once. After all three, your work zero is exactly where it was.

  No `$H` homing required, no limit switches in GRBL config, no
  hardware install — just the switches the Lunyee already ships with.
