- **Resume-from-cursor now always rapids XY first, then plunges Z
  straight down — never both at once.** Previously, when you resumed
  mid-cut and the cursor landed on a line that specified all three
  axes (the typical case), the bit would move diagonally from safe Z
  to the target XYZ — ploughing through wood at an angle and ruining
  the piece. The streamer's resume preamble now scans the cursor
  region for the first absolute X / Y / Z values and inserts:
  1. A safety re-lift to safe Z (in case Z was jogged down during
     re-zero).
  2. A horizontal-only `G0 X<x> Y<y>` rapid at safe Z.
  3. A vertical-only `G1 Z<z> F100` plunge straight down at a
     conservative feed (slower than the cut feed, in case the bit has
     to plough through any residual at the cursor's depth).

  Then the original G-code at the cursor resumes — but the bit is
  already at the right position, so the first move is effectively a
  no-op. Verified with a unit test that asserts the rapid is
  horizontal-only AND precedes a vertical-only plunge.
