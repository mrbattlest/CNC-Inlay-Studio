- **Critical fix:** plug toolpaths could draw a straight cut through
  the design feature on the largest roughing bit. The `merge_close_loops`
  optimisation joins two adjacent ring-chains into one continuous cut
  with a G1 connector at depth, on the assumption that the intervening
  material is already cleared. That's true on the pocket side, but on
  the **plug** side the cleared region is the *complement* — interrupted
  by the design feature itself. Two rings on opposite sides of a JMU
  letter (or a dachshund silhouette, etc.) could be within the merge's
  jump threshold (~16 mm for a 6.35 mm bit) and the merge would draw a
  G1 line straight through the letter, ruining the inlay.

  `merge_close_loops` now takes an `inside_region` polygon and refuses
  any connector whose straight segment leaves that region. The pipeline
  passes the cleared region to both merge calls, so cross-feature
  connectors are reliably rejected on every bit on every job.
