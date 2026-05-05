- First signed + notarised release. macOS Gatekeeper now lets the app
  launch with the standard "downloaded from internet — open?" prompt
  instead of the "cannot be checked for malware" warning that earlier
  builds triggered. The right-click-Open or `xattr -cr` workarounds
  are no longer needed.
- Internal: Qt's bundled developer tools (Designer / Assistant /
  Linguist) pruned from the .app bundle — they were never invoked by
  the app and were nested .app bundles that complicated codesigning.
