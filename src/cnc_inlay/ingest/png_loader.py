"""Load a PNG silhouette and return a shapely Polygon ready for toolpath generation."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import Polygon
from shapely.validation import make_valid
from skimage import measure

from cnc_inlay.core.job import Design


def _to_silhouette_mask(img: Image.Image) -> np.ndarray:
    """Convert a PIL image to a binary silhouette mask (True = inlay shape)."""
    if img.mode == "RGBA":
        # Alpha channel: any non-transparent pixel is part of the shape.
        alpha = np.array(img.split()[-1])
        return alpha > 16
    gray = np.array(img.convert("L"))
    # Otsu-like split: anything darker than midpoint is foreground.
    threshold = 128
    if gray.mean() > 200:
        threshold = max(64, int(gray.mean() - 40))
    return gray < threshold


def load_png(
    path: str | Path,
    *,
    target_width_mm: float = 60.0,
) -> Design:
    """Threshold the PNG, trace the largest contour, return as a Design.

    target_width_mm sets the design's physical width (height scales to keep aspect ratio).
    The PNG itself carries no unit information, so this is the only sensible default.
    """
    p = Path(path)
    img = Image.open(p)
    mask = _to_silhouette_mask(img)
    if not mask.any():
        raise ValueError(f"PNG {p} appears to be empty after thresholding")

    contours = measure.find_contours(mask.astype(float), level=0.5)
    if not contours:
        raise ValueError(f"No traceable contour in {p}")

    contour = max(contours, key=len)
    h_px, w_px = mask.shape
    scale = target_width_mm / w_px
    target_height_mm = h_px * scale

    # measure.find_contours returns (row, col); convert to (x, y) with y flipped
    # so the design has the same orientation as the original PNG.
    points = [(c * scale, target_height_mm - r * scale) for r, c in contour]
    if points[0] != points[-1]:
        points.append(points[0])

    polygon = Polygon(points)
    if not polygon.is_valid:
        polygon = make_valid(polygon)
        if hasattr(polygon, "geoms"):
            polygon = max(polygon.geoms, key=lambda g: g.area)
    if polygon.is_empty or polygon.area <= 0:
        raise ValueError(f"PNG {p} produced an empty polygon")

    minx, miny, maxx, maxy = polygon.bounds
    return Design(
        polygon=polygon,
        width_mm=maxx - minx,
        height_mm=maxy - miny,
        source_path=p,
    )
