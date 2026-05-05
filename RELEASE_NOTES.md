- AI Generate dialog's **Detail** slider now has 5 stops instead of 3,
  so the slider behaves like an actual spectrum:
    • **Bold** — chunky stencil, no internal cutouts (any bit ≥6 mm)
    • **Simple** — clean stencil, 1–2 defining cutouts (3–4 mm endmills)
    • **Detailed** — paper-cut style, several cutouts (2 mm endmills) ← default
    • **Rich** — decorative paper-cut, many cutouts (1.5 mm endmills)
    • **Ornate** — scherenschnitte/wood-block, ornate cutouts
                   (1 mm endmills + V-bit pre-pass)
  Each stop is now labelled directly under its tick mark, so the
  available positions are visible at a glance and the active position
  is highlighted in orange. Picking the matching stop for your finest
  endmill gives the most usable result.
- The underlying Gemini prompt was tuned per stop — minimum-feature
  width steps from 8 % → 5 % → 3 % → 2 % → 1 % of the image width,
  the internal-cutout policy steps from "none" → "ornate", and the
  style anchor steps from "stencil" → "scherenschnitte / Beardsley".
