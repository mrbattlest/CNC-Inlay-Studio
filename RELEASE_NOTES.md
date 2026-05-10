- V-bit lateral-offset math now respects the bit's cone-to-shank
  transition. The cone formula ``d × tan(half_angle)`` keeps growing
  the cutting diameter forever in pure math, but a real V-bit's
  cutting cone has a finite height — once the cone has widened to
  match the shank diameter, the bit becomes a cylindrical shank
  that doesn't widen further. Past that depth, the lateral cutting
  edge is fixed at ``shank_radius``, not ``d × tan(half_angle)``.

  Verified cone heights (from Amana / IDC Woodcraft / Whiteside
  datasheets):
    • 30° V-bit, 1/4″ shank → 11.85 mm
    • 30° V-bit, 1/8″ shank → 5.92 mm
    • 60° V-bit, 1/4″ shank → 5.50 mm
    • 90° V-bit, 1/4″ shank → 3.18 mm

  ``vbit_lateral_reach_mm`` now caps at ``shank_radius`` once the
  cone math would exceed it, and the pocket / plug pipelines pass
  ``vbit.shank_mm`` through so the cap is enforced in real cuts.
- Most existing inlays (≤ ~5 mm pockets with 30°/60° V-bits and
  1/4″ shanks) sit comfortably below the transition, so this fix
  doesn't change their toolpaths. It matters for deeper pockets,
  steeper angles, or smaller shanks where the bit's straight-shank
  region would otherwise be incorrectly modeled as still-widening
  cone.
