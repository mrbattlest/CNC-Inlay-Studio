- Fixes Help → About and Help → Check for Updates being unresponsive.
  Qt's default ``MenuRole.TextHeuristicRole`` saw words like "Check",
  "Update", and "License" in the menu-item text and tried to auto-
  classify them, which on macOS could silently drop the menu item's
  ``triggered`` signal. All Help-menu actions and Settings →
  Preferences now have explicit menu roles so Qt leaves the
  signal alone:
    • About → ``AboutRole`` (auto-promotes to the bold app menu)
    • Preferences → ``PreferencesRole`` (auto-promotes to ⌘, slot)
    • Check for Updates / Reveal Run Log / Buy License /
      Enter License → ``NoRole`` (stay where placed, signals fire)

- File-open and file-save dialogs now remember the folder you last
  used and default there next time. Categories are kept separate
  (designs, projects, G-code), so bouncing between an SVG asset
  folder and an .inlay project folder doesn't make every dialog
  reopen in the wrong place. First-time users still land in
  ~/Documents.
