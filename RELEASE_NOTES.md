- **Finished tab now shows how the plug actually seats into the
  pocket — colour-coded.** The view overlays the real pocket cavity
  and the real plug feature (both built from the computed toolpaths)
  and marks the mismatch:
  - **Green** — the pocket cavity is wider than the plug. This is a
    *tolerable* glue gap; the plug still drops in and the glue fills
    the space.
  - **Red** — the plug runs *past* the pocket wall. This is an
    *intolerable* interference: the plug is physically oversize there
    and the two halves cannot seat together.

  A **legend** in the top-left names both colours with their total
  areas. The caption gives a one-line verdict ("✓ plug seats" or
  "⛔ N mm² interference — plug WILL NOT seat").

- **Hard alert when the inlay won't fit.** If any red interference is
  detected, Preview now raises a modal warning ("Inlay will not fit")
  and puts a ⛔ line at the top of the sidebar recommendations — so
  you can't accidentally cut a design whose plug won't physically
  assemble.

- **Zoom controls on the Finished tab.** A +/−/⌂ zoom toolbar now sits
  in the top-right corner, exactly like the Pocket and Plug previews.
  Scroll-wheel zoom, drag-to-pan, and double-click-to-reset also work.
