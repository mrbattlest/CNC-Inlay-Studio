"""Load an SVG file and combine its closed paths into an inlay region.

We use the SVG **even-odd fill rule**: when paths are nested (e.g. a frame
described by two closed outlines, or letters inside a banner), we XOR them
together. The result is the visible "ink" of the drawing — exactly what an
inlay should occupy.

Open paths (a single curve with no Close directive) are dropped — they don't
describe a region. Duplicate paths (some SVGs repeat the same closed outline
multiple times) are deduplicated before combination, otherwise XOR would
cancel them out.
"""
from __future__ import annotations

from pathlib import Path as FsPath

from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import make_valid
from svgelements import (
    SVG,
    Arc,
    Close,
    CubicBezier,
    Line,
    Move,
    Path as SvgPath,
    QuadraticBezier,
    Shape,
)

from cnc_inlay.core.job import Design

# svgelements normalises all coordinates to px (96 DPI). Convert to mm.
_PX_PER_MM = 96.0 / 25.4
_MM_PER_PX = 25.4 / 96.0
_FLATNESS_PX = 0.25 * _PX_PER_MM
_GEO_CLOSURE_TOL_PX = 0.5  # how close first/last must be to count as closed


def _segment_points(segment, flatness: float) -> list[tuple[float, float]]:
    if isinstance(segment, Line):
        end = segment.end
        return [(float(end.x), float(end.y))]
    if isinstance(segment, (QuadraticBezier, CubicBezier, Arc)):
        try:
            length = float(segment.length(error=flatness))
        except TypeError:
            length = float(segment.length())
        n = max(4, min(2000, int(length / max(flatness, 0.1))))
        out: list[tuple[float, float]] = []
        for i in range(1, n + 1):
            t = i / n
            p = segment.point(t)
            out.append((float(p.x), float(p.y)))
        return out
    return []


def _is_geo_closed(ring: list[tuple[float, float]]) -> bool:
    if len(ring) < 3:
        return False
    dx = ring[0][0] - ring[-1][0]
    dy = ring[0][1] - ring[-1][1]
    return (dx * dx + dy * dy) ** 0.5 < _GEO_CLOSURE_TOL_PX


def _path_to_closed_polylines(
    path: SvgPath, flatness: float
) -> list[list[tuple[float, float]]]:
    """Iterate segments and return only properly-closed polylines (one per subpath)."""
    rings: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    subpath_start: tuple[float, float] | None = None

    def commit(explicit_close: bool) -> None:
        nonlocal current
        if len(current) < 3:
            current = []
            return
        if explicit_close:
            if subpath_start is not None and current[-1] != subpath_start:
                current.append(subpath_start)
            rings.append(current)
        elif _is_geo_closed(current):
            current.append(current[0])
            rings.append(current)
        # else: drop — this is an open path and not a region.
        current = []

    for seg in path:
        if isinstance(seg, Move):
            commit(explicit_close=False)
            end = seg.end
            current = [(float(end.x), float(end.y))]
            subpath_start = current[0]
        elif isinstance(seg, Close):
            commit(explicit_close=True)
            subpath_start = None
        else:
            current.extend(_segment_points(seg, flatness))
    commit(explicit_close=False)
    return rings


def _ring_to_polygon(ring: list[tuple[float, float]]) -> Polygon | None:
    if len(ring) < 4:
        return None
    try:
        p = Polygon(ring)
    except Exception:
        return None
    if not p.is_valid:
        p = make_valid(p)
        if isinstance(p, MultiPolygon):
            p = max(p.geoms, key=lambda g: g.area, default=None)
        if not isinstance(p, Polygon):
            return None
    if p.is_empty or p.area <= 0:
        return None
    return p


def _dedupe(polygons: list[Polygon], tol_px: float = 0.5) -> list[Polygon]:
    """Drop polygons whose bounds and area are nearly identical to one already kept."""
    out: list[Polygon] = []
    for p in polygons:
        is_dupe = False
        b = p.bounds
        a = p.area
        for q in out:
            qb = q.bounds
            qa = q.area
            if (
                abs(b[0] - qb[0]) < tol_px
                and abs(b[1] - qb[1]) < tol_px
                and abs(b[2] - qb[2]) < tol_px
                and abs(b[3] - qb[3]) < tol_px
                and abs(a - qa) / max(a, 1e-6) < 0.005
            ):
                is_dupe = True
                break
        if not is_dupe:
            out.append(p)
    return out


def _combine_even_odd(polygons: list[Polygon]) -> Polygon | MultiPolygon | None:
    if not polygons:
        return None
    result = polygons[0]
    for p in polygons[1:]:
        result = result.symmetric_difference(p)
    if not result.is_valid:
        result = make_valid(result)
    if result.is_empty:
        return None
    if isinstance(result, (Polygon, MultiPolygon)):
        return result
    # GeometryCollection — keep only the polygonal parts.
    polys = [g for g in result.geoms if isinstance(g, Polygon) and g.area > 0]
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def _to_mm_and_flip(geom, height_px: float, miny_px: float):
    """Convert px-coordinates to mm and flip Y so design 'up' is +Y."""

    def transform_ring(ring):
        return [
            (x * _MM_PER_PX, (height_px - (y - miny_px)) * _MM_PER_PX)
            for x, y in ring
        ]

    if isinstance(geom, MultiPolygon):
        return MultiPolygon(
            [
                Polygon(
                    transform_ring(p.exterior.coords),
                    [transform_ring(r.coords) for r in p.interiors],
                )
                for p in geom.geoms
            ]
        )
    return Polygon(
        transform_ring(geom.exterior.coords),
        [transform_ring(r.coords) for r in geom.interiors],
    )


def load_svg(path: str | FsPath, *, progress_cb=None) -> Design:
    """Parse an SVG file and return its visible inlay region as a Design.

    ``progress_cb(pct, msg)`` (optional) is called periodically with a
    percentage 0-100 and a short status string — wired up by the background-
    task runner so the user sees a real progress bar during long loads.
    """
    p = FsPath(path)
    if progress_cb:
        progress_cb(2, f"Reading {p.name}…")
    svg = SVG.parse(str(p))
    if progress_cb:
        progress_cb(5, "Parsing path data…")
    design = _build_design(svg, progress_cb=progress_cb)
    design.source_path = p
    if progress_cb:
        progress_cb(100, "Done.")
    return design


def load_svg_from_string(svg_text: str, *, progress_cb=None) -> Design:
    """Parse SVG markup from an in-memory string. ``source_path`` is left None."""
    import io

    if progress_cb:
        progress_cb(2, "Reading SVG markup…")
    svg = SVG.parse(io.StringIO(svg_text))
    if progress_cb:
        progress_cb(5, "Parsing path data…")
    return _build_design(svg, progress_cb=progress_cb)


def _build_design(svg: SVG, *, progress_cb=None) -> Design:
    """Combine all closed paths under the even-odd fill rule, mm-convert, flip Y."""
    # Phase budgets (must sum to ~95; the 5 leading is for file I/O):
    #   5..80%  bezier flattening (the heavy phase for traced SVGs)
    #   80..90% dedupe
    #   90..98% even-odd combine
    shapes = [el for el in svg.elements() if isinstance(el, Shape)]
    n_shapes = len(shapes)
    polygons: list[Polygon] = []
    for i, element in enumerate(shapes):
        if progress_cb and (i % 5 == 0 or i == n_shapes - 1):
            pct = 5 + 75 * (i / max(n_shapes, 1))
            progress_cb(pct, f"Flattening path {i + 1} / {n_shapes}…")
        try:
            sp = SvgPath(element)
        except Exception:
            continue
        for ring in _path_to_closed_polylines(sp, _FLATNESS_PX):
            poly = _ring_to_polygon(ring)
            if poly is not None:
                polygons.append(poly)

    if not polygons:
        raise ValueError("No closed shapes found in SVG")

    if progress_cb:
        progress_cb(82, f"Deduplicating {len(polygons)} shapes…")
    polygons = _dedupe(polygons)
    if progress_cb:
        progress_cb(90, f"Combining {len(polygons)} shapes (even-odd)…")
    combined = _combine_even_odd(polygons)
    if combined is None:
        combined = max(polygons, key=lambda g: g.area)

    if progress_cb:
        progress_cb(98, "Converting to mm…")
    minx, miny, maxx, maxy = combined.bounds
    height_px = maxy - miny
    converted = _to_mm_and_flip(combined, height_px=height_px, miny_px=miny)
    cminx, cminy, cmaxx, cmaxy = converted.bounds

    return Design(
        polygon=converted,
        width_mm=cmaxx - cminx,
        height_mm=cmaxy - cminy,
        source_path=None,
    )
