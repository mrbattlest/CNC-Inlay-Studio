- **Bit-swept-area overlay toggle.** A new "▦ Swept areas" button
  appears on the Pocket and Plug top-down (2D) previews after you
  click Preview. Toggle it on to see each bit's *actual swept
  footprint* — translucent coloured fills sized to the bit diameter
  — instead of just chain centerlines. Lets you verify visually that
  adjacent bits actually overlap (no gap between e.g. the 1/4" and
  the 2 mm) and that no bit's edge runs past the design boundary,
  without having to read centerline geometry and guess. The fills
  use the same per-bit legend colours as the chains.

- **Pre-cut design preflight.** Preview now runs a medial-axis
  feature-width pass against the smallest flat endmill in your
  lineup. Any design feature whose *local* width is below the
  smallest bit's diameter is flagged in the sidebar's recommendation
  panel with: total uncuttable area, count of hot-spots, and the top
  5 hot-spots' centre coordinates + areas + max widths. Now you find
  out about sub-bit features (and the matching plug points that
  won't seat) BEFORE you cut, not after.

  Critically, the preflight only flags features that are *genuinely*
  too narrow (skeleton-restricted) — it doesn't count the legitimate
  boundary strip every design has near its wall, which the bit's
  flute clears via the outermost offset ring. So when the preflight
  says "X mm² will stay uncut" that's the real uncut area.

- **Checkbox indicators are now visible.** The Fusion-on-dark default
  for QCheckBox / QRadioButton was nearly invisible against the panel
  background. They now get a 1.5 px amber border (unchecked) and
  filled amber (checked), so you can see at a glance which options
  are on. Same treatment for the GroupBox-collapse boxes.
