#!/usr/bin/env bash
#
# Build CNC Inlay Studio as a native macOS .app + .dmg installer.
#
#   1. Generates assets/icon.icns from assets/icon.png (sips + iconutil).
#   2. Runs PyInstaller via uv to produce dist/CNC Inlay Studio.app.
#   3. Packages the .app into dist/CNC Inlay Studio.dmg using hdiutil.
#
# Requires: uv (already installed), and macOS-builtin sips, iconutil, hdiutil.
#
# Usage (from the project root):
#     ./build_app.sh
#

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

APP_NAME="CNC Inlay Studio"
BUNDLE_ID="com.malcolmross.cnc-inlay-studio"

SOURCE_PNG="assets/icon.png"
ICONSET_DIR="assets/icon.iconset"
ICNS="assets/icon.icns"

ENTRY_POINT="src/cnc_inlay/__main__.py"
DIST_DIR="dist"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
DMG_PATH="$DIST_DIR/$APP_NAME.dmg"

# ---------------------------------------------------------------------------

step() { echo; echo "═══ $1"; }
fail() { echo "✗ $1" >&2; exit 1; }

[[ -f "$SOURCE_PNG" ]] || fail "Place your icon at $SOURCE_PNG (PNG, 1024×1024 or larger) before running this script."

# ── 1. Build the .icns ────────────────────────────────────────────────────
step "Building $ICNS from $SOURCE_PNG"
rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

sips -z 16 16     "$SOURCE_PNG" --out "$ICONSET_DIR/icon_16x16.png"     >/dev/null
sips -z 32 32     "$SOURCE_PNG" --out "$ICONSET_DIR/icon_16x16@2x.png"  >/dev/null
sips -z 32 32     "$SOURCE_PNG" --out "$ICONSET_DIR/icon_32x32.png"     >/dev/null
sips -z 64 64     "$SOURCE_PNG" --out "$ICONSET_DIR/icon_32x32@2x.png"  >/dev/null
sips -z 128 128   "$SOURCE_PNG" --out "$ICONSET_DIR/icon_128x128.png"   >/dev/null
sips -z 256 256   "$SOURCE_PNG" --out "$ICONSET_DIR/icon_128x128@2x.png">/dev/null
sips -z 256 256   "$SOURCE_PNG" --out "$ICONSET_DIR/icon_256x256.png"   >/dev/null
sips -z 512 512   "$SOURCE_PNG" --out "$ICONSET_DIR/icon_256x256@2x.png">/dev/null
sips -z 512 512   "$SOURCE_PNG" --out "$ICONSET_DIR/icon_512x512.png"   >/dev/null
sips -z 1024 1024 "$SOURCE_PNG" --out "$ICONSET_DIR/icon_512x512@2x.png">/dev/null

iconutil -c icns "$ICONSET_DIR" -o "$ICNS"
echo "✓ Wrote $ICNS"

# ── 2. Make sure the dev deps are installed (pyinstaller) ─────────────────
step "Ensuring uv dependencies are in sync"
uv sync --group dev

# ── 3. Run PyInstaller ────────────────────────────────────────────────────
step "Building $APP_NAME.app with PyInstaller"
rm -rf build "$DIST_DIR/$APP_NAME.app" "$DIST_DIR/$APP_NAME"
uv run pyinstaller \
    --name "$APP_NAME" \
    --windowed \
    --icon "$ICNS" \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --add-data "src/cnc_inlay/presets/data:cnc_inlay/presets/data" \
    --collect-all "PySide6" \
    --collect-all "shiboken6" \
    --collect-all "pyqtgraph" \
    --collect-all "OpenGL" \
    --collect-all "scipy" \
    --collect-all "skimage" \
    --collect-all "shapely" \
    --collect-all "PIL" \
    --collect-all "svgelements" \
    --collect-all "pyclipper" \
    --collect-all "serial" \
    --noconfirm \
    "$ENTRY_POINT"

[[ -d "$APP_BUNDLE" ]] || fail "Expected $APP_BUNDLE to exist after PyInstaller run; aborting."
echo "✓ Built $APP_BUNDLE"

# ── 3.5. Prune unused PySide6 frameworks + plugins ─────────────────────────
# `--collect-all PySide6` pulls in EVERY Qt framework, including
# QtWebEngineCore (~285 MB embedded Chromium) and the entire QML/Quick/3D
# stack we don't use. Pruning takes the bundle from ~830 MB → ~280 MB and
# the DMG from ~360 MB → ~120 MB (also fits under wrangler's 300 MiB cap).
#
# To verify a framework can be safely pruned, grep src/ for it: if nothing
# imports `from PySide6.<Foo>`, it's safe to delete.
step "Pruning unused PySide6 frameworks"
QT_LIB_DIR="$APP_BUNDLE/Contents/Frameworks/PySide6/Qt/lib"
QT_PLUGINS_DIR="$APP_BUNDLE/Contents/Frameworks/PySide6/Qt/plugins"
QT_QML_DIR="$APP_BUNDLE/Contents/Frameworks/PySide6/Qt/qml"
PYSIDE_PKG_DIR="$APP_BUNDLE/Contents/Frameworks/PySide6"

# Frameworks we actually use (grep `from PySide6` in src/ → these names):
KEEP_FRAMEWORKS=(
    QtCore QtGui QtWidgets QtOpenGL QtOpenGLWidgets QtSvg QtDBus QtPrintSupport
)

if [[ -d "$QT_LIB_DIR" ]]; then
    for fw in "$QT_LIB_DIR"/Qt*.framework; do
        name=$(basename "$fw" .framework)
        keep=false
        for k in "${KEEP_FRAMEWORKS[@]}"; do
            [[ "$name" == "$k" ]] && keep=true && break
        done
        $keep || rm -rf "$fw"
    done
    # Drop the matching PySide6 .abi3.so binding modules too — they'd error on
    # import but we never touch them.
    for so in "$PYSIDE_PKG_DIR"/Qt*.abi3.so; do
        [[ -f "$so" ]] || continue
        name=$(basename "$so" .abi3.so)
        keep=false
        for k in "${KEEP_FRAMEWORKS[@]}"; do
            [[ "$name" == "$k" ]] && keep=true && break
        done
        $keep || rm -f "$so"
    done
fi
[[ -d "$QT_QML_DIR" ]] && rm -rf "$QT_QML_DIR"
# Drop Qt's bundled developer tools — end users never touch them and
# they're nested .app bundles + standalone binaries which would each
# need their own codesign pass (notarisation rejects them otherwise).
# PyInstaller drops .app copies in two places (Frameworks/ + Resources/)
# so we wipe both. The standalone CLI tools (balsam, lupdate, etc.) are
# used for translations / QML / Quick3D — none of which we ship.
for d in \
    "$PYSIDE_PKG_DIR/Assistant__dot__app" \
    "$PYSIDE_PKG_DIR/Designer__dot__app" \
    "$PYSIDE_PKG_DIR/Linguist__dot__app" \
    "$APP_BUNDLE/Contents/Resources/PySide6/Assistant.app" \
    "$APP_BUNDLE/Contents/Resources/PySide6/Designer.app" \
    "$APP_BUNDLE/Contents/Resources/PySide6/Linguist.app"; do
    [[ -e "$d" ]] && rm -rf "$d"
done
for f in \
    "$PYSIDE_PKG_DIR/balsam" \
    "$PYSIDE_PKG_DIR/balsamui" \
    "$PYSIDE_PKG_DIR/lupdate" \
    "$PYSIDE_PKG_DIR/lrelease" \
    "$PYSIDE_PKG_DIR/qmlformat" \
    "$PYSIDE_PKG_DIR/qmllint" \
    "$PYSIDE_PKG_DIR/qmlls" \
    "$PYSIDE_PKG_DIR/qsb" \
    "$PYSIDE_PKG_DIR/svgtoqml"; do
    [[ -e "$f" ]] && rm -f "$f"
done

if [[ -d "$QT_PLUGINS_DIR" ]]; then
    rm -rf "$QT_PLUGINS_DIR/qmltooling" \
           "$QT_PLUGINS_DIR/scenegraph" \
           "$QT_PLUGINS_DIR/3dinputdevices" \
           "$QT_PLUGINS_DIR/3drenderers" \
           "$QT_PLUGINS_DIR/sceneparsers" \
           "$QT_PLUGINS_DIR/geometryloaders" \
           "$QT_PLUGINS_DIR/renderers" \
           "$QT_PLUGINS_DIR/renderplugins" \
           "$QT_PLUGINS_DIR/qmllint" \
           "$QT_PLUGINS_DIR/multimedia" \
           "$QT_PLUGINS_DIR/position" \
           "$QT_PLUGINS_DIR/canbus" \
           "$QT_PLUGINS_DIR/sqldrivers" \
           "$QT_PLUGINS_DIR/webview" 2>/dev/null || true
fi
# Note: do NOT prune cryptography/ or cffi/ — google-genai (AI image
# generation) transitively imports cryptography.hazmat.bindings._rust at
# module-load time. Dropping it crashes the app at startup.

NEW_SIZE=$(du -sh "$APP_BUNDLE" | awk '{print $1}')
echo "✓ Pruned bundle size: $NEW_SIZE"

# ── 3.6. Code-sign with Developer ID (if credentials present) ──────────────
# Reads ~/.apple/credentials for `signingIdentity:` and signs the .app
# inside-out (frameworks → dylibs/sos → bundle root). Without a valid
# Developer ID signature + notarisation (handled by make-release.sh),
# end users see Gatekeeper's "cannot verify it's free of malware" alert.
#
# Local builds without ~/.apple/credentials skip signing — useful for
# fast iteration; the resulting .app is just unsigned (same as before).
APPLE_CRED_FILE="$HOME/.apple/credentials"
SIGNING_IDENTITY=""
if [[ -f "$APPLE_CRED_FILE" ]]; then
    # Value is everything after the first "key: " — sed (not awk -F)
    # because the signing identity itself contains a colon
    # ("Developer ID Application: Malcolm Ross (TEAMID)").
    SIGNING_IDENTITY=$(sed -n 's/^signingIdentity: *//p' "$APPLE_CRED_FILE")
fi
ENTITLEMENTS="$PROJECT_ROOT/assets/entitlements.plist"

if [[ -n "$SIGNING_IDENTITY" ]]; then
    step "Signing bundle with Developer ID: $SIGNING_IDENTITY"
    [[ -f "$ENTITLEMENTS" ]] || fail "Missing $ENTITLEMENTS"

    # 1) Sign every embedded framework (Qt*.framework, Python.framework)
    #    inside-out — frameworks must be signed before the parent bundle.
    while IFS= read -r -d '' fw; do
        codesign --force --timestamp --options runtime \
            --sign "$SIGNING_IDENTITY" "$fw" 2>/dev/null || \
            codesign --force --timestamp --options runtime \
                --sign "$SIGNING_IDENTITY" "$fw"
    done < <(find "$APP_BUNDLE/Contents" -type d -name "*.framework" -print0)

    # 2) Sign every nested Mach-O binary. We detect by file content
    #    (`file -b` says "Mach-O ...") rather than just .dylib/.so —
    #    PyInstaller bundles extension-less standalone binaries
    #    (e.g. the Python interpreter, Qt CLI tools) that we'd
    #    otherwise miss and fail notarisation on. Skip files already
    #    signed inside frameworks (codesign --force re-signs them
    #    cleanly anyway, but this is faster and avoids touching
    #    framework Versions/Current internals out of order).
    SIGN_COUNT=0
    while IFS= read -r -d '' f; do
        # Skip anything inside a .framework — those were signed in step 1.
        case "$f" in *.framework/*) continue ;; esac
        head_bytes=$(file -b "$f")
        case "$head_bytes" in
            Mach-O*)
                codesign --force --timestamp --options runtime \
                    --sign "$SIGNING_IDENTITY" "$f" >/dev/null
                SIGN_COUNT=$((SIGN_COUNT + 1))
                ;;
        esac
    done < <(find "$APP_BUNDLE/Contents" -type f -print0)
    echo "  • signed $SIGN_COUNT nested binaries"

    # 3) Sign the main bundle with hardened runtime + entitlements.
    codesign --force --timestamp --options runtime \
        --entitlements "$ENTITLEMENTS" \
        --sign "$SIGNING_IDENTITY" "$APP_BUNDLE"

    # 4) Verify — `codesign -vvv --deep` walks every nested binary and
    #    catches the most common notarisation rejections (unsigned
    #    nested binaries, missing hardened runtime). We don't use
    #    `--verify --deep --strict` because it has a path-handling
    #    bug on bundles this large with spaces in the path that
    #    spuriously reports "No such file or directory".
    if codesign -vvv --deep "$APP_BUNDLE" 2>&1 | tail -2; then
        echo "✓ Signed $APP_BUNDLE"
    else
        fail "Signature verification failed — won't pass notarisation."
    fi
else
    echo "ⓘ ~/.apple/credentials not found — skipping signing (local dev build)."
fi

# ── 4. Build the DMG ──────────────────────────────────────────────────────
step "Packaging $DMG_PATH"
rm -f "$DMG_PATH"
TMP_DMG_DIR="$(mktemp -d)"
trap "rm -rf '$TMP_DMG_DIR'" EXIT

# Copy the .app + an Applications symlink for drag-to-install UX.
cp -R "$APP_BUNDLE" "$TMP_DMG_DIR/"
ln -s /Applications "$TMP_DMG_DIR/Applications"

hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$TMP_DMG_DIR" \
    -ov -format UDZO \
    "$DMG_PATH" >/dev/null

# Sign the DMG too if we have an identity — required so notarytool will
# accept it and so the staple step has something to bind the ticket to.
if [[ -n "$SIGNING_IDENTITY" ]]; then
    codesign --force --timestamp --sign "$SIGNING_IDENTITY" "$DMG_PATH"
    echo "✓ Signed DMG"
fi

echo "✓ Wrote $DMG_PATH"

# ── 5. Done ───────────────────────────────────────────────────────────────
step "Build complete"
echo "  App bundle: $APP_BUNDLE"
echo "  DMG:        $DMG_PATH"
cat <<'NOTE'

To install:
  • Open the .dmg
  • Drag CNC Inlay Studio.app to the Applications folder shortcut
  • Eject the volume

If this build was signed (~/.apple/credentials present) and notarised
(via make-release.sh), Gatekeeper will let it run on first launch.
Local unsigned builds need a one-time bypass:
  • Right-click the .app in Finder → Open → Open anyway, OR
  • Run once: xattr -cr "/Applications/CNC Inlay Studio.app"

NOTE
