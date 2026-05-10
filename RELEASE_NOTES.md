- **Critical fix to v1.1.9's V-bit optimisation:** the air-cutting
  skip is correct on the pocket side but WRONG on the plug side,
  and v1.1.9 applied it to both. Result: the plug V-bit pass
  plunged straight to the first-contact depth on its first pass
  and tried to shear a thick wedge of plug-feature wood in one go,
  cutting too aggressively against the wall.

  Why the geometry differs:
    • **Pocket** — the V-bit centerline sits INSIDE the cleared
      cavity, in air. At intermediate depths the cone is fully
      surrounded by air; only at full depth does its surface-level
      edge reach the wood wall. Early passes genuinely cut air, so
      skipping them is safe.
    • **Plug** — the V-bit centerline sits INSIDE the plug-feature
      itself, in solid wood (the material that becomes the inlay).
      Every pass, from the shallowest, removes plug material to
      form the bevel. Skipping early passes means the first pass
      shears off too much in one go.

  v1.1.15 disables the optimisation on the plug side. Plug V-bit
  passes now step down from the surface in pass-depth increments
  the way they always did before v1.1.9. Pocket side still benefits
  from the air-cutting skip.
