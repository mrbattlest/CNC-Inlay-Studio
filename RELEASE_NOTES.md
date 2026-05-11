- Fixes Help → About CNC Inlay Studio doing nothing. The About
  action used Qt's ``AboutRole``, which auto-promotes the item to
  the bold macOS application menu — but PySide6 6.x sometimes
  routes those clicks through the system about-dialog handler
  (which expects version metadata from Info.plist) instead of
  firing our ``triggered`` signal, so the click looked
  unresponsive. About now uses ``NoRole`` like every other Help
  item; it lives only under the Help menu and reliably opens the
  custom dialog.
