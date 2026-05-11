- Plug-side V-bit pass now bevels to **pocket depth only**, not
  `pocket_depth + glue_gap`. The flat-endmill clearance on the plug
  side correctly cuts a glue-gap deeper than the pocket depth so
  the assembled inlay has room for adhesive. The V-bit bevel,
  though, only needs to span the wall the plug actually contacts
  inside the pocket — i.e. the pocket's depth. Cutting the bevel
  the extra glue-gap deeper either spent V-bit time below the
  effective mating zone (wasted) or, worse, undercut the plug's
  shoulder so the visible top surface of the assembled inlay had a
  small slack gap rather than a tight wedge. Pocket-side V-bit
  bevel was already at pocket depth; both sides now use the same
  depth, which is what the mating math requires.
