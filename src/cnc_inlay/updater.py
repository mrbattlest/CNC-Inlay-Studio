"""GitHub-Releases-backed update checker.

The app's version (``cnc_inlay.__version__``) is compared against the latest
GitHub release's tag. If a newer one exists, the user is shown a dialog with
the release notes and a button to download the .dmg.

Why GitHub Releases instead of a hand-rolled manifest:
- One artefact (the .dmg) and one release page covers everything — no separate
  manifest.json to keep in sync.
- Release notes are written in GitHub's UI and rendered as markdown there;
  we just pass the body through to the dialog.
- ``GET /repos/.../releases/latest`` returns assets + body + tag in one call
  with no auth (unauthenticated GitHub API allows 60 requests/hr per IP, far
  more than a desktop app needs).

Does NOT auto-replace the .app bundle. The downloaded .dmg is opened in
Finder and the user drags the new app over the old one in /Applications,
then quits + reopens. Atomic in-place replacement of the running .app on
macOS is brittle (Gatekeeper, signature verification, NSWorkspace caching);
the manual drag is what every Mac user already knows.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Callable

import cnc_inlay
from cnc_inlay.core.units import load_settings, save_settings


# --- Configure these to point at your GitHub repo ----------------------------
# When you create the repo, change these two strings. The updater silently
# does nothing when the configured repo doesn't exist (404) so leaving the
# placeholders in place doesn't break the app for offline / first-run users.
GITHUB_OWNER = "mrbattlest"
GITHUB_REPO = "CNC-Inlay-Studio"
# -----------------------------------------------------------------------------

_API_LATEST_URL = (
    "https://api.github.com/repos/{owner}/{repo}/releases/latest"
)
_USER_AGENT = "CNC-Inlay-Studio-Updater"
_NETWORK_TIMEOUT_SEC = 10
_DMG_ASSET_RE = re.compile(r"\.dmg$", re.IGNORECASE)
_SETTINGS_KEY_SKIP_VERSION = "updater_skipped_version"
_SETTINGS_KEY_AUTO_CHECK = "updater_auto_check"


@dataclass
class ReleaseInfo:
    version: str           # e.g. "0.2.0" (tag with leading 'v' stripped)
    name: str              # release title
    notes: str             # release body (markdown)
    dmg_url: str | None    # direct download for the .dmg, or None if no .dmg asset
    html_url: str          # GitHub release page (fallback if no dmg)


# ---------------------------------------------------------------------- semver

def _parse_version(text: str) -> tuple[int, ...]:
    """Parse a version string into a tuple suitable for comparison.

    Accepts ``"v1.2.3"``, ``"1.2.3"``, ``"1.2.3-beta1"``. Pre-release
    suffixes are dropped (we treat ``1.2.3`` and ``1.2.3-beta1`` as equal).
    """
    text = text.strip()
    if text.lower().startswith("v"):
        text = text[1:]
    # Drop any pre-release / build metadata after the numeric core.
    core = re.match(r"\d+(?:\.\d+)*", text)
    if not core:
        return (0,)
    return tuple(int(p) for p in core.group(0).split("."))


def is_newer(remote: str, local: str) -> bool:
    """True when ``remote`` parses to a strictly higher version than ``local``."""
    return _parse_version(remote) > _parse_version(local)


# ------------------------------------------------------------------- GitHub API

def fetch_latest_release(
    owner: str = GITHUB_OWNER,
    repo: str = GITHUB_REPO,
    timeout: float = _NETWORK_TIMEOUT_SEC,
) -> ReleaseInfo | None:
    """Hit the GitHub Releases API for the latest non-draft, non-prerelease
    release. Returns None on any network / parse error or 404 (e.g. the repo
    has no releases yet or the configured repo doesn't exist).
    """
    url = _API_LATEST_URL.format(owner=owner, repo=repo)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None

    tag = payload.get("tag_name") or ""
    if not tag:
        return None
    version = tag[1:] if tag.lower().startswith("v") else tag

    dmg_url: str | None = None
    for asset in payload.get("assets") or []:
        if _DMG_ASSET_RE.search(asset.get("name") or ""):
            dmg_url = asset.get("browser_download_url")
            break

    return ReleaseInfo(
        version=version,
        name=payload.get("name") or tag,
        notes=payload.get("body") or "",
        dmg_url=dmg_url,
        html_url=payload.get("html_url") or "",
    )


# ----------------------------------------------------------------- preferences

def get_skipped_version() -> str | None:
    """User clicked 'Skip This Version' — don't notify until something newer."""
    return load_settings().get(_SETTINGS_KEY_SKIP_VERSION)


def set_skipped_version(version: str | None) -> None:
    data = load_settings()
    if version is None:
        data.pop(_SETTINGS_KEY_SKIP_VERSION, None)
    else:
        data[_SETTINGS_KEY_SKIP_VERSION] = version
    save_settings(data)


def get_auto_check_enabled() -> bool:
    """Default: auto-check on launch. User can opt out via the dialog."""
    return bool(load_settings().get(_SETTINGS_KEY_AUTO_CHECK, True))


def set_auto_check_enabled(enabled: bool) -> None:
    data = load_settings()
    data[_SETTINGS_KEY_AUTO_CHECK] = bool(enabled)
    save_settings(data)


# ------------------------------------------------------------ async check API

def check_for_update_async(on_done: Callable[["UpdateCheckResult"], None]) -> None:
    """Run the network call on a worker thread so launch isn't blocked.

    ``on_done`` is invoked from the worker thread; the caller is responsible
    for marshalling back to the Qt main thread (e.g. via ``QMetaObject`` or a
    ``Signal``).
    """
    def _run():
        result = check_for_update()
        try:
            on_done(result)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


@dataclass
class UpdateCheckResult:
    """Outcome of an update check, suitable for driving UI directly."""
    current_version: str
    release: ReleaseInfo | None    # None on network failure or no releases yet
    is_update_available: bool
    is_skipped_by_user: bool       # True iff user previously chose Skip This Version


def check_for_update() -> UpdateCheckResult:
    """Synchronous check — fetch + compare + apply skip preference."""
    current = cnc_inlay.__version__
    release = fetch_latest_release()
    if release is None:
        return UpdateCheckResult(
            current_version=current,
            release=None,
            is_update_available=False,
            is_skipped_by_user=False,
        )
    available = is_newer(release.version, current)
    skipped = available and get_skipped_version() == release.version
    return UpdateCheckResult(
        current_version=current,
        release=release,
        is_update_available=available,
        is_skipped_by_user=skipped,
    )
