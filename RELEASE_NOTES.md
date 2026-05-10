- The On Machine scrub bar's "Show Pocket" / "Show Plug" toggle
  buttons are gone — the active timeline now auto-syncs to the
  preview tab you're looking at. Switch the main-area preview from
  Pocket → Plug (or vice versa) and the scrub bar follows along
  with the matching G-code program. Run-from-cursor always operates
  on the side that's currently visible, so there's no longer a
  way to accidentally pick a line on one side while looking at the
  other.
- Switching to the Finished or Surfacing tabs doesn't disturb the
  scrub bar — it keeps showing whichever Pocket/Plug timeline was
  last active, since those tabs don't correspond to a streamable
  G-code program.
