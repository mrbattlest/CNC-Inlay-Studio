# App icon

The build script (`build_app.sh`) expects the source app icon at
`assets/icon.png` — a square PNG, ideally **1024×1024 or larger** for
sharp results across all macOS icon sizes.

The script will generate `assets/icon.icns` (the macOS-native multi-resolution
icon container) automatically using `sips` and `iconutil`. You don't need to
commit `icon.icns` — it's in `.gitignore`.
