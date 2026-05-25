- **Long-cut responsiveness, round two.** v1.2.17 fixed the run-log
  fsync flood and throttled the streamProgress signal. There was a
  third culprit hiding behind those: the 3D bit-position cone was
  creating **32 fresh OpenGL line items** on the Canvas3D every
  status report (5 Hz). Each item added/removed triggers a full
  paintGL of the scene, and a multi-bit dachshund 3D scene has
  thousands of toolpath line items — a single repaint isn't cheap.
  Multiply by 32 items × 5 Hz × hours of cutting and the main thread
  drowns in scene-graph churn.

  Two fixes:

  1. **One cached cone item, updated in place.** The 32 segments are
     now packed into a single ``GLLinePlotItem`` (``mode='lines'``)
     created once, then ``setData(pos=…)``'d on every status report.
     Benchmark: 100 cone updates dropped from churning 3200 items to
     **1.27 ms total** — 78K updates/sec, no item creation, no
     scene-graph thrash.

  2. **Skip the hidden canvas.** The 2D and 3D views of each side
     live in a QStackedWidget so only one is visible at a time. The
     other was getting status updates anyway — wasted work,
     especially the expensive 3D one. Now ``_on_machine_status_changed``
     only pushes to the canvas the operator is actually looking at;
     when they toggle 2D/3D, the next status report (≤200 ms later)
     refreshes the newly-visible view.

  If the app still gets sluggish during a long cut after this, please
  shift-drag the canvas and send me a snippet — I'll dig further.
