#!/usr/bin/env bash
#
# Cut a new release of CNC Inlay Studio:
#   1. Bumps __version__ in src/cnc_inlay/__init__.py and pyproject.toml
#   2. Builds the .app + .dmg via build_app.sh
#   3. Uploads the .dmg to Cloudflare R2 at:
#       tapawingo-app/cnc-inlay-studio/CNC-Inlay-Studio-<version>.dmg
#   4. Writes + uploads manifest.json describing the release
#
# Once step 4 lands, every running copy of the app picks up the new release
# on its next launch (auto-check) or via Help → Check for Updates….
#
# Requirements:
#   * uv, sips, iconutil, hdiutil       (already required by build_app.sh)
#   * wrangler                          (npm install -g wrangler  /  brew install wrangler)
#   * Xcode command-line tools          (provides codesign, notarytool, stapler)
#   * ~/.cloudflare/credentials  with two keys:
#       accountID: <your account id>
#       apiToken:  <Cloudflare API token with R2 Edit on tapawingo-app>
#   * ~/.apple/credentials  with four keys:
#       signingIdentity: Developer ID Application: <Name> (<TEAMID>)
#       appleId:         <apple developer email>
#       teamId:          <10-char Apple team id>
#       appPassword:     <app-specific password from appleid.apple.com>
#
# Usage:
#   ./make-release.sh <version> <release-notes-file>
#
#   <version>             e.g. 0.1.2  (no leading 'v')
#   <release-notes-file>  path to a markdown file with the release notes
#
# Example:
#   ./make-release.sh 0.1.2 RELEASE_NOTES.md
#
set -euo pipefail

# ────────────────────────────────────────────────────────────────────────────
# Config — change these only if you move buckets / paths.

R2_BUCKET="tapawingo-app"
R2_PREFIX="cnc-inlay-studio"
R2_PUBLIC_URL="https://pub-2f6bb3641a7848fdb6d18a515f004ea6.r2.dev"
CREDENTIALS_FILE="$HOME/.cloudflare/credentials"
APPLE_CRED_FILE="$HOME/.apple/credentials"

# ────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

step()  { echo; echo "═══ $1"; }
fail()  { echo "✗ $1" >&2; exit 1; }

# ── 0. Validate args & creds ──────────────────────────────────────────────
[[ $# -ge 1 ]] || fail "Usage: $0 <version> [release-notes-file]"
VERSION="$1"
NOTES_FILE="${2:-}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
    fail "Version must be MAJOR.MINOR.PATCH (e.g. 0.1.2). Got: $VERSION"
[[ -f "$CREDENTIALS_FILE" ]] || \
    fail "Missing $CREDENTIALS_FILE. Create it with 'accountID:' and 'apiToken:' lines."

CLOUDFLARE_ACCOUNT_ID=$(awk -F': *' '/^accountID:/{print $2}' "$CREDENTIALS_FILE")
CLOUDFLARE_API_TOKEN=$(awk  -F': *' '/^apiToken:/{print  $2}' "$CREDENTIALS_FILE")
[[ -n "$CLOUDFLARE_ACCOUNT_ID" && -n "$CLOUDFLARE_API_TOKEN" ]] || \
    fail "Could not parse accountID / apiToken from $CREDENTIALS_FILE."
export CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN

# Apple credentials (required for release builds — without notarisation,
# end users see Gatekeeper's malware warning).
[[ -f "$APPLE_CRED_FILE" ]] || \
    fail "Missing $APPLE_CRED_FILE. Create it with 'signingIdentity:', 'appleId:', 'teamId:', 'appPassword:' lines."
# sed (not awk) because signingIdentity's value contains a colon.
SIGNING_IDENTITY=$(sed -n 's/^signingIdentity: *//p' "$APPLE_CRED_FILE")
APPLE_ID=$(          sed -n 's/^appleId: *//p'         "$APPLE_CRED_FILE")
APPLE_TEAM_ID=$(     sed -n 's/^teamId: *//p'          "$APPLE_CRED_FILE")
APPLE_APP_PASSWORD=$(sed -n 's/^appPassword: *//p'     "$APPLE_CRED_FILE")
[[ -n "$SIGNING_IDENTITY" && -n "$APPLE_ID" && -n "$APPLE_TEAM_ID" && -n "$APPLE_APP_PASSWORD" ]] || \
    fail "Could not parse all four keys (signingIdentity, appleId, teamId, appPassword) from $APPLE_CRED_FILE."

# Read release notes (or empty body if none provided).
if [[ -n "$NOTES_FILE" ]]; then
    [[ -f "$NOTES_FILE" ]] || fail "Notes file not found: $NOTES_FILE"
    NOTES_BODY="$(cat "$NOTES_FILE")"
else
    NOTES_BODY=""
fi

# ── 1. Bump version in source ─────────────────────────────────────────────
step "Bumping __version__ to $VERSION"
# __init__.py
python3 - <<PYEOF
from pathlib import Path
p = Path("src/cnc_inlay/__init__.py")
p.write_text(f'__version__ = "$VERSION"\n')
print(f"  wrote {p}")
PYEOF
# pyproject.toml: change the very first 'version = "..."' line.
python3 - <<PYEOF
from pathlib import Path
p = Path("pyproject.toml")
out = []
done = False
for line in p.read_text().splitlines():
    if not done and line.lstrip().startswith("version = "):
        out.append(f'version = "$VERSION"')
        done = True
    else:
        out.append(line)
p.write_text("\n".join(out) + "\n")
print(f"  wrote {p}")
PYEOF

# ── 2. Build the .dmg ─────────────────────────────────────────────────────
step "Building .app + .dmg"
./build_app.sh
DMG_LOCAL="dist/CNC Inlay Studio.dmg"
[[ -f "$DMG_LOCAL" ]] || fail "Expected $DMG_LOCAL after build."
DMG_SIZE=$(du -h "$DMG_LOCAL" | awk '{print $1}')
echo "✓ DMG built: $DMG_SIZE"

# ── 2.5. Notarise + staple the DMG ─────────────────────────────────────────
# Submits the signed DMG to Apple's notary service, polls until done,
# then staples the resulting ticket so first-launch works offline.
# Without this step, Gatekeeper still shows the malware warning even
# when the bundle is correctly Developer-ID-signed.
#
# notarytool exits 0 even when the submission is rejected (status:
# Invalid), so we capture the output and inspect status ourselves —
# bailing out with the Apple log fetched if rejected.
step "Submitting DMG to Apple notary service (this can take 1–10 minutes)"
NOTARY_OUT=$(mktemp -t notary).log
trap "rm -f '$NOTARY_OUT'" EXIT
xcrun notarytool submit "$DMG_LOCAL" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" \
    --wait 2>&1 | tee "$NOTARY_OUT"

NOTARY_ID=$(grep -m1 '^  id:' "$NOTARY_OUT" | awk '{print $2}')
NOTARY_STATUS=$(grep -m1 '^  status:' "$NOTARY_OUT" | awk '{print $2}')

if [[ "$NOTARY_STATUS" != "Accepted" ]]; then
    echo
    echo "✗ Notarisation status: $NOTARY_STATUS (id $NOTARY_ID)"
    echo "  Fetching Apple's rejection log..."
    xcrun notarytool log "$NOTARY_ID" \
        --apple-id "$APPLE_ID" \
        --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_PASSWORD"
    fail "Notarisation rejected — see log above and fix before retrying."
fi
echo "✓ Notarisation accepted"

# Staple the .app FIRST (using the ticket Apple issued for the
# DMG submission — notarisation tickets are keyed by CDHash, so the
# .app inside is also notarised and stapler can fetch the ticket for
# it from CloudKit). Without an .app staple, dragging the .app out of
# the DMG to /Applications gives end users the "could not verify is
# free of malware" error if their offline CloudKit lookup fails.
step "Stapling notary ticket to .app bundle"
APP_BUNDLE_LOCAL="dist/CNC Inlay Studio.app"
xcrun stapler staple "$APP_BUNDLE_LOCAL"
xcrun stapler validate "$APP_BUNDLE_LOCAL"
echo "✓ Stapled .app"

# Rebuild the DMG so it contains the now-stapled .app. The original
# DMG was notarised, but its contents (the .app inside) had no embedded
# ticket. Re-creating the DMG embeds the staple so drag-to-install
# works offline. The new DMG file has a different hash, so we re-sign
# it and re-staple — Apple's ticket database recognises everything in
# the original submission, so stapling the new DMG also succeeds.
step "Rebuilding DMG with stapled .app"
rm -f "$DMG_LOCAL"
TMP_DMG_DIR="$(mktemp -d)"
trap "rm -rf '$TMP_DMG_DIR' '$NOTARY_OUT'" EXIT
cp -R "$APP_BUNDLE_LOCAL" "$TMP_DMG_DIR/"
ln -s /Applications "$TMP_DMG_DIR/Applications"
hdiutil create \
    -volname "CNC Inlay Studio" \
    -srcfolder "$TMP_DMG_DIR" \
    -ov -format UDZO \
    "$DMG_LOCAL" >/dev/null
codesign --force --timestamp --sign "$SIGNING_IDENTITY" "$DMG_LOCAL"
echo "✓ Rebuilt + signed DMG"

# Try to staple the rebuilt DMG too. The rebuilt DMG has a different
# hash than the one Apple notarised, so staple may fail with "Record
# not found" — that's fine. The .app inside is fully stapled, which is
# what matters for the drag-to-install flow we tell users to use. The
# DMG itself just needs to be signed (which it is) so macOS will mount
# it without complaint.
step "Attempting to staple the rebuilt DMG (best-effort)"
if xcrun stapler staple "$DMG_LOCAL" 2>&1 | tail -3; then
    echo "✓ Stapled DMG"
else
    echo "ⓘ DMG staple unavailable (Apple's ticket DB doesn't yet know"
    echo "  this rebuilt DMG's hash) — that's fine. The .app inside is"
    echo "  stapled, so drag-to-install works offline."
fi
DMG_SIZE=$(du -h "$DMG_LOCAL" | awk '{print $1}')
echo "✓ DMG ready ($DMG_SIZE)"

# ── 3. Upload .dmg to R2 (versioned + "latest" alias) ─────────────────────
# We upload two copies: the versioned one (for archival + the manifest's
# dmg_url field) and a "latest" alias at a stable URL. The Gumroad product
# page can link to the "latest" URL once and never need updating per
# release — buyers always download the most recent version on first
# install, then the in-app updater takes over from there.
DMG_KEY="$R2_PREFIX/CNC-Inlay-Studio-$VERSION.dmg"
DMG_PUBLIC_URL="$R2_PUBLIC_URL/$DMG_KEY"
LATEST_KEY="$R2_PREFIX/CNC-Inlay-Studio-latest.dmg"
LATEST_PUBLIC_URL="$R2_PUBLIC_URL/$LATEST_KEY"

step "Uploading .dmg to R2: $R2_BUCKET/$DMG_KEY"
wrangler r2 object put "$R2_BUCKET/$DMG_KEY" \
    --file "$DMG_LOCAL" \
    --content-type "application/x-apple-diskimage" \
    --remote 2>&1 | tail -3
echo "✓ Uploaded: $DMG_PUBLIC_URL"

step "Uploading 'latest' alias: $R2_BUCKET/$LATEST_KEY"
wrangler r2 object put "$R2_BUCKET/$LATEST_KEY" \
    --file "$DMG_LOCAL" \
    --content-type "application/x-apple-diskimage" \
    --remote 2>&1 | tail -3
echo "✓ Uploaded: $LATEST_PUBLIC_URL"

# ── 4. Build + upload manifest.json ───────────────────────────────────────
RELEASE_DATE="$(date +%Y-%m-%d)"
MANIFEST_LOCAL="$(mktemp -t cnc-inlay-manifest).json"
trap "rm -f '$MANIFEST_LOCAL'" EXIT
python3 - <<PYEOF > "$MANIFEST_LOCAL"
import json
print(json.dumps({
    "version": "$VERSION",
    "release_date": "$RELEASE_DATE",
    "release_notes": """$NOTES_BODY""",
    "dmg_url": "$DMG_PUBLIC_URL",
}, indent=2))
PYEOF
MANIFEST_KEY="$R2_PREFIX/manifest.json"
MANIFEST_URL="$R2_PUBLIC_URL/$MANIFEST_KEY"
step "Uploading manifest to R2: $R2_BUCKET/$MANIFEST_KEY"
wrangler r2 object put "$R2_BUCKET/$MANIFEST_KEY" \
    --file "$MANIFEST_LOCAL" \
    --content-type "application/json" \
    --remote 2>&1 | tail -3
echo "✓ Uploaded: $MANIFEST_URL"

# ── 5. Done ───────────────────────────────────────────────────────────────
step "Release v$VERSION complete"
echo "  Manifest:  $MANIFEST_URL"
echo "  Download:  $DMG_PUBLIC_URL"
echo
echo "  Verify with:"
echo "    curl -s $MANIFEST_URL | python3 -m json.tool"
echo
echo "  Don't forget to commit + push the version bump:"
echo "    git add src/cnc_inlay/__init__.py pyproject.toml"
echo "    git commit -m 'Bump to v$VERSION'"
echo "    git push origin main"
