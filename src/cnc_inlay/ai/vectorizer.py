"""Convert a black-on-white PNG silhouette into an inlay-ready SVG.

Pipeline:
  1. Load the PNG with Pillow, composite any alpha onto white, convert to grayscale.
  2. Threshold to a binary mask (Otsu picks the best cut).
  3. Trace closed contours with ``skimage.measure.find_contours``.
  4. Simplify each contour with shapely to drop redundant near-collinear vertices.
  5. Emit one ``<path d="...Z" fill="#000000"/>`` per contour into a single SVG.

The contour walk produces sub-pixel-accurate boundaries so the resulting
silhouette closely matches what Gemini drew. Internal holes (e.g. the gap
between a pretzel's loops) come back as separate contours; the existing SVG
loader's even-odd combination handles them correctly.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image
from shapely.geometry import Polygon
from skimage import filters, measure


# Drop tiny noise blobs whose area is less than this fraction of the canvas.
_MIN_CONTOUR_AREA_FRAC = 0.0005

# Simplification tolerance in source pixels — bigger = fewer SVG points per
# contour but smoother / less faithful curves. 1.0 is a sweet spot for
# 1000-px-wide images.
_SIMPLIFY_TOLERANCE_PX = 1.0


def png_to_svg(png_bytes: bytes, *, viewbox_size: int = 1000) -> str:
    """Trace ``png_bytes`` (any size) into an SVG of solid black filled paths.

    The output uses ``viewBox="0 0 N N"`` where N matches the longer image
    edge scaled to ``viewbox_size`` px — keeping the aspect ratio. The
    existing svg_loader does the px → mm conversion downstream.
    """
    img = Image.open(io.BytesIO(png_bytes))

    # Composite onto white if the model returned an RGBA image with transparency.
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg

    img = img.convert("L")
    arr = np.asarray(img, dtype=np.uint8)
    h, w = arr.shape

    # Otsu threshold finds the best black/white cut for the image's actual
    # histogram — far more robust than a fixed midpoint when the model returns
    # off-white backgrounds or slightly-grey blacks.
    try:
        thresh = filters.threshold_otsu(arr)
    except Exception:
        thresh = 128
    # `dark` is the silhouette: True where pixels are at or below the threshold.
    # `<=` rather than `<` matters for perfectly bimodal images (pure black/
    # pure white), where Otsu lands on the dark pixel value itself.
    dark = arr <= thresh

    # Pad with a 1-px white border so contours that touch the image edge still
    # close cleanly — find_contours otherwise leaves them open at the boundary.
    padded = np.pad(dark, 1, mode="constant", constant_values=False)

    # Sub-pixel-accurate boundaries between dark and light at value 0.5.
    contours = measure.find_contours(padded.astype(float), 0.5)

    min_area = (w * h) * _MIN_CONTOUR_AREA_FRAC
    paths: list[str] = []
    for c in contours:
        # find_contours returns (row, col); SVG wants (x, y) = (col, row).
        # Subtract 1 to undo the padding offset.
        pts = [(float(p[1] - 1), float(p[0] - 1)) for p in c]
        if len(pts) < 4:
            continue
        try:
            poly = Polygon(pts)
        except Exception:
            continue
        if not poly.is_valid or poly.is_empty:
            continue
        if poly.area < min_area:
            continue
        # Light simplification — keep the silhouette but drop redundant vertices.
        simple = poly.simplify(_SIMPLIFY_TOLERANCE_PX, preserve_topology=True)
        if simple.is_empty or not hasattr(simple, "exterior"):
            continue
        coords = list(simple.exterior.coords)
        if len(coords) < 4:
            continue
        d = _coords_to_path_d(coords)
        paths.append(f'<path fill="#000000" d="{d}"/>')

    if not paths:
        raise ValueError(
            "No silhouette found in the generated image. The model may have "
            "produced a mostly-white image — try a different prompt."
        )

    # Compute viewBox from the actual image dimensions, scaled so the long
    # edge equals viewbox_size. Aspect ratio preserved.
    if w >= h:
        vb_w = viewbox_size
        vb_h = viewbox_size * h / w
    else:
        vb_h = viewbox_size
        vb_w = viewbox_size * w / h
    # The path data is in source-image px; an outer transform scales it to
    # match viewBox.
    sx = vb_w / w
    sy = vb_h / h
    body = "".join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {vb_w:.2f} {vb_h:.2f}">'
        f'<g transform="scale({sx:.6f} {sy:.6f})">{body}</g>'
        f'</svg>'
    )


def _coords_to_path_d(coords: list[tuple[float, float]]) -> str:
    """Turn a list of (x, y) points into an SVG path 'd' string ending in Z."""
    parts = [f"M {coords[0][0]:.2f} {coords[0][1]:.2f}"]
    for x, y in coords[1:]:
        parts.append(f"L {x:.2f} {y:.2f}")
    parts.append("Z")
    return " ".join(parts)
