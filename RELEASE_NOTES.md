- Preview Toolpaths now reports progress smoothly instead of jumping
  in big steps and stalling. Per-bit progress is broken into:
    • residual computation (sub-step 0–10 %)
    • offset rings (10–50 %, **now emits a tick per ring** instead of
      sitting silent through dozens of rings — this was the long
      "stuck at 25 %" gap the user saw on big designs)
    • chain ordering (50–65 %)
    • thin-residual cleanup (65–80 %, **now emits per residual piece**)
    • toolpath optimisation (80–92 %)
    • swept-area accounting (92–100 %)
- Thin-residual cleanup is now multi-threaded across residual pieces.
  AI-generated designs typically produce 10–50 disconnected residual
  regions; the per-piece work (rasterise → skeletonise → extract
  chains → simplify) is independent and the heavy compute steps drop
  into numpy / skimage / shapely C code that releases the GIL, so
  threading delivers real wall-clock speedup. Capped at 4 worker
  threads to avoid oversubscribing the user's machine.
