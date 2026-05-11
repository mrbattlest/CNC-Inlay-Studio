## v1.2.3 — V-bit inlay walls actually carve now (not just "kiss")

The long-standing "the V-bit barely touches the wood" / "the pocket
comes out too large" problem is fixed. The root cause: the pocket's
flat-endmill clearance was cutting the cavity all the way to the
design line, so when the V-bit followed its path its cone reached the
cavity wall exactly tangentially — it just kissed the wall and removed
nothing (or, in the most recent versions, removed a wedge OUTSIDE the
design line, making the cavity ~1 mm too big and leaving a glue gap
against the plug).

**The corrected geometry** (worked through cross-section by
cross-section, and consistent with the F-Engrave reference
implementation):

- **Pocket**: when a V-bit is active, the flat-endmill clears the
  cavity to the design line **inset by L** (L = pocket_depth ×
  tan(half_angle), capped at the bit's shank radius). The V-bit then
  follows that same inset line at full depth; its cone — spanning ±L
  at the surface — sweeps the wedge of wall wood back out to the
  original design line. Final cavity: the design line at the surface
  (so the visible inlay matches your design exactly) and (design − L)
  at the floor, sloped at the bit angle.

- **Plug**: unchanged geometry — the endmill clears the complement
  (leaving the plug feature at full design size on the cleared floor)
  and the V-bit follows the full design boundary, its cone carving a
  wedge off the plug feature's top. The plug ends up (design − L) at
  the cut face and (design) at the cleared floor — which, after the
  flip, mates the pocket cavity exactly. Zero gap at the surface,
  self-centering wedge.

**Caveat — thin features**: shrinking the pocket region by L erases
design features narrower than 2L (e.g. a 1.8 mm dog collar with a 30°
bit at 3.5 mm depth → L ≈ 0.94 mm → 2L ≈ 1.9 mm). The plug-side V-bit
likewise erodes sub-2L features to slivers. For designs with very
thin features, enable **Skip pocket V-bit (plug only)** — that path
keeps the cavity walls vertical and avoids the shrink entirely.
A sharper V-bit (30° has a smaller L than 60°/90°) or a shallower
pocket also widens the safe-feature range.

**⚠ Validate on scrap first.** This geometry has been wrong more than
once. Cut a small test inlay on scrap wood, check the pocket and plug
mate flush with no visible gap, *then* commit to a real piece.
