- **Finished tab now reflects the actual cut.** Previously the
  Finished preview was driven by a morphological-opening estimate of
  the design — it modelled the smallest finishing bit's reach but
  didn't change when you toggled roughing bits, the V-bit, or the
  step-over, so it looked like nothing more than a wood-grain styling
  of the SVG.

  It's now built from the **real pocket toolpaths**: the visible plug
  area is the union of every pocket bit's actual swept footprint,
  clipped to the design and shrunk by xy_tolerance. Change the bit
  lineup, re-Preview, and the finished preview changes with it —
  because it's measured off the chains the machine will really cut.

  And the spots the bits genuinely can't reach — features narrower
  than the smallest bit, sharp concave corners, the V-bit shrink
  zone — are now **hatched in red** directly on the preview, with a
  caption reporting the total shortfall area ("⚠ red = N mm² the bits
  can't cut"). You can see exactly where the finished piece will
  deviate from the drawing before you cut a single millimetre of wood.
