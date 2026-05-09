- Fixes "GRBL error 22 (feed rate undefined)" when using **Run from
  cursor** to start mid-program. The G-code emitter writes the F-word
  on the first G1 of each chain and relies on it being modal for the
  rest of that chain — so when Run-from-cursor jumped past the
  F-bearing line, GRBL had no modal feed rate set and aborted on
  the first cut move. The resume preamble now scans backward for the
  most recent F-word in the program and re-asserts it before any
  motion commands resume.
- This was particularly visible on V-bit cuts because the V-bit
  section often runs much slower than the endmills before it
  (e.g. 120 mm/min vs 600 mm/min) — the wrong-feed failure mode
  was unmissable.
