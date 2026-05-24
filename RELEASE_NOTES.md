- **Witness-mark divot is now clearly announced + safely placed.**
  v1.2.14 shipped it default-on but failed to communicate it: the
  Two-Dachshunds pocket sent the bit to an "upper right" spot you
  weren't expecting and started to plunge — easy to read as a runaway
  cut, which it wasn't. Three fixes:

  - **Sidebar checkbox** (under the V-bit toggle): *"Cut witness-mark
    divot at start (for recovery re-zero)"*. Default ON, but visible
    and one click to disable per-project.
  - **Stock-aware placement.** The divot is now nominally placed 5 mm
    past the design's top-right corner AND clamped to stay at least
    5 mm inside the stock edge. On the Two-Dachshunds project this
    moves the divot from (108, 44.5) — 0.5 mm from the stock edge,
    nearly off the wood — to a safe (105, 40). When the stock is too
    small for the ideal placement the divot pulls in further (but
    never inside the design).
  - **Visible on the canvas.** The 2 D Pocket and Plug previews now
    draw an amber crosshair + ring at the divot's exact location, so
    you can SEE where the first cut will land before clicking Run.
    The recommendation panel also lists the per-side coordinates
    explicitly and tells you which checkbox to untick to disable.

  Plus the existing Recovery Wizard → Witness tab keeps working — it
  now reads the same smart-placement formula so the coordinates the
  wizard shows always match the divot that was actually cut.
