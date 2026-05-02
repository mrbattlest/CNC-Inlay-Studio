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
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
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
_SETTINGS_KEY_REOPEN_PROJECT = "updater_reopen_project_path"


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


def set_reopen_project_path(path: str | None) -> None:
    """Record a project path the next launch should re-open. Used by the
    auto-installer to restore session across the update restart."""
    data = load_settings()
    if path is None:
        data.pop(_SETTINGS_KEY_REOPEN_PROJECT, None)
    else:
        data[_SETTINGS_KEY_REOPEN_PROJECT] = str(path)
    save_settings(data)


def take_reopen_project_path() -> str | None:
    """One-shot read: return the saved reopen path and clear it. Called by
    the new app on launch so a future relaunch doesn't keep popping the
    same project."""
    data = load_settings()
    path = data.pop(_SETTINGS_KEY_REOPEN_PROJECT, None)
    save_settings(data)
    return path


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


# ----------------------------------------------------- in-app download + install

def get_installed_bundle_path() -> Path | None:
    """Path to the currently-running .app bundle, or None if we're running
    from source (dev mode). The bundled exe lives at
    ``<bundle>.app/Contents/MacOS/<exe>``, so the bundle is two parents up.
    """
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    # Walk up looking for the .app suffix — handles both standard layout and
    # quirks where exe path is symlinked.
    for parent in [exe.parent.parent.parent, *exe.parents]:
        if parent.suffix == ".app":
            return parent
    return None


def download_update(
    url: str,
    dest_dir: Path | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    chunk_size: int = 256 * 1024,
    timeout: float = 30.0,
) -> Path:
    """Download ``url`` to a temp file, calling ``progress_cb(pct, status)``
    every chunk. Returns the local Path of the downloaded file.

    Raises on any network / I/O error so the caller can show an error dialog.
    """
    if dest_dir is None:
        dest_dir = Path(tempfile.gettempdir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".dmg",
        prefix="cnc-inlay-update-",
        dir=str(dest_dir),
    )
    os.close(fd)
    dest = Path(tmp_path)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total_bytes = 0
        try:
            total_bytes = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total_bytes = 0
        downloaded = 0
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if progress_cb is not None:
                    if total_bytes > 0:
                        pct = downloaded / total_bytes * 100
                        progress_cb(
                            pct,
                            f"Downloading… {downloaded / 1_048_576:.0f} MB "
                            f"of {total_bytes / 1_048_576:.0f} MB",
                        )
                    else:
                        progress_cb(
                            -1,
                            f"Downloading… {downloaded / 1_048_576:.0f} MB",
                        )
    return dest


def install_update_and_relaunch(dmg_path: Path) -> None:
    """Spawn a detached helper that waits for THIS process to exit, mounts
    the DMG, swaps the .app bundle, and relaunches the new version. Then
    quit the app so the helper can do its work.

    The helper is a generated bash script in $TMPDIR; it logs to a file
    next to itself so failures can be diagnosed after the fact.
    """
    bundle = get_installed_bundle_path()
    if bundle is None:
        raise RuntimeError(
            "Auto-install only works in the bundled .app — run the .dmg "
            "manually if you launched from source."
        )
    pid = os.getpid()
    fd, helper_path = tempfile.mkstemp(
        suffix=".sh", prefix="cnc-inlay-installer-"
    )
    os.close(fd)
    log_path = helper_path + ".log"
    helper_src = _build_installer_script(
        parent_pid=pid,
        dmg_path=str(dmg_path),
        bundle_path=str(bundle),
        log_path=log_path,
    )
    Path(helper_path).write_text(helper_src)
    os.chmod(helper_path, 0o755)
    # Detach the helper: new session, no controlling tty, both stdio dropped.
    # If we don't fully detach, killing the parent (us) cascades to the helper.
    subprocess.Popen(
        ["/bin/bash", helper_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _build_installer_script(
    *, parent_pid: int, dmg_path: str, bundle_path: str, log_path: str,
) -> str:
    """Generate the bash helper that does the swap + relaunch.

    Strategy:
      1. Wait for the parent (running app) to exit.
      2. Mount the downloaded DMG with hdiutil (-nobrowse so Finder doesn't
         pop the volume).
      3. Find the new .app inside the mount.
      4. rsync (or cp -R) the new bundle over the existing one in /Applications.
         Atomic-ish: we move the old aside first, rename new in, then delete old.
      5. Strip the quarantine xattr so Gatekeeper doesn't re-prompt.
      6. Detach the DMG.
      7. ``open`` the new .app to launch it as a fresh process.
      8. Self-delete and clean up the downloaded .dmg.
    """
    # Use shlex to safely embed the paths — they may contain spaces.
    return f"""#!/bin/bash
# Auto-installer for CNC Inlay Studio update.
# Generated at {Path(__file__).name} runtime; safe to delete after run.

set -u
PARENT_PID={parent_pid}
DMG={shlex.quote(dmg_path)}
BUNDLE={shlex.quote(bundle_path)}
LOG={shlex.quote(log_path)}

exec >>"$LOG" 2>&1
echo "=== installer started $(date) pid=$$"
echo "  parent_pid=$PARENT_PID"
echo "  dmg=$DMG"
echo "  bundle=$BUNDLE"

# 1. Wait for the running app to exit.
echo "waiting for parent to exit…"
for i in $(seq 1 60); do
    kill -0 "$PARENT_PID" 2>/dev/null || break
    sleep 0.5
done
if kill -0 "$PARENT_PID" 2>/dev/null; then
    echo "parent still alive after 30s — aborting"
    exit 1
fi
echo "parent gone; proceeding"

# 2. Mount the DMG.
MOUNT_POINT=$(hdiutil attach -nobrowse -noverify -noautoopen "$DMG" 2>>"$LOG" \\
    | tail -1 | awk -F'\\t' '{{print $NF}}')
if [ -z "$MOUNT_POINT" ] || [ ! -d "$MOUNT_POINT" ]; then
    echo "hdiutil attach failed; mount_point=$MOUNT_POINT"
    exit 1
fi
echo "mounted at $MOUNT_POINT"

# 3. Find the .app inside the mount.
NEW_APP=$(find "$MOUNT_POINT" -maxdepth 2 -name "*.app" -type d | head -1)
if [ -z "$NEW_APP" ]; then
    echo "no .app found inside DMG"
    hdiutil detach "$MOUNT_POINT" -quiet || true
    exit 1
fi
echo "new app: $NEW_APP"

# 4. Atomic-ish swap. Move old to a tmp name, copy new in, delete old tmp.
BACKUP="${{BUNDLE}}.old-$$"
echo "backing up old bundle to $BACKUP"
mv "$BUNDLE" "$BACKUP" || {{ echo "could not move old bundle aside"; hdiutil detach "$MOUNT_POINT" -quiet; exit 1; }}
echo "copying new bundle into place"
cp -R "$NEW_APP" "$BUNDLE" || {{
    echo "copy failed — restoring old bundle"
    rm -rf "$BUNDLE" 2>/dev/null
    mv "$BACKUP" "$BUNDLE"
    hdiutil detach "$MOUNT_POINT" -quiet
    exit 1
}}
rm -rf "$BACKUP"
echo "swap complete"

# 5. Clear the quarantine attribute so Gatekeeper doesn't re-prompt.
xattr -cr "$BUNDLE" 2>>"$LOG" || true

# 6. Detach the DMG.
hdiutil detach "$MOUNT_POINT" -quiet || true

# 7. Launch the new version.
echo "launching $BUNDLE"
open "$BUNDLE"

# 8. Clean up.
rm -f "$DMG"
echo "=== installer done $(date)"
# Self-delete (last thing we do).
( sleep 1; rm -f "$0" ) &
"""


def download_update_async(
    url: str,
    on_progress: Callable[[float, str], None],
    on_done: Callable[[Path], None],
    on_error: Callable[[str], None],
) -> None:
    """Background-download wrapper. Callbacks fire from the worker thread;
    the caller (UI) must marshal back to the main thread before touching
    widgets — same pattern the check_for_update_async path uses."""
    def _run():
        try:
            path = download_update(url, progress_cb=on_progress)
        except Exception as exc:
            try:
                on_error(f"{type(exc).__name__}: {exc}")
            except Exception:
                pass
            return
        try:
            on_done(path)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
