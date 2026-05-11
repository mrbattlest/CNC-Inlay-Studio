- Fixes Help → Check for Updates doing nothing when the app is
  already on the latest version. The updater check runs on a
  non-Qt threading.Thread and signals back to the main thread; the
  default AutoConnection sometimes fell back to DirectConnection
  and tried to open the result dialog on the worker thread, where
  QMessageBox silently fails. The signal connection now uses an
  explicit ``Qt.QueuedConnection`` so the dialog always opens on
  the main thread. You'll now see the proper "You're up to date"
  dialog (or "Update available", or "Check failed") on every
  click.
- Updater also no longer swallows callback errors silently — they
  print to stderr so future failures are visible in Console.app.
