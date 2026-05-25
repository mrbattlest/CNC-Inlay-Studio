- **Fixed progressive UI lag during long cuts.** Two compounding
  issues were burying the main thread under work:

  1. The per-Connect-session run log was opened with
     ``buffering=1`` — line-buffered, so every G-code line and every
     GRBL status report fsync'd to disk. During dense streaming the
     worker was doing hundreds of fsync calls per second, which
     stalled the worker thread, which backed up the cross-thread
     ``streamProgress`` signal queue, which is what you felt as
     "increasingly unresponsive UI". The log is now block-buffered
     (8 KB) with a daemon thread that flushes every 2 seconds —
     **150× faster** in a benchmark (756K writes/sec vs ~5K). Crash
     safety drops from "the last line" to "the last ~2 s of log".

  2. ``streamProgress`` was emitted on every ``_stream_tick`` —
     66 Hz, every 15 ms. The main-thread queue couldn't fall behind
     forever without growing. Throttled to ≤ 12 emits/sec (every
     80 ms) OR whenever the cursor jumps ≥ 4 lines, whichever comes
     first. The scrub bar is still perfectly smooth at this rate;
     the queue stops backing up.

  No more "the app is fine for a minute then gets sluggish then
  freezes" pattern on long cuts. If you still see UI lag during a
  cut, shift-drag the canvas and send me a snippet — there might be
  a third culprit I haven't found.
