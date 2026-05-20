- **Pocket V-bit pass is now skipped by default.** Investigating the
  Two-Dachshunds project we found that running the V-bit on the pocket
  side requires shrinking the cleared region inward by the V-bit's
  lateral reach (L = depth × tan(half-angle)) so the cone has wall-wedge
  to carve. On designs with narrow features — letter strokes, animal
  ears, dachshund legs, anything thinner than ~2L — that inward shrink
  either eats the feature or rounds its sharp concave corners, and the
  matching plug-feature points then can't seat into the rounded
  notches. Shipping **Skip pocket V-bit (plug only) = ON** as the new
  default means the pocket-finishing endmill cuts right up to the
  actual design boundary with vertical walls; the plug still gets its
  full V-bit pass, so the visible joint is still a sharp tapered line
  (the plug's V-walls compress into the vertical pocket walls on first
  seating and self-shave to fit). Existing projects keep whatever value
  was saved; you can toggle the checkbox in the sidebar to opt back in
  per-project.

- **Area-feedback selection.** Shift-drag a rectangle on the Pocket or
  Plug top-down (2D) preview to capture a clipboard snippet for sending
  to me. The snippet includes the project path + content hash, app
  version, active tab, the selected area in work-coord mm, the local
  feature half-width inside the selection (= smallest bit-radius that
  can fit), and a per-bit summary of how many chains actually touch the
  region. A small dialog opens with a textbox for your observation and
  a Copy to Clipboard button. Paste it back to me with your comment and
  I can diagnose without having to re-load the project and triangulate
  which "neck" or "ear" you mean. 3D-view selection and the surfacing
  tab will come in a follow-up.
