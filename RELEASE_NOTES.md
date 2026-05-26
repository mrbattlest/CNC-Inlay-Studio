- **Cached toolpaths load without prompting.** The cache signature
  used to include the app version, so every release silently
  invalidated every user's cached toolpaths and the next project open
  popped a modal asking you to recompute. With the dozen-releases-
  in-a-week recent cadence, that was a modal on every project open —
  exactly the friction the cache exists to avoid. The signature no
  longer includes the version; opening a project that has cached
  toolpaths now just shows them, instantly, no dialog.

  Genuine changes (different params, edited design, changed bit
  selection) still invalidate the signature — but instead of refusing
  to load the stale cache, the app loads it anyway with a status-bar
  notice ("cached toolpaths shown — click Preview Toolpaths to
  refresh if anything has changed"). Run-on-Machine confirms once
  before streaming if the cache was stale, so you can't accidentally
  cut something stale. Otherwise the cache just works.
