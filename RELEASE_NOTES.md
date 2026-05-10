- Fixes the **Set Z = 0** button being blocked during a tool-change
  pause. Setting Z = 0 after installing a new bit (paper-feeler
  test) is the entire reason the tool-change pause exists; the old
  refusal forced operators to abort the whole run, jog manually,
  then Run-from-cursor to recover. Set-zero now works whenever the
  job is paused (M0 tool change OR user-initiated feed-hold).
- Streamer now pauses itself the instant an M0 tool-change line is
  sent, instead of pumping more lines into GRBL's planner / serial
  buffer. With an aggressive streamer + a slow operator (paper-
  feeler Z zero takes a minute), the planner could fill and starve
  jog ($J=) commands of buffer space, making jogs feel
  unresponsive. Pausing the streamer on M0 keeps GRBL's serial RX
  clear so jogs always land cleanly during a tool change. Clicking
  ▶ Resume both un-pauses the streamer and clears GRBL's hold.
- (Coming next release: jog-during-manual-pause + auto-return-to-
  pause-position on Resume so you can pause mid-cut, jog away to
  inspect or blow chips, then Resume and have the bit lift, return,
  and plunge straight back to where it left off without cutting
  through wood on the way.)
