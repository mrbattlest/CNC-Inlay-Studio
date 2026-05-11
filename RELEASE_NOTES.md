## v1.2.2 — Revert the v1.2.0 V-bit rewrite; bigger bit-legend palette

**Reverted the v1.2.0 medial-axis V-carve.** That rewrite applied a
V-groove-engraving algorithm (bit tip follows the polygon's skeleton)
to filled-silhouette inlay — which on the PLUG side carved a V-trough
straight down the middle of the plug feature (the wood that's supposed
to STAY). The V-bit toolpath generation is back to the v1.1.30
boundary-following behaviour. It's imperfect (the known "thin bevel"
look) but it doesn't destroy the plug, and the "Skip pocket V-bit
(plug only)" checkbox from v1.1.27 is still there if you want it.

The Pocket allowance / Surface clearance controls introduced in
v1.2.0 are removed (the reverted code doesn't use them). Project
files that were saved with those values still load fine — the fields
are kept on the model, just unused.

**Bit legend / path-preview colours no longer repeat.** The palette
went from 5 colours to 10 hand-tuned, mutually distinct ones (amber,
sky, lavender, lime, pink, tangerine, teal, indigo, fuchsia, cool
grey). Designs with 11+ bits get additional colours generated
procedurally by golden-angle hue rotation — so colours stay
perceptually spread out and never collide, no matter how many bits
are in the cut sequence.

A proper V-bit inlay implementation tuned for silhouettes is still
on the roadmap; v1.2.0's approach was the wrong foundation and needed
to come out before it caused a ruined cut.
