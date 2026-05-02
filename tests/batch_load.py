"""Run the SVG loader against everything in tests/SVGs/ and report shape stats."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from shapely.geometry import MultiPolygon

from cnc_inlay.ingest.svg_loader import load_svg

SVG_DIR = Path(__file__).parent / "SVGs"


def describe(path: Path) -> str:
    try:
        design = load_svg(path)
    except Exception as exc:
        return f"FAIL ({type(exc).__name__}: {exc})"
    geom = design.polygon
    if isinstance(geom, MultiPolygon):
        regions = len(list(geom.geoms))
        holes = sum(len(p.interiors) for p in geom.geoms)
    else:
        regions = 1
        holes = len(geom.interiors)
    return (
        f"OK  {design.width_mm:6.1f} × {design.height_mm:6.1f} mm  "
        f"regions={regions:3d}  holes={holes:3d}  area={geom.area:8.1f} mm²"
    )


def main() -> int:
    files = sorted(SVG_DIR.glob("*.svg"))
    if not files:
        print(f"no SVGs found in {SVG_DIR}")
        return 1
    width = max(len(f.name) for f in files)
    for f in files:
        print(f"{f.name:<{width}}  →  {describe(f)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
