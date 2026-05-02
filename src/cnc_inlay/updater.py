"""Cloudflare R2-backed update checker.

A small JSON manifest hosted on a public R2 bucket describes the latest
release; the app fetches it, compares ``cnc_inlay.__version__`` against the
manifest's ``version``, and offers an "Update Available" dialog with the
release notes plus a button to download the .dmg.

Manifest schema (see make-release.sh which writes it)::

    {
      "version": "0.1.1",
      "release_date": "2026-05-02",
      "release_notes": "Markdown-formatted release notes...",
      "dmg_url": "https://pub-xxx.r2.dev/cnc-inlay-studio/CNC-Inlay-Studio-0.1.1.dmg"
    }

Why R2 instead of GitHub Releases:
- The source repo is private; the GitHub Releases API requires auth on
  private repos and we don't want to bake an auth token into the app.
- Cloudflare R2 has free egress, so DMG downloads from however many users
  cost nothing.
- A single static manifest URL is the simplest possible auth-free contract.

Does NOT auto-replace the .app bundle. The downloaded .dmg is opened in
the system browser; the user drags the new app over the old one in
/Applications, then quits + reopens. Atomic in-place replacement of the
running .app on macOS is brittle (Gatekeeper, signature verification,
NSWorkspace caching); the manual drag is what every Mac user already knows.
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


# --- Configure to point at your manifest URL --------------------------------
# The manifest lives at:
#     {R2_PUBLIC_URL}/{R2_PREFIX}/manifest.json
# Bake-in time: change R2_PUBLIC_URL if you move buckets or switch to a
# custom domain (e.g. https://updates.tapawingo.com).
R2_PUBLIC_URL = "https://pub-2f6bb3641a7848fdb6d18a515f004ea6.r2.dev"
R2_PREFIX = "cnc-inlay-studio"
# ----------------------------------------------------------------------------

MANIFEST_URL = f"{R2_PUBLIC_URL}/{R2_PREFIX}/manifest.json"

_USER_AGENT = "CNC-Inlay-Studio-Updater"
_NETWORK_TIMEOUT_SEC = 10
_SETTINGS_KEY_SKIP_VERSION = "updater_skipped_version"
_SETTINGS_KEY_AUTO_CHECK = "updater_auto_check"


@dataclass
class ReleaseInfo:
    version: str           # e.g. "0.2.0"
    name: str              # release title (defaults to "v{version}")
    notes: str             # release notes body (markdown)
    dmg_url: str | None    # direct download URL for the .dmg
    html_url: str          # fallback URL (same as dmg_url for R2)


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


# ------------------------------------------------------------- manifest fetch

def fetch_latest_release(
    manifest_url: str = MANIFEST_URL,
    timeout: float = _NETWORK_TIMEOUT_SEC,
) -> ReleaseInfo | None:
    """GET the R2-hosted manifest.json and parse it. Returns None on any
    network / parse error (e.g. the bucket isn't reachable or the manifest
    isn't published yet for a brand-new install).
    """
    req = urllib.request.Request(
        manifest_url,
        headers={
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None

    version = (payload.get("version") or "").strip()
    if not version:
        return None
    dmg_url = payload.get("dmg_url") or None
    return ReleaseInfo(
        version=version,
        name=payload.get("name") or f"v{version}",
        notes=payload.get("release_notes") or "",
        dmg_url=dmg_url,
        html_url=dmg_url or "",
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
