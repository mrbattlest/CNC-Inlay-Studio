- Fixes the "Apple could not verify is free of malware" error that
  v1.1.0 hit on first launch. Root cause: only the .dmg was stapled
  with Apple's notarisation ticket, not the .app inside it — so when
  users drag-and-dropped the app to Applications, the ticket stayed
  with the .dmg and macOS had to fetch a replacement from Apple
  online. If that fetch failed, Gatekeeper blocked the launch.
  v1.1.1 staples both the .dmg AND the .app, so first-launch works
  offline.
- Fixes the in-app auto-updater so it doesn't strip codesign data
  when installing future versions. Previously it ran `xattr -cr`
  on the new .app to clear macOS quarantine flags, which also
  stripped notarisation metadata and corrupted the signature
  ("CNC Inlay Studio is damaged and can't be opened"). Now it
  only clears the quarantine xattr, leaving the seal intact, and
  uses `ditto` instead of `cp -R` to preserve all bundle metadata.

Note: if you're updating from v1.1.0, please download v1.1.1 manually
from the Updates dialog's Download button (don't rely on auto-update
this once — the v1.1.0 updater still has the bug). After v1.1.1,
auto-update will work correctly.
