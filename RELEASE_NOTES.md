- V-bit wall pass now skips the air-cutting top passes. The previous
  flat endmill leaves a vertical-walled cavity inset by its radius;
  the V-bit's tip is so narrow that the first several pass-depth
  steps don't reach uncleared wood at all. Now the V-bit drops
  straight to its first-contact depth on the first pass, then steps
  down by the configured pass depth from there.

  First-contact math is per V-bit angle:
      contact_z = pocket_depth − (prev_bit_radius / tan(half_angle))

  Examples for a 3.5 mm pocket finished with a 0.5 mm endmill:
    • 30° V-bit: was 9 passes, now 3 (skips ~2.6 mm of air)
    • 60° V-bit: contact at full depth (no skipping needed)
    • 90° V-bit: same — wide enough that it cuts immediately

  The benefit grows with sharper V-bit angles, deeper pockets, and
  smaller finishing endmills.
