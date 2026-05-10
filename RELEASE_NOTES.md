- **Critical fix:** the V-bit wall pass was running on the wrong
  side of the plug edge. Both pocket and plug used `polygon.buffer(
  -lateral_reach)` to compute the V-bit's centerline path, which
  is correct for the pocket (centerline inside the cleared cavity)
  but wrong for the plug — it put the centerline INSIDE the plug
  feature (in solid plug-wood), so the V-bit ground into the plug
  material from the inside.
- The plug V-bit centerline is now offset OUTWARD by `lateral_reach`
  (`polygon.buffer(+lateral_reach)`), so it traces a path in the
  cleared complement just outside the plug boundary. The cone's
  flank then sweeps the wedge of waste wood OUTSIDE the plug,
  leaving the plug with an **outward-sloping** wall that mates with
  the pocket's inward-sloping wall after the stock is flipped face-
  down for insertion.
- Also re-enables the v1.1.9 air-cutting-skip optimisation on the
  plug side (it was disabled in v1.1.15 because the broken
  centerline placement made it dangerous). With the centerline now
  correctly in air, the skip is safe again — early passes truly
  cut nothing and skipping them saves real machine time.
- Adds a 1/4" shank, 3/4" diameter, 2-flute solid surfacing bit to
  the My Bits catalog (alongside the existing 3-flute insert and
  4-flute solid options).
