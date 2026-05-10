- **Critical V-bit geometry fix.** The V-bit centerline path was
  incorrectly inset from the design boundary by ``lateral_reach``
  (= depth × tan(half_angle)). The intent was "the cone's outer
  edge at the surface kisses the boundary at full depth", but the
  consequence was that the cone sat entirely inside the cleared
  cavity (or cleared complement on the plug) at every depth,
  removing essentially zero wood — only tangentially kissing the
  wall at z = 0. Users observed this as "the V-bit barely touched
  the wood".

  Per the standard V-bit inlay technique (Vectric / Whiteside /
  Amana), **the V-bit's TIP follows the design boundary at full
  depth, with no inset**. The cone's flank then extends
  ``lateral_reach`` outward at the surface, sweeping a wedge of
  wood from the wall and leaving a clean angled surface from
  ``(boundary + lateral_reach, surface)`` down to
  ``(boundary, floor)``. Whether that wedge is on the outside of
  the boundary (pocket — wood is outside) or the inside (plug —
  wood is inside) is determined automatically by which side has
  wood; the cone simply doesn't cut where there isn't anything to
  cut.

  This fix applies to both pocket and plug V-bit passes. The
  ``is_plug`` parameter is retained for API compatibility but no
  longer affects the centerline.

- The v1.1.9 air-cutting-skip optimisation has also been removed —
  with the corrected centerline, the cone reaches into wood at
  every depth, so there are no air-cutting passes to skip. The full
  surface-down pass schedule is exactly what's wanted now: each
  pass shaves a thin, clean slice of the wall, progressively
  deepening the bevel.
