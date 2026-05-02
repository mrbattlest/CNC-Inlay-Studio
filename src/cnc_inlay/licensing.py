"""Trial + license management for CNC Inlay Studio.

Three states the app can be in:

  * **TRIAL_ACTIVE** — within the 7-day trial window. Everything works.
  * **TRIAL_EXPIRED** — past day 7 with no license entered. The app still
    opens and the user can preview / edit / save .inlay projects, but the
    "value" features (Run on Machine, AI Generate) gate to a Buy dialog.
  * **LICENSED** — a valid Gumroad license key has been entered. Same as
    TRIAL_ACTIVE forever, no countdown shown.

Trial-state durability:
We store the trial-start timestamp in TWO independent locations so a casual
"uninstall + reinstall" doesn't reset the clock:

  1. ``~/Library/Application Support/CNC Inlay Studio/settings.json``
     (the existing settings file; survives `.app` reinstall)
  2. ``~/Library/Preferences/com.malcolmross.cnc-inlay-studio.plist``
     (a separate plist outside the obvious config dir, written via plistlib)

On launch we read both, take the EARLIEST start date, and write the same
date back to both. To reset the trial a user must find AND delete both —
different locations, different file types, neither in the obvious uninstall
spot. Gets us most of Keychain's protection with none of the "an unsigned
app wants Keychain access" UX horror.
"""
from __future__ import annotations

import json
import plistlib
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from cnc_inlay.core.units import load_settings, save_settings


# ---------- Configure once you have the Gumroad product set up --------------
# Gumroad's "permalink" — the slug at the end of your product URL. For
# https://yourname.gumroad.com/l/abc12 the permalink is "abc12". The Verify
# License API uses this to look the key up under your account. Until this is
# filled in, license verification will always fail (trial-only mode).
GUMROAD_PRODUCT_PERMALINK: str = "cncinlaystudio"
GUMROAD_PRODUCT_URL: str = "https://malcolmross.gumroad.com/l/cncinlaystudio"
# ---------------------------------------------------------------------------

TRIAL_DAYS = 7
TRIAL_NAG_FROM_DAY = 5  # status-bar countdown starts appearing this many days in
GUMROAD_VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"

_SETTINGS_KEY_TRIAL_STARTED_AT = "trial_started_at"   # unix timestamp
_SETTINGS_KEY_LICENSE_EMAIL = "license_email"
_SETTINGS_KEY_LICENSE_KEY = "license_key"
_SETTINGS_KEY_LICENSE_VERIFIED_AT = "license_verified_at"

_PLIST_PATH = (
    Path.home() / "Library" / "Preferences" /
    "com.malcolmross.cnc-inlay-studio.plist"
)
_PLIST_KEY_TRIAL_STARTED_AT = "trial_started_at"


class LicenseStatus(Enum):
    TRIAL_ACTIVE = "trial_active"
    TRIAL_EXPIRED = "trial_expired"
    LICENSED = "licensed"


@dataclass
class LicenseState:
    """Snapshot of the user's licensing state, suitable for driving UI."""
    status: LicenseStatus
    days_remaining_in_trial: int     # 0 once expired
    licensed_to_email: str | None    # only set when status == LICENSED
    license_key: str | None          # idem


# ---------------------------------------------------------------- plist layer

def _read_plist() -> dict[str, Any]:
    if not _PLIST_PATH.exists():
        return {}
    try:
        with open(_PLIST_PATH, "rb") as f:
            return plistlib.load(f) or {}
    except Exception:
        return {}


def _write_plist(data: dict[str, Any]) -> None:
    try:
        _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_PLIST_PATH, "wb") as f:
            plistlib.dump(data, f)
    except Exception:
        pass


# ---------------------------------------------------------------- trial state

def _get_or_create_trial_start() -> float:
    """Return the trial-start unix timestamp. Initialises on first call.

    Reads from BOTH the JSON settings file and the plist; if either has a
    value, we use the earliest (so deleting just one doesn't reset the
    clock). If neither has one, we initialise with ``now`` and write to
    both so future reinstalls see the same date.
    """
    settings = load_settings()
    json_ts = settings.get(_SETTINGS_KEY_TRIAL_STARTED_AT)
    plist = _read_plist()
    plist_ts = plist.get(_PLIST_KEY_TRIAL_STARTED_AT)

    candidates = [
        float(t) for t in (json_ts, plist_ts) if t is not None
    ]
    if candidates:
        ts = min(candidates)
    else:
        ts = time.time()

    # Always re-sync both stores to the canonical (earliest) value, so the
    # next launch sees the same date no matter which file is consulted first.
    if json_ts != ts:
        settings[_SETTINGS_KEY_TRIAL_STARTED_AT] = ts
        save_settings(settings)
    if plist_ts != ts:
        plist[_PLIST_KEY_TRIAL_STARTED_AT] = ts
        _write_plist(plist)
    return ts


def _trial_days_remaining() -> int:
    """Days left in the trial window. 0 when expired."""
    started = _get_or_create_trial_start()
    elapsed_days = (time.time() - started) / 86400.0
    return max(0, int(TRIAL_DAYS - elapsed_days))


# ---------------------------------------------------------------- license storage

def _get_stored_license() -> tuple[str | None, str | None]:
    """Return (email, key) for the locally-cached license, or (None, None)."""
    s = load_settings()
    email = s.get(_SETTINGS_KEY_LICENSE_EMAIL)
    key = s.get(_SETTINGS_KEY_LICENSE_KEY)
    return (email if isinstance(email, str) else None,
            key if isinstance(key, str) else None)


def _store_license(email: str, key: str) -> None:
    s = load_settings()
    s[_SETTINGS_KEY_LICENSE_EMAIL] = email
    s[_SETTINGS_KEY_LICENSE_KEY] = key
    s[_SETTINGS_KEY_LICENSE_VERIFIED_AT] = time.time()
    save_settings(s)


def clear_license() -> None:
    """Remove a previously-stored license (used by 'Deactivate' button)."""
    s = load_settings()
    for k in (
        _SETTINGS_KEY_LICENSE_EMAIL,
        _SETTINGS_KEY_LICENSE_KEY,
        _SETTINGS_KEY_LICENSE_VERIFIED_AT,
    ):
        s.pop(k, None)
    save_settings(s)


# ---------------------------------------------------------------- gumroad verify

@dataclass
class VerifyResult:
    """Outcome of a Gumroad /v2/licenses/verify call."""
    valid: bool
    email: str | None = None
    error: str | None = None     # human-readable; populated on invalid/error
    refunded: bool = False
    chargebacked: bool = False
    disputed: bool = False


def verify_license_with_gumroad(
    license_key: str, *, timeout: float = 10.0,
) -> VerifyResult:
    """Hit Gumroad's public verify endpoint. Returns ``VerifyResult.valid``
    True only when the key exists, is bound to ``GUMROAD_PRODUCT_PERMALINK``,
    and hasn't been refunded / charged-back / disputed.

    Network failures and configuration-not-set are reported as ``valid=False``
    with a descriptive ``error`` so the caller can show a useful message.
    """
    if (not GUMROAD_PRODUCT_PERMALINK
            or GUMROAD_PRODUCT_PERMALINK == "REPLACE_ME"):
        return VerifyResult(
            valid=False,
            error="License verification isn't configured yet. Contact the developer.",
        )
    if not license_key.strip():
        return VerifyResult(valid=False, error="Enter a license key.")

    body = urllib.parse.urlencode({
        "product_permalink": GUMROAD_PRODUCT_PERMALINK,
        "license_key": license_key.strip(),
        "increment_uses_count": "false",
    }).encode()
    req = urllib.request.Request(
        GUMROAD_VERIFY_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return VerifyResult(valid=False, error="License key not recognised.")
        return VerifyResult(valid=False, error=f"Verification server error ({e.code}).")
    except urllib.error.URLError:
        return VerifyResult(valid=False, error="Couldn't reach the licence server. Check your internet connection.")
    except Exception as exc:
        return VerifyResult(valid=False, error=f"Verification failed: {exc}")

    if not payload.get("success"):
        return VerifyResult(
            valid=False,
            error=payload.get("message") or "License key not recognised.",
        )
    purchase = payload.get("purchase") or {}
    refunded = bool(purchase.get("refunded"))
    chargebacked = bool(purchase.get("chargebacked"))
    disputed = bool(purchase.get("disputed"))
    email = purchase.get("email")
    if refunded or chargebacked or disputed:
        return VerifyResult(
            valid=False, email=email,
            refunded=refunded, chargebacked=chargebacked, disputed=disputed,
            error="This license has been refunded or disputed.",
        )
    return VerifyResult(valid=True, email=email)


def activate_license(email: str, key: str) -> VerifyResult:
    """Verify with Gumroad; on success, persist locally for offline use."""
    result = verify_license_with_gumroad(key)
    if result.valid:
        _store_license(email.strip(), key.strip())
    return result


# ---------------------------------------------------------------- public API

def get_license_state() -> LicenseState:
    """Single entry point UI code uses to figure out what to show + gate."""
    email, key = _get_stored_license()
    if email and key:
        return LicenseState(
            status=LicenseStatus.LICENSED,
            days_remaining_in_trial=0,
            licensed_to_email=email,
            license_key=key,
        )
    days = _trial_days_remaining()
    if days > 0:
        return LicenseState(
            status=LicenseStatus.TRIAL_ACTIVE,
            days_remaining_in_trial=days,
            licensed_to_email=None,
            license_key=None,
        )
    return LicenseState(
        status=LicenseStatus.TRIAL_EXPIRED,
        days_remaining_in_trial=0,
        licensed_to_email=None,
        license_key=None,
    )


def is_unlocked() -> bool:
    """``True`` while the user is allowed to access the gated features
    (Run on Machine, AI Generate). False after the trial expires until they
    enter a license. Use this to gate any feature behind the paywall."""
    state = get_license_state()
    return state.status in (LicenseStatus.TRIAL_ACTIVE, LicenseStatus.LICENSED)


def status_bar_text() -> str | None:
    """The text to show in the status-bar trial pill, or None if no pill
    needed. Only appears in the last few days of the trial + after expiry,
    and never when licensed."""
    state = get_license_state()
    if state.status == LicenseStatus.LICENSED:
        return None
    if state.status == LicenseStatus.TRIAL_EXPIRED:
        return "Trial expired — Buy License"
    if state.days_remaining_in_trial <= (TRIAL_DAYS - TRIAL_NAG_FROM_DAY):
        d = state.days_remaining_in_trial
        return f"{d} day{'s' if d != 1 else ''} left in trial — Buy License"
    return None
