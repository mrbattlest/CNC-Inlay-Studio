"""Rasterize shapely polygons to binary masks and convert pixel ↔ mm coordinates.

All output is in **centered coordinates** — (0, 0) sits at the geometric centre
of the design. The user's choice of G-code origin is applied separately at
G-code emission time, so that the visualisation always uses a consistent frame.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from shapely.affinity import scale as shp_scale
from shapely.geometry import MultiPolygon, Polygon


@dataclass(frozen=True)
class RasterTransform:
    """Maps pixel (row, col) -> (x_mm, y_mm) in centered design coordinates.

    Pixel row 0 is the top of the image (y_max in design space). Y is flipped.
    """

    resolution_mm: float
    origin_x_mm: float  # mm value at pixel column 0
    origin_y_mm: float  # mm value at pixel row 0 (top edge)
    height_px: int
    width_px: int

    def pixel_to_mm(self, row: np.ndarray, col: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = self.origin_x_mm + col * self.resolution_mm
        y = self.origin_y_mm - row * self.resolution_mm
        return x, y


def _polygon_to_pixel_rings(
    polygon: Polygon,
    minx: float,
    maxy: float,
    resolution_mm: float,
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    """Convert a shapely polygon to pixel-space exterior + interior rings."""
    def to_px(coords):
        return [
            ((x - minx) / resolution_mm, (maxy - y) / resolution_mm) for x, y in coords
        ]

    exterior = to_px(polygon.exterior.coords)
    interiors = [to_px(r.coords) for r in polygon.interiors]
    return exterior, interiors


def rasterize(
    geom: Polygon | MultiPolygon,
    resolution_mm: float,
    mirror_x: bool = False,
    pad_px: int = 2,
) -> tuple[np.ndarray, RasterTransform]:
    """Rasterize a shapely Polygon or MultiPolygon to a boolean mask.

    Output is always in **centered coordinates** — (0, 0) is the geometric
    centre of the design's bounding box.

    Args:
        geom: shapely Polygon or MultiPolygon in design-space mm.
        resolution_mm: pixel size in mm (e.g. 0.1).
        mirror_x: mirror across the vertical centre (for plug geometry).
        pad_px: padding around the polygon in pixels.

    Returns:
        (mask, transform) where mask[row, col] is True inside the geometry.
    """
    if mirror_x:
        cx = (geom.bounds[0] + geom.bounds[2]) / 2
        geom = shp_scale(geom, xfact=-1, yfact=1, origin=(cx, 0))

    minx, miny, maxx, maxy = geom.bounds
    width_mm = maxx - minx
    height_mm = maxy - miny

    width_px = int(np.ceil(width_mm / resolution_mm)) + 2 * pad_px
    height_px = int(np.ceil(height_mm / resolution_mm)) + 2 * pad_px

    img = Image.new("L", (width_px, height_px), 0)
    draw = ImageDraw.Draw(img)

    polys: list[Polygon]
    if isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    else:
        polys = [geom]

    for poly in polys:
        exterior, interiors = _polygon_to_pixel_rings(poly, minx, maxy, resolution_mm)
        exterior = [(x + pad_px, y + pad_px) for x, y in exterior]
        interiors = [[(x + pad_px, y + pad_px) for x, y in ring] for ring in interiors]
        draw.polygon(exterior, fill=1)
        for ring in interiors:
            draw.polygon(ring, fill=0)

    mask = np.array(img, dtype=bool)

    # Centred frame: (0, 0) at geometric centre of the design.
    cx_mm = (minx + maxx) / 2
    cy_mm = (miny + maxy) / 2

    origin_x_mm = (minx - pad_px * resolution_mm) - cx_mm
    origin_y_mm = (maxy + pad_px * resolution_mm) - cy_mm

    transform = RasterTransform(
        resolution_mm=resolution_mm,
        origin_x_mm=origin_x_mm,
        origin_y_mm=origin_y_mm,
        height_px=height_px,
        width_px=width_px,
    )
    return mask, transform
