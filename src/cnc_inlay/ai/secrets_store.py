"""Plaintext on-disk storage for the user's Gemini API key + model preference.

Honest about what this is: a `secrets.json` file in the app's config directory
with mode 0600 (user read/write only). On a single-user macOS install this is
the same effective security as AES-with-a-hardcoded-key — anyone with disk
access can read either — but without the Keychain auth-prompt friction the
user wanted to avoid.

On first run, the previous Keychain-stored key (if any) is migrated into the
plaintext file via the `security` CLI, then deleted from the Keychain.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from cnc_inlay.core.units import load_settings, save_settings

_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "CNC Inlay Studio"
_SECRETS_PATH = _CONFIG_DIR / "secrets.json"

# Legacy Keychain identifiers used before the switch to plaintext storage.
_LEGACY_SERVICE = "cnc-inlay"
_LEGACY_USERNAME = "gemini-api-key"

DEFAULT_MODEL = "gemini-2.5-flash-image"

_BROKEN_MODELS = {
    "imagen-3.0-generate-002",        # wrong API surface (needs generate_images)
    "gemini-2.5-flash-image-preview", # retired alias — replaced by gemini-2.5-flash-image
}


# ---------- file IO ----------


def _read_secrets() -> dict:
    if not _SECRETS_PATH.exists():
        return {}
    try:
        return json.loads(_SECRETS_PATH.read_text())
    except Exception:
        return {}


def _write_secrets(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _SECRETS_PATH.write_text(json.dumps(data, indent=2))
    # Owner-only — anyone else on a multi-user box can't read it.
    try:
        os.chmod(_SECRETS_PATH, 0o600)
    except Exception:
        pass


# ---------- keychain migration (one-time) ----------


def _migrate_from_keychain() -> str | None:
    """Pull the Gemini key out of the macOS Keychain via the `security` CLI,
    write it to the plaintext store, and delete the Keychain entry. Returns
    the migrated key (or None if there was nothing to migrate)."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _LEGACY_SERVICE,
             "-a", _LEGACY_USERNAME, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    key = result.stdout.strip()
    if not key:
        return None
    # Persist to file, then forget the Keychain copy.
    data = _read_secrets()
    data["gemini_api_key"] = key
    _write_secrets(data)
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", _LEGACY_SERVICE,
             "-a", _LEGACY_USERNAME],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass
    return key


# ---------- public API ----------


def get_api_key() -> str | None:
    data = _read_secrets()
    key = data.get("gemini_api_key")
    if key:
        return key.strip()
    # First call after upgrading? Migrate from Keychain if it's there.
    migrated = _migrate_from_keychain()
    return migrated.strip() if migrated else None


def set_api_key(key: str) -> None:
    """Store ``key`` in the plaintext file. Empty string clears it."""
    data = _read_secrets()
    if not key.strip():
        data.pop("gemini_api_key", None)
    else:
        data["gemini_api_key"] = key.strip()
    _write_secrets(data)


def has_api_key() -> bool:
    return bool(get_api_key())


def get_model() -> str:
    data = load_settings()
    saved = data.get("gemini_model", DEFAULT_MODEL)
    # Self-heal previously-broken stored choices (Imagen, retired -preview alias).
    if saved in _BROKEN_MODELS:
        return DEFAULT_MODEL
    return saved


def set_model(model: str) -> None:
    data = load_settings()
    data["gemini_model"] = model
    save_settings(data)
