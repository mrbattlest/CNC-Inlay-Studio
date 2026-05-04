"""3D OpenGL preview of the design and its three toolpath layers.

Layers:
- V-carve walls (red)         — V-bit medial-axis cuts with variable depth.
- Endmill flat-bottom clear   (orange) — endmill clearing residual interior at flat depth.
- Boundary cut                (yellow) — plug-only outer profile freeing the inlay piece.

Travel (G0) moves are rendered as light grey lift→traverse→plunge segments.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from shapely.geometry import MultiPolygon, Polygon

from cnc_inlay.toolpath.vcarve import Chain
from cnc_inlay.ui.arrows import chevron_markers, chevron_polyline_3d
from cnc_inlay.ui.info_overlay import InfoOverlay
from cnc_inlay.ui.theme import (
    BG,
    BOUNDARY,
    GRID,
    MACHINE_BIT,
    POCKET,
    POCKET_CLEAR,
    SHAPE_LINE,
    TRAVEL,
)
from cnc_inlay.ui.zoom_toolbar import ViewModeToggle, ZoomToolbar

ViewMode = str  # "pocket" | "plug"


def _qcolor_to_rgba(qcolor) -> tuple[float, float, float, float]:
    return (qcolor.redF(), qcolor.greenF(), qcolor.blueF(), 1.0)


_SHAPE_RGBA = _qcolor_to_rgba(SHAPE_LINE)
_VCARVE_RGBA = _qcolor_to_rgba(POCKET)
_POCKET_CLEAR_RGBA = _qcolor_to_rgba(POCKET_CLEAR)
_BOUNDARY_RGBA = _qcolor_to_rgba(BOUNDARY)
_STOCK_RGBA = (GRID.redF(), GRID.greenF(), GRID.blueF(), 0.55)
_TRAVEL_RGBA = (TRAVEL.redF(), TRAVEL.greenF(), TRAVEL.blueF(), 1.0)
_MACHINE_BIT_RGBA = (MACHINE_BIT.redF(), MACHINE_BIT.greenF(), MACHINE_BIT.blueF(), 1.0)
_SAFE_Z_MM = 5.0

# Lunyee 3018 PRO MAX bed envelope.
BED_WIDTH_MM = 270.0   # Lunyee 3018 PRO MAX measured X travel
BED_HEIGHT_MM = 150.0  # Lunyee 3018 PRO MAX measured Y travel
_BED_RGBA = (0.49, 0.65, 0.58, 0.85)  # muted teal/green


class Canvas3D(gl.GLViewWidget):
    # Emitted when the user clicks the floating "3D | Top" toggle. Value is
    # "3d" or "top". MainWindow connects this to switch the parent
    # QStackedWidget between the 3D and 2D canvases.
    viewModeToggled = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundColor(pg.mkColor(BG))
        # Initial camera frames the WHOLE bed envelope. Setting distance=250
        # was too close for the new elev=60 / azim=270 default — the camera
        # ended up partially inside the 270×150 bed, clipping the grid +
        # screw circles. Axis-label GLTextItems render at fixed screen
        # pixels and stayed visible regardless, which is the bug we fixed
        # here. ``_reset_camera`` only runs when set_design() is called, so
        # a fresh window with no design also needs this baseline.
        self.opts["distance"] = max(BED_WIDTH_MM, BED_HEIGHT_MM) * 2.0
        self.opts["elevation"] = self.DEFAULT_ELEVATION
        self.opts["azimuth"] = self.DEFAULT_AZIMUTH
        # Discoverability — pyqtgraph's GLViewWidget supports rotate/pan/zoom
        # but the user has to know the mouse mappings. The tooltip surfaces
        # them on hover so panning isn't a hidden feature.
        self.setToolTip(
            "Left-drag: rotate · "
            "Right-drag (or middle-drag, or Shift+drag): pan · "
            "Scroll: zoom"
        )

        self._design_items: list = []
        self._stock_items: list = []
        self._bed_items: list = []
        self._vcarve_items: list = []
        self._pocket_items: list = []
        self._boundary_items: list = []

        self._poly_rings: list[
            tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]
        ] = []
        self._extent: tuple[float, float] = (60.0, 60.0)
        self._stock_depth_mm: float = 5.0
        # Physical stock thickness (the real wood). Drives where the
        # wood-block wireframe sits and how high to lift toolpath chains
        # so they appear to cut INTO the wood from the top, instead of
        # plunging through the bed grid. Falls back to stock_depth_mm
        # (= pocket_depth + glue_gap) if the host doesn't tell us the
        # measured stock thickness.
        self._stock_thickness_mm: float = 0.0
        # Stock XY footprint for the wood-block wireframe. 0 = unknown,
        # in which case the block defaults to the design extent + pad.
        self._stock_footprint_w_mm: float = 0.0
        self._stock_footprint_h_mm: float = 0.0
        self._vcarve_chains: list[Chain] = []
        self._pocket_chains: list[Chain] = []
        self._boundary_chains: list[Chain] = []
        self._view_mode: ViewMode = "pocket"

        # Grid + bed envelope are CREATED in showEvent, not here. Reason:
        # pyqtgraph GL items lazily upload buffers to whichever GL context
        # is current at first paint. If we create them in __init__ before
        # this widget has its own context, they bind to the FIRST window's
        # context and silently render nothing in any subsequent window.
        # See _initialise_bed_items().
        self._grid = None

        # 3D / Top-down toggle — sits at the very top-right corner; zoom
        # toolbar slots in just below it. Replaces the old nested QTabWidget.
        # The ``viewModeToggled`` signal is forwarded to whoever wires the
        # parent QStackedWidget (MainWindow).
        self._view_toggle = ViewModeToggle(self, initial="3d")
        self._view_toggle.modeChanged.connect(self.viewModeToggled.emit)
        self._view_toggle.show()
        self._view_toggle.raise_()

        self._zoom_toolbar = ZoomToolbar(self)
        self._zoom_toolbar.zoomIn.connect(self._zoom_in)
        self._zoom_toolbar.zoomOut.connect(self._zoom_out)
        self._zoom_toolbar.fit.connect(self._reset_camera)
        self._zoom_toolbar.show()
        self._zoom_toolbar.raise_()

        self._info_overlay = InfoOverlay(self)
        self._info_overlay.raise_()

        # Multi-bit "Tool sequence" legend, shared style with the 2D canvas.
        # GLViewWidget is a QOpenGLWidget so child Qt widgets render fine on
        # top. Hidden by default; only shown for multi-bit programs.
        from PySide6.QtWidgets import QLabel
        self._bit_legend = QLabel(self)
        self._bit_legend.setTextFormat(Qt.RichText)
        self._bit_legend.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._bit_legend.setStyleSheet(
            "QLabel { background: rgba(38, 42, 49, 220); color: #e8e8ea; "
            "border: 1px solid #3a3f47; border-radius: 6px; "
            "padding: 6px 10px; font-size: 11px; }"
        )
        self._bit_legend.hide()
        self._bit_legend.raise_()

        self._machine_marker_items: list = []

        # pyqtgraph multi-window quirk: GL items lazily allocate VBOs
        # against whichever GL context is current at first paint. If we
        # construct GLGridItem / GLLinePlotItem in __init__ before this
        # widget has its own context, they bind to the FIRST window's
        # context and render nothing in any subsequent window. Symptom:
        # only GLTextItem axis labels show (those go through Qt's painter
        # via renderText, not raw GL). Fix: defer construction to the
        # first showEvent, when this widget definitely has its own
        # context.
        self._bed_initialised = False

    # ---------------------------------------------------------------- design

    def set_design(
        self,
        geom: Polygon | MultiPolygon | None,
        stock_depth_mm: float = 5.0,
    ) -> None:
        self._poly_rings.clear()
        self._stock_depth_mm = stock_depth_mm
        if geom is not None:
            polys: list[Polygon] = (
                list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
            )
            minx, miny, maxx, maxy = geom.bounds
            cx = (minx + maxx) / 2
            cy = (miny + maxy) / 2
            self._extent = (maxx - minx, maxy - miny)
            for poly in polys:
                exterior = [(x - cx, y - cy) for x, y in poly.exterior.coords]
                interiors = [
                    [(x - cx, y - cy) for x, y in r.coords] for r in poly.interiors
                ]
                self._poly_rings.append((exterior, interiors))
        else:
            self._extent = (60.0, 60.0)
        self.clear_toolpaths()
        self._redraw_polygon()
        self._redraw_stock()
        self._reset_camera()

    # -------------------------------------------------------------- toolpaths

    def set_vcarve(self, chains: list[Chain]) -> None:
        self._vcarve_chains = chains or []
        self._redraw_layer(self._vcarve_chains, self._vcarve_items, _VCARVE_RGBA)

    def set_pocket_clear(self, chains: list[Chain]) -> None:
        self._pocket_chains = chains or []
        self._redraw_layer(self._pocket_chains, self._pocket_items, _POCKET_CLEAR_RGBA)
        self._bit_legend.hide()

    def set_pocket_clear_by_bit(
        self, bit_chains: list[tuple[object, list[Chain], str, float]]
    ) -> None:
        """Multi-bit version: each bit's chains drawn in its own colour, plus
        the same 'Tool sequence' legend as the 2D top-down view (anchored to
        the bottom-right corner)."""
        if len(bit_chains) <= 1:
            chains = bit_chains[0][1] if bit_chains else []
            self.set_pocket_clear(chains)
            return
        from cnc_inlay.ui.canvas import build_bit_legend_html
        from cnc_inlay.ui.theme import color_for_bit_index
        # Wipe accumulated chains/items, then draw each bit's chains in its
        # own palette colour.
        self._clear(self._pocket_items)
        flat: list[Chain] = []
        for i, entry in enumerate(bit_chains):
            # Tolerate both the new 4-tuple and any legacy 3-tuples.
            chains = entry[1]
            qc = color_for_bit_index(i)
            rgba = (qc.redF(), qc.greenF(), qc.blueF(), qc.alphaF())
            self._redraw_layer(chains, self._pocket_items, rgba, append=True)
            flat.extend(chains)
        self._pocket_chains = flat

        self._bit_legend.setText(build_bit_legend_html(bit_chains))
        self._bit_legend.adjustSize()
        self._bit_legend.setMinimumWidth(260)
        self._bit_legend.show()
        self._position_bit_legend()

    def set_boundary(self, chains: list[Chain]) -> None:
        self._boundary_chains = chains or []
        self._redraw_layer(self._boundary_chains, self._boundary_items, _BOUNDARY_RGBA)

    def set_stock_outline(self, width_mm: float, height_mm: float) -> None:
        """Render the user's actual wood as a thin neutral-grey rectangle at
        Z=0 so it's visible against the toolpaths above."""
        # Re-use a dedicated items list so we can wipe just this layer.
        if not hasattr(self, "_stock_outline_items"):
            self._stock_outline_items: list = []
        self._clear(self._stock_outline_items)
        if width_mm <= 0 or height_mm <= 0:
            return
        sx, sy = width_mm / 2.0, height_mm / 2.0
        # Closed rectangle outline at Z=0.
        rect_pts = np.array([
            [-sx, -sy, 0.0], [sx, -sy, 0.0],
            [sx, sy, 0.0], [-sx, sy, 0.0],
            [-sx, -sy, 0.0],
        ], dtype=np.float32)
        # Light grey, subtle dashed effect via repeated pen width — close to
        # the SHAPE_LINE colour used elsewhere.
        self._add_line(rect_pts, (0.66, 0.69, 0.73, 0.9), 1.5, self._stock_outline_items)

    def clear_toolpaths(self) -> None:
        self._vcarve_chains = []
        self._pocket_chains = []
        self._boundary_chains = []
        for items in (self._vcarve_items, self._pocket_items, self._boundary_items):
            self._clear(items)

    # ------------------------------------------------------------- view mode

    def set_view_mode(self, mode: ViewMode) -> None:
        if mode not in ("pocket", "plug"):
            return
        self._view_mode = mode
        self._redraw_polygon()

    # -------------------------------------------------------------- internal

    def _clear(self, items: list) -> None:
        for it in items:
            self.removeItem(it)
        items.clear()

    def _add_line(
        self,
        points: np.ndarray,
        color: tuple[float, float, float, float],
        width: float,
        items: list,
    ) -> None:
        item = gl.GLLinePlotItem(
            pos=points, color=color, width=width, antialias=True, mode="line_strip"
        )
        self.addItem(item)
        items.append(item)

    def _redraw_polygon(self) -> None:
        # Plug toolpaths are cut from the mirrored design; mirror the outline
        # on the plug-side canvases so the outline sits on top of its toolpath.
        # Outline is drawn at the TOP of the wood block (Z = stock_thickness)
        # so it sits on the working surface rather than down at bed level.
        self._clear(self._design_items)
        if not self._poly_rings:
            return
        mirror = self._view_mode == "plug"
        z_top = self._z_lift()
        for exterior, interiors in self._poly_rings:
            ext = np.array(
                [(-x if mirror else x, y, z_top) for x, y in exterior],
                dtype=np.float32,
            )
            self._add_line(ext, _SHAPE_RGBA, 1.6, self._design_items)
            for interior in interiors:
                ring = np.array(
                    [(-x if mirror else x, y, z_top) for x, y in interior],
                    dtype=np.float32,
                )
                self._add_line(ring, _SHAPE_RGBA, 1.4, self._design_items)

    def _draw_bed(self) -> None:
        """Draw the CNC bed envelope (270×150 mm, centred on the origin) plus
        the 24 clamp-screw holes drawn at Z=0 and the 1..N axis-label numbers
        along the front (X) and left (Y) edges so 3D matches the top-down
        view's coordinate cues."""
        self._clear(self._bed_items)
        x0, x1 = -BED_WIDTH_MM / 2, BED_WIDTH_MM / 2
        y0, y1 = -BED_HEIGHT_MM / 2, BED_HEIGHT_MM / 2
        rect = np.array(
            [
                [x0, y0, 0.0], [x1, y0, 0.0], [x1, y1, 0.0],
                [x0, y1, 0.0], [x0, y0, 0.0],
            ],
            dtype=np.float32,
        )
        self._add_line(rect, _BED_RGBA, 2.0, self._bed_items)
        # Screw holes + axis labels reuse the same label-space convention as
        # canvas.py so 2D and 3D stay in lockstep.
        from cnc_inlay.ui.canvas import (
            BED_LABEL_X_MAX,
            BED_LABEL_Y_MAX,
            SCREW_HOLE_DIAMETER_MM,
            SCREW_HOLE_LABEL_POSITIONS,
        )
        radius = SCREW_HOLE_DIAMETER_MM / 2.0
        n_seg = 16  # circle resolution
        ring_color = (0.55, 0.6, 0.66, 0.9)
        for x_lbl, y_lbl in SCREW_HOLE_LABEL_POSITIONS:
            cx = x0 + (x_lbl - 1) * 10.0
            cy = y0 + (y_lbl - 1) * 10.0
            pts = np.array([
                [cx + radius * np.cos(2 * np.pi * i / n_seg),
                 cy + radius * np.sin(2 * np.pi * i / n_seg),
                 0.0]
                for i in range(n_seg + 1)
            ], dtype=np.float32)
            self._add_line(pts, ring_color, 1.0, self._bed_items)

        # Axis labels — small numbers along front (X) and left (Y) edges.
        label_color = QColor("#7a8088")
        # 3D needs bigger labels than the 2D top-down — they're rendered at
        # screen-pixel size regardless of zoom, and the 3D camera is usually
        # zoomed further out so 7pt was unreadable.
        label_font = QFont("Helvetica", 11)
        for label in range(1, BED_LABEL_X_MAX + 1):
            x = x0 + (label - 1) * 10.0
            t = gl.GLTextItem(
                pos=(x, y0 - 6.0, 0.0),
                text=str(label),
                color=label_color,
                font=label_font,
                glOptions="translucent",
            )
            self.addItem(t)
            self._bed_items.append(t)
        for label in range(1, BED_LABEL_Y_MAX + 1):
            y = y0 + (label - 1) * 10.0
            t = gl.GLTextItem(
                pos=(x0 - 8.0, y, 0.0),
                text=str(label),
                color=label_color,
                font=label_font,
                glOptions="translucent",
            )
            self.addItem(t)
            self._bed_items.append(t)

    def set_stock_block(
        self, width_mm: float, height_mm: float, thickness_mm: float,
    ) -> None:
        """Tell the canvas the physical stock dimensions so it can render
        the wood as a 3D block sitting on the bed grid (bottom at Z=0,
        top at Z=+thickness) and lift the toolpath chains accordingly.
        Called by the host when the user updates plug/pocket stock
        thickness or when fresh design dimensions arrive."""
        self._stock_footprint_w_mm = max(0.0, width_mm)
        self._stock_footprint_h_mm = max(0.0, height_mm)
        self._stock_thickness_mm = max(0.0, thickness_mm)
        # Re-emit everything so the lift takes effect immediately.
        self._redraw_polygon()
        self._redraw_stock()
        self._redraw_layer(self._vcarve_chains, self._vcarve_items, _VCARVE_RGBA)
        self._redraw_layer(self._pocket_chains, self._pocket_items, _POCKET_CLEAR_RGBA)
        self._redraw_layer(self._boundary_chains, self._boundary_items, _BOUNDARY_RGBA)

    def _z_lift(self) -> float:
        """How much to add to chain Z values for rendering, so cuts
        appear to descend INTO the wood from the top instead of below
        the bed grid. Equal to the stock thickness when set, else falls
        back to the stock-depth so the cut floor sits on the bed (legacy
        behaviour)."""
        return self._stock_thickness_mm if self._stock_thickness_mm > 0 else 0.0

    def _redraw_stock(self) -> None:
        self._clear(self._stock_items)
        # Wood block geometry. Use the physical stock footprint when set
        # by the host; otherwise fall back to the design extent + pad
        # (legacy behaviour for screens before the host wired up stock
        # dimensions).
        if self._stock_footprint_w_mm > 0 and self._stock_footprint_h_mm > 0:
            x0 = -self._stock_footprint_w_mm / 2.0
            x1 =  self._stock_footprint_w_mm / 2.0
            y0 = -self._stock_footprint_h_mm / 2.0
            y1 =  self._stock_footprint_h_mm / 2.0
        else:
            w, h = self._extent
            pad = max(w, h) * 0.05 + 2
            x0, x1 = -w / 2 - pad, w / 2 + pad
            y0, y1 = -h / 2 - pad, h / 2 + pad
        # Block extends from Z=0 (bottom = bed surface) up to
        # Z=+thickness (top of wood). Toolpaths drawn elsewhere are
        # lifted by _z_lift() so cuts descend INTO this block rather
        # than appearing to plunge through the bed.
        thickness = self._stock_thickness_mm if self._stock_thickness_mm > 0 else self._stock_depth_mm
        z_bot = 0.0
        z_top = thickness

        # Brighter / thicker wireframe than the previous rendering so the
        # block reads as a 3D object rather than a faint outline. Top
        # face gets the most emphasis (it's the working surface).
        top_color = (0.78, 0.62, 0.42, 1.0)   # warm wood-tone
        body_color = (0.52, 0.42, 0.30, 0.85)
        top = np.array(
            [[x0, y0, z_top], [x1, y0, z_top], [x1, y1, z_top], [x0, y1, z_top], [x0, y0, z_top]],
            dtype=np.float32,
        )
        bot = np.array(
            [[x0, y0, z_bot], [x1, y0, z_bot], [x1, y1, z_bot], [x0, y1, z_bot], [x0, y0, z_bot]],
            dtype=np.float32,
        )
        self._add_line(top, top_color, 2.5, self._stock_items)
        self._add_line(bot, body_color, 1.5, self._stock_items)
        for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
            pts = np.array([[x, y, z_top], [x, y, z_bot]], dtype=np.float32)
            self._add_line(pts, body_color, 1.5, self._stock_items)

    def _redraw_layer(
        self,
        chains: list[Chain],
        items: list,
        color: tuple[float, float, float, float],
        append: bool = False,
    ) -> None:
        # ``append=True`` lets the multi-bit renderer call this once per bit
        # and accumulate items into the same target list with different
        # per-bit colours.
        if not append:
            self._clear(items)
        if not chains:
            return
        # Lift all rendered Z values by the stock thickness so chains
        # appear to descend INTO the wood block (whose top sits at
        # +stock_thickness) instead of plunging through the bed grid at
        # Z=0. The G-code itself is unchanged — this is a render-only
        # offset so the visual matches what the bit physically does.
        z_lift = self._z_lift()
        safe_z = _SAFE_Z_MM + z_lift
        for chain in chains:
            if len(chain) < 2:
                continue
            pts = np.array([(x, y, z + z_lift) for x, y, z in chain], dtype=np.float32)
            self._add_line(pts, color, 2.0, items)
            # Direction chevrons (chevron helper takes 3D points; lift
            # those too so they sit on the chain rather than below it).
            for pt, direction in chevron_markers(chain):
                lifted_pt = (pt[0], pt[1], pt[2] + z_lift) if len(pt) >= 3 else pt
                tri = chevron_polyline_3d(lifted_pt, direction, size_mm=1.5)
                arrow_pts = np.array(tri, dtype=np.float32)
                self._add_line(arrow_pts, color, 2.5, items)
        # Travel moves: lift → traverse → plunge between consecutive chains.
        for prev, nxt in zip(chains, chains[1:]):
            if not prev or not nxt:
                continue
            ex, ey, ez = prev[-1]
            sx, sy, sz = nxt[0]
            travel = np.array(
                [
                    [ex, ey, ez + z_lift],
                    [ex, ey, safe_z],
                    [sx, sy, safe_z],
                    [sx, sy, sz + z_lift],
                ],
                dtype=np.float32,
            )
            self._add_line(travel, _TRAVEL_RGBA, 1.0, items)

    # ----------------------------------------------------------------- view

    # Default 3D camera orientation. Picked so:
    #   * X axis runs horizontally with +X to the right of the screen,
    #   * Y axis runs into the screen (away from the operator) — same sense
    #     as the physical bed when standing in front of the machine,
    #   * Z axis points up,
    #   * tilted back 30° from a perfect top-down view so depth is visible.
    # ``azimuth = 270`` puts the camera at the -Y side (south of the bed)
    # looking toward +Y. ``elevation = 60`` is 60° above horizon = 30° down
    # from straight-overhead.
    DEFAULT_AZIMUTH = 270.0
    DEFAULT_ELEVATION = 60.0

    def _reset_camera(self) -> None:
        # Camera distance must always frame the BED envelope, not just the
        # current design — otherwise on a fresh window (no design loaded,
        # default extent of 60×60 mm) the camera would sit INSIDE the
        # 270×150 bed rectangle and clip out the grid + screw circles. The
        # axis-label GLTextItems render at fixed screen-pixel size and
        # stay visible even when clipped, which is exactly the bug
        # ("3D shows only X/Y numbers"). max() ensures we always pull
        # back far enough to see the whole bed.
        w, h = self._extent
        framing = max(w, h, BED_WIDTH_MM, BED_HEIGHT_MM)
        self.opts["distance"] = framing * 2.0
        self.opts["center"] = pg.Vector(0, 0, -self._stock_depth_mm / 2)
        self.opts["elevation"] = self.DEFAULT_ELEVATION
        self.opts["azimuth"] = self.DEFAULT_AZIMUTH
        self.update()

    def reset_view(self) -> None:
        self._reset_camera()

    # ---------- view orientation save / restore (project file) -----------

    def get_view_orientation(self) -> dict:
        """Snapshot enough camera state to faithfully restore the view."""
        c = self.opts.get("center")
        return {
            "elevation": float(self.opts.get("elevation", self.DEFAULT_ELEVATION)),
            "azimuth": float(self.opts.get("azimuth", self.DEFAULT_AZIMUTH)),
            "distance": float(self.opts.get("distance", 250.0)),
            "center_x": float(c.x()) if c is not None else 0.0,
            "center_y": float(c.y()) if c is not None else 0.0,
            "center_z": float(c.z()) if c is not None else 0.0,
        }

    def set_view_orientation(self, view: dict | None) -> None:
        """Apply a saved orientation. ``None`` or invalid input → no-op."""
        if not view:
            return
        try:
            self.opts["elevation"] = float(view.get("elevation", self.DEFAULT_ELEVATION))
            self.opts["azimuth"] = float(view.get("azimuth", self.DEFAULT_AZIMUTH))
            self.opts["distance"] = float(view.get("distance", 250.0))
            self.opts["center"] = pg.Vector(
                float(view.get("center_x", 0.0)),
                float(view.get("center_y", 0.0)),
                float(view.get("center_z", 0.0)),
            )
            self.update()
        except (TypeError, ValueError):
            pass

    def _zoom_in(self) -> None:
        self.opts["distance"] = max(20.0, self.opts["distance"] / 1.25)
        self.update()

    def _zoom_out(self) -> None:
        self.opts["distance"] = min(5000.0, self.opts["distance"] * 1.25)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._view_toggle.position_top_right(self.width(), self.height())
        self._zoom_toolbar.position_top_right(
            self.width(), self.height(),
            top_offset=self._view_toggle.height() + 6,
        )
        self._info_overlay.position_bottom_left(self.width(), self.height())
        self._position_bit_legend()

    def showEvent(self, event):
        # See canvas.py for why this matters: tab-switch makes the canvas
        # visible after the legend was set up against a stale parent size.
        super().showEvent(event)
        if not self._bed_initialised:
            self._bed_initialised = True
            self._initialise_bed_items()
        self._position_bit_legend()

    def _initialise_bed_items(self) -> None:
        """Construct grid + bed envelope items. Called from the first
        showEvent so the items bind to THIS widget's GL context. See
        __init__ comment on _bed_initialised for the multi-window
        rationale."""
        self._grid = gl.GLGridItem()
        self._grid.setSize(x=BED_WIDTH_MM, y=BED_HEIGHT_MM)
        self._grid.setSpacing(10, 10)
        self._grid.setColor(pg.mkColor(GRID))
        self.addItem(self._grid)
        self._draw_bed()

    def _position_bit_legend(self) -> None:
        # Don't gate on isVisible() — see canvas.py for the same fix.
        self._bit_legend.adjustSize()
        margin = 12
        x = max(margin, self.width() - self._bit_legend.width() - margin)
        y = max(margin, self.height() - self._bit_legend.height() - margin)
        self._bit_legend.move(x, y)

    def set_size_info(
        self,
        label: str,
        width_mm: float,
        height_mm: float,
        grid_mm: float = 10.0,
        units=None,
    ) -> None:
        self._info_overlay.set_info(grid_mm, label, width_mm, height_mm, units)
        self._info_overlay.position_bottom_left(self.width(), self.height())

    # -------------------------------------------------- machine / sim marker

    def set_sim_position(self, x: float, y: float, z: float) -> None:
        """Bit indicator used during simulator playback. Reuses the same
        purple wireframe cone the live machine-position marker uses so
        the simulator preview reads identically to a real cut. The bit
        diameter defaults to 3 mm because the simulator doesn't carry
        per-segment bit info — it's just a visual marker."""
        self.set_machine_position(x, y, z, bit_diameter_mm=3.0)

    def set_machine_position(
        self, x: float, y: float, z: float, bit_diameter_mm: float = 3.0
    ) -> None:
        """Draw a bright-purple wireframe cone with its tip at the bit point.

        The cone widens upward: the apex is exactly where GRBL says the bit
        tip is, the base sits ``cone_height`` above it. ``bit_diameter_mm``
        sets the cone's base radius so the cone is visually proportional to
        the actual endmill. Z is lifted by stock thickness so the cone
        descends INTO the wood block from above instead of from below
        the bed grid.
        """
        self._clear(self._machine_marker_items)
        z = z + self._z_lift()
        radius = max(0.6, bit_diameter_mm / 2.0)
        cone_height = max(8.0, radius * 12.0)
        base_z = z + cone_height
        n_segments = 16
        base_points = []
        for i in range(n_segments):
            angle = 2 * np.pi * i / n_segments
            base_points.append(
                (x + radius * np.cos(angle), y + radius * np.sin(angle), base_z)
            )
        # Slant lines: tip → each base point.
        tip = (x, y, z)
        for bp in base_points:
            pts = np.array([tip, bp], dtype=np.float32)
            self._add_line(pts, _MACHINE_BIT_RGBA, 2.5, self._machine_marker_items)
        # Base ring.
        for i in range(n_segments):
            a = base_points[i]
            b = base_points[(i + 1) % n_segments]
            pts = np.array([a, b], dtype=np.float32)
            self._add_line(pts, _MACHINE_BIT_RGBA, 2.5, self._machine_marker_items)

    def clear_machine_position(self) -> None:
        self._clear(self._machine_marker_items)
