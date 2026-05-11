## v1.2.0 — Proper V-bit inlay toolpaths (medial-axis V-carve)

The V-bit pass has been fully rewritten to use the canonical
medial-axis V-carve algorithm (per F-Engrave's reference open-source
implementation and Vectric's V-Carve Inlay strategy). Earlier
releases traced closed loops along the design boundary at fixed Z —
which barely cut wood and never produced the actual prismatic
frustum that V-bit inlay relies on. Now:

- **Bit tip follows the medial axis** of the design polygon (the
  skeleton — the locus of points equidistant from the boundary).
  The bit's UPPER edge traces the design boundary at the surface,
  not the tip.

- **Z varies per point**: ``Z = -r/tan(half_angle) + flat_depth``,
  clipped to ``[-flat_depth, 0]``. Wide regions reach the flat
  floor; narrow regions are V-cut at the maximum depth their local
  width allows. The cone-to-shank transition still caps the
  effective depth for steep bits.

- **Pocket allowance** (new param, default 0.1 mm) — small wall
  slack on the pocket side so the plug doesn't bind on a tight
  cone-angle match. Pocket only.

- **Surface clearance** (new param, default 2.5 mm) — Z offset that
  shifts the entire plug pass deeper so the plug stock stands proud
  of the pocket surface after pressing. Plane/sand flush after
  gluing to expose a sharp inlay edge. Plug only.

- **Mating geometry**: both halves are prismatic frustums of the
  same V-angle. Wedge fit comes from cone angles matching, not
  from offset tricks. Plug uses MIRRORED design with NO allowance.

- **Pass-depth split** still works on the new variable-Z chains:
  each pass clips Z to a deeper ceiling, progressively shaving the
  V-profile from surface to floor.

Two new sidebar controls under the Wall finish dropdown: **Pocket
allowance** (mm) and **Surface clearance** (mm). Persisted in the
.inlay project file.

**For existing projects**: the cache will auto-invalidate on first
open (version-folded signature from v1.1.13), and Preview will
regenerate the V-bit chains using the new algorithm.
