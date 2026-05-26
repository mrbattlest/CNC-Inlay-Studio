- **Auto-simplify heavily-vectorised designs.** Raster-traced SVGs
  (e.g. logos run through a vectoriser) commonly have **tens of
  thousands** of micro-segment vertices along every curve. Each
  shapely buffer / offset op in the rest-machining loop is roughly
  O(vertex_count), and the pipeline does dozens of those per bit
  across multiple bits — so a 27 K-vertex design like the
  florida_gator_logo would hang on the 1.5 mm bit's offset-rings
  phase at ~30 % progress and never finish in reasonable time.

  Designs over 5 000 vertices now get a one-shot Douglas-Peucker
  simplification at 0.05 mm tolerance (1/4 of the smallest typical
  bit-radius, well below anything a finishing cut could resolve)
  before the pipeline starts. On the gator-logo it cuts the polygon
  from 27 707 vertices to 1 698 in 14 ms — area preserved to within
  0.01 %, visual fidelity intact, and the buffer ops the pipeline
  then runs end up 6×-50× faster.

  No change for lightly-traced or hand-drawn SVGs — they stay
  untouched.
