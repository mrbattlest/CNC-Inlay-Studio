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

The first time you launch the app, macOS Gatekeeper will block it because it
isn't signed. Either:
  • Right-click the .app in Finder → Open → Open anyway, OR
  • Run once: xattr -cr "/Applications/CNC Inlay Studio.app"

NOTE
