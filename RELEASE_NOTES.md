- **Set Z = 0 (and XY / All) now actually fires inside a tool-change
  pause** — fixes the "click does nothing, have to abort the run"
  symptom. v1.1.10 added a permission check that allows the action
  while paused, but the secondary confirm dialog the action then
  popped could appear behind the active tool-change modal and steal
  no focus, making the click look unresponsive. In the tool-change
  flow we now skip that secondary confirmation: clicking Set Z = 0
  inside the tool-change dialog sends the G10 L20 P1 immediately and
  surfaces a green status toast confirming the new zero. The full
  confirmation dialog still appears when re-zeroing outside the
  tool-change flow (idle or manual feed-hold), so accidental
  re-zeros are still gated.
