- AI Generate dialog now has a **Detail** slider with three levels:
  Simple, Detailed, Intricate. Each picks a prompt tuned for a
  different finest-bit size:
    • **Simple** — chunky stencil, no internal cutouts, safe with any
      bit ≥3 mm.
    • **Detailed** (default) — paper-cut style with deliberate
      internal negative space (eyes, leaf separations, building
      windows). Tuned for 2–3 mm endmills.
    • **Intricate** — ornate scherenschnitte / wood-block-print
      style with rich internal cutouts and decorative flourishes.
      Tuned for 1–1.5 mm endmills (ideally with a V-bit pre-pass).
  The slider remembers your last setting across sessions.
- Reworked the underlying Gemini prompt template. The previous one
  forbade ALL internal detail and forced an 8 % thickness floor,
  which made every output look like a featureless blob. Each level
  now relaxes those constraints differently and adds a style anchor
  (paper-cut, scherenschnitte) that pushes the model toward
  recognizable, decorative results.
