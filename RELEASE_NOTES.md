- Plug-side finer bits no longer chase residual into the outer
  stock corners. The "priority region" that restricts smaller bits
  to the area near the design feature was previously sized by
  ``boundary_offset_mm`` (the plug-profile margin parameter,
  typically 15 mm). On stock that's only a few mm wider than the
  design, that 15 mm band reached all the way into the outer stock
  corners and forced the smallest bit (e.g. 1 mm) to spend extra
  passes polishing waste material — even though it's just air to
  the outside of the eventual plug.
- The priority band is now sized to the LARGEST roughing bit's
  diameter — geometrically that's the maximum residual width the
  largest bit can leave behind along the design boundary. Smaller
  bits clean up that band; the outer-corner stock waste is left
  with the largest bit's natural-radius residual (which gets
  discarded anyway when the plug is cut out).
