- **Plug toolpath is now derived from the real pocket cavity.** The
  plug used to be cut to the ideal design — so wherever a bit couldn't
  fully reach the pocket (a sub-bit nose tip, a tight concave corner),
  the matching plug feature stood proud and hit unmilled pocket wood.
  The plug side is now generated from the pocket's actual swept cavity:
  the plug feature is trimmed to exactly what the pocket can receive.
  On the Two-Dachshunds test the plug feature went from standing proud
  in several spots to **0.00 mm² of interference** — it now seats fully.

- **Rest-machining seam overlap — no more inter-bit slivers.** Each
  smaller bit's residual now extends one full bit-diameter back INTO
  the previous (larger) bit's already-cleared zone before its offset
  rings are computed. Without that overlap the smaller bit's first
  ring sat one radius inside the larger bit's scalloped seam, leaving
  thin uncut triangles right at the bit-to-bit boundary. Letting the
  smaller bit re-trace a diameter-wide band of already-milled (flat-
  bottomed) wood is harmless and closes the seam completely. On the
  Two-Dachshunds pocket this dropped total uncut area to **2.0 mm²
  (0.03%)** — and that residual is now ONLY the features genuinely
  narrower than the smallest bit, which no bit can physically cut.

- **Direction arrows stay a constant size when you zoom.** The
  toolpath direction chevrons were scene geometry — zooming in blew
  them up until they read as cut paths. They're now screen-fixed
  (constant pixel size at every zoom level), so they stay small and
  unmistakably arrow-like no matter how far you zoom in.
