"""2D top-down preview of the design and its three toolpath layers.

Each tab (Pocket / Plug) renders its own canvas with three distinct layer
colours: V-carve walls (red), endmill clearance (orange), and plug boundary
cut (yellow). Travel/rapid moves are dashed grey.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
)
from shapely.geometry import MultiPolygon, Polygon

from cnc_inlay.toolpath.vcarve import Chain
from cnc_inlay.ui.arrows import chevron_markers, chevron_polyline_2d
from cnc_inlay.ui.info_overlay import InfoOverlay
from cnc_inlay.ui.theme import (
    BIT_PALETTE,
    color_for_bit_index,
)
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
from cnc_inlay.ui.zoom_toolbar import ZoomToolbar

# Lunyee 3018 PRO MAX bed envelope.
BED_WIDTH_MM = 270.0   # Lunyee 3018 PRO MAX measured X travel
BED_HEIGHT_MM = 150.0  # Lunyee 3018 PRO MAX measured Y travel

# Bed surface markings — the physical bed has 1 cm-spaced axis numbering
# starting at "1" (which sits at physical 0) and going up to "28" (X) / "16"
# (Y). Numbering offset +1 means label N corresponds to physical (N-1) cm.
BED_LABEL_X_MAX = 28
BED_LABEL_Y_MAX = 16

# Threaded clamp holes drilled into the bed for fixturing. Positions are in
# the bed's LABEL coordinate space (label 1 = physical 0). 5.65 mm diameter.
# Source: user-measured. Treating the "0" entries in row 1 as a typo for "1"
# (the bed's bottom edge); all four rows live ON the bed surface.
SCREW_HOLE_DIAMETER_MM = 5.65
SCREW_HOLE_LABEL_POSITIONS = [
    # row 1 (y=1)
    (1, 1), (6.2825, 1), (11.8475, 1), (17.2825, 1), (22.8475, 1), (28, 1),
    # row 2 (y=6)
    (1, 6), (6.2825, 6), (11.8475, 6), (17.2825, 6), (22.8475, 6), (28, 6),
    # row 3 (y=11)
    (1, 11), (6.2825, 11), (11.8475, 11), (17.2825, 11), (22.8475, 11), (28, 11),
    # row 4 (y=16)
    (1, 16), (6.2825, 16), (11.8475, 16), (17.2825, 16), (22.8475, 16), (28, 16),
]
_BED_COLOR = "#7ea799"  # muted teal/green for bed boundary

ViewMode = str  # "pocket" | "plug"


def build_bit_legend_html(
    bit_chains: list[tuple[object, list, str, float]],
) -> str:
    """Render the multi-bit "Tool sequence" legend as an HTML string.

    Shared between the 2D and 3D canvases so both show the same per-bit
    colour swatches, names, durations, and stacked timeline bar.
    """
    total_s = sum(max(0.0, d) for _, _, _, d in bit_chains)

    def _fmt(s: float) -> str:
        s = max(0.0, s)
        if s >= 3600:
            return f"{int(s // 3600)}h {int((s % 3600) // 60):02d}m"
        return f"{int(s // 60)}m {int(s % 60):02d}s"

    rows = []
    for i, (bit, _, role, dur) in enumerate(bit_chains):
        color = color_for_bit_index(i)
        pct = (dur / total_s * 100) if total_s > 0 else 0
        rows.append(
            f"<tr>"
            f"<td style='color:#9aa0a6; padding-right:6px;'>"
            f"<b>{i + 1}.</b></td>"
            f"<td><span style='display:inline-block; width:14px; height:14px; "
            f"background:{color.name()}; border:1px solid #3a3f47; "
            f"vertical-align:middle;'>&nbsp;</span></td>"
            f"<td style='padding-left:6px'>"
            f"<b>{bit.name}</b>"
            f"<span style='color:#9aa0a6'> &nbsp;{role.lower()}</span>"
            f"</td>"
            f"<td style='padding-left:10px; color:#d8dce2;'>"
            f"{_fmt(dur)} <span style='color:#9aa0a6'>({pct:.0f}%)</span>"
            f"</td>"
            f"</tr>"
        )
    return (
        f"<b>Tool sequence</b> &nbsp; "
        f"<span style='color:#9aa0a6'>total ~{_fmt(total_s)}</span>"
        f"<table cellspacing='2' cellpadding='0' "
        f"style='margin-top:4px'>{''.join(rows)}</table>"
    )


class PreviewCanvas(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        # GPU-accelerated viewport: replaces the default QWidget with a
        # QOpenGLWidget so QPainter calls run through the OpenGL backend.
        # All chain/arrow/text/grid drawing then renders on the GPU instead
        # of CPU rasterisation. Significantly smoother pan / zoom / resize
        # for scenes with thousands of vertices.
        try:
            from PySide6.QtOpenGLWidgets import QOpenGLWidget
            from PySide6.QtGui import QSurfaceFormat
            fmt = QSurfaceFormat()
            fmt.setSamples(4)  # 4× MSAA → smoother lines on the GPU
            gl_viewport = QOpenGLWidget(self)
            gl_viewport.setFormat(fmt)
            self.setViewport(gl_viewport)
        except Exception:
            # Fall back to CPU viewport on any platform where GL init fails;
            # functionality is unaffected, just less smooth.
            pass
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing
        )
        self.setBackgroundBrush(QBrush(BG))
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # --- Performance for pan-while-dragging ---
        # A complex multi-bit toolpath puts thousands of QGraphicsPathItem
        # children in the scene. Per-frame re-stroking each one with
        # antialiasing was the cause of the laggy hand-drag pan. The three
        # settings below recover the missing fluidity:
        #
        #  * CacheBackground — caches the heavy drawBackground() (bed grid +
        #    24 screw circles + 44 axis numbers) into a pixmap so panning
        #    just blits instead of re-rendering it every frame.
        #  * SmartViewportUpdate — updates only viewport rects that actually
        #    changed during a pan, not the whole widget.
        #  * DontAdjustForAntialiasing + DontSavePainterState — skips Qt's
        #    per-item painter-state save/restore and AA rect adjustment;
        #    every chain item benefits from this on every frame.
        self.setCacheMode(QGraphicsView.CacheBackground)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setOptimizationFlags(
            QGraphicsView.DontAdjustForAntialiasing
            | QGraphicsView.DontSavePainterState
        )
        # Click-and-drag panning. Cursor switches to a hand on hover; left-
        # button drag moves the view. Scroll wheel still zooms (wheelEvent
        # below). With the default NoDrag mode the user could zoom but not
        # navigate to the zoomed-into region — exactly the bug we're fixing.
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setInteractive(True)
        self.scale(1, -1)

        self._poly_rings: list[
            tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]
        ] = []
        self._content_rect: QRectF | None = None
        self._vcarve_chains: list[Chain] = []
        self._pocket_chains: list[Chain] = []
        self._boundary_chains: list[Chain] = []
        self._view_mode: ViewMode = "pocket"

        self._shape_item: QGraphicsPathItem | None = None
        self._vcarve_items: list[QGraphicsPathItem] = []
        self._pocket_items: list[QGraphicsPathItem] = []
        self._boundary_items: list[QGraphicsPathItem] = []

        self._zoom_toolbar = ZoomToolbar(self)
        self._zoom_toolbar.zoomIn.connect(self._zoom_in)
        self._zoom_toolbar.zoomOut.connect(self._zoom_out)
        self._zoom_toolbar.fit.connect(self._fit)
        self._zoom_toolbar.show()
        self._zoom_toolbar.raise_()

        self._info_overlay = InfoOverlay(self)
        self._info_overlay.raise_()

        # Legend for multi-bit toolpaths — shows colour ↔ bit name. Hidden in
        # single-bit mode so the canvas stays clean.
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

        self._machine_marker_item: QGraphicsPathItem | None = None
        self._stock_outline_item: QGraphicsPathItem | None = None

    # ---------------------------------------------------------------- design

    def set_design(self, geom: Polygon | MultiPolygon | None) -> None:
        self._poly_rings.clear()
        self._content_rect = None
        if geom is not None:
            polys: list[Polygon] = (
                list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
            )
            minx, miny, maxx, maxy = geom.bounds
            cx = (minx + maxx) / 2
            cy = (miny + maxy) / 2
            for poly in polys:
                exterior = [(x - cx, y - cy) for x, y in poly.exterior.coords]
                interiors = [
                    [(x - cx, y - cy) for x, y in r.coords] for r in poly.interiors
                ]
                self._poly_rings.append((exterior, interiors))
            self._content_rect = QRectF(
                minx - cx, miny - cy, maxx - minx, maxy - miny
            )
        self.clear_toolpaths()
        self._redraw_polygon()
        self._fit()

    # -------------------------------------------------------------- toolpaths

    def set_vcarve(self, chains: list[Chain]) -> None:
        self._vcarve_chains = chains or []
        self._redraw_layer(self._vcarve_chains, self._vcarve_items, POCKET, 3)

    def set_pocket_clear(self, chains: list[Chain]) -> None:
        self._pocket_chains = chains or []
        self._redraw_layer(self._pocket_chains, self._pocket_items, POCKET_CLEAR, 2)
        self._bit_legend.hide()

    def set_pocket_clear_by_bit(
        self, bit_chains: list[tuple[object, list[Chain], str, float]]
    ) -> None:
        """Render a multi-bit clearing pass with one colour per bit + legend.

        ``bit_chains`` is a list of (Bit, chains, role, duration_s) tuples in
        cut order. Single-bit programs use ``set_pocket_clear`` for the
        original visual style; this method handles actual multi-bit runs.
        """
        # Fall back to legacy single-colour rendering for single-bit programs.
        if len(bit_chains) <= 1:
            chains = bit_chains[0][1] if bit_chains else []
            self.set_pocket_clear(chains)
            return
        # Wipe existing items and accumulated chains, then draw each bit's
        # chains in its own palette colour.
        for item in self._pocket_items:
            self._scene.removeItem(item)
        self._pocket_items.clear()
        flat: list[Chain] = []
        for i, (bit, chains, role, _dur) in enumerate(bit_chains):
            color = color_for_bit_index(i)
            self._redraw_layer(
                chains, self._pocket_items, color, 2 + i * 0.001,
                append=True,
            )
            flat.extend(chains)
        self._pocket_chains = flat
        self._bit_legend.setText(build_bit_legend_html(bit_chains))
        self._bit_legend.adjustSize()
        # Wider so the timeline bar has room to render.
        self._bit_legend.setMinimumWidth(260)
        self._bit_legend.show()
        self._position_bit_legend()

    def set_boundary(self, chains: list[Chain]) -> None:
        self._boundary_chains = chains or []
        self._redraw_layer(self._boundary_chains, self._boundary_items, BOUNDARY, 4)

    def set_stock_outline(self, width_mm: float, height_mm: float) -> None:
        """Draw a dashed rectangle representing the user's actual wood. 0
        values clear it (no stock dimensions specified)."""
        # Clear any prior outline.
        if hasattr(self, "_stock_outline_item") and self._stock_outline_item is not None:
            self._scene.removeItem(self._stock_outline_item)
            self._stock_outline_item = None
        if width_mm <= 0 or height_mm <= 0:
            return
        # Stock is centered on the origin (matching the centered-design frame).
        rect = QRectF(-width_mm / 2, -height_mm / 2, width_mm, height_mm)
        path = QPainterPath()
        path.addRect(rect)
        pen = QPen(QColor("#a8b0bb"))
        pen.setStyle(Qt.DashLine)
        pen.setCosmetic(True)
        pen.setWidthF(1.4)
        item = self._scene.addPath(path, pen)
        item.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        item.setZValue(0.5)  # behind toolpaths but in front of bed grid
        self._stock_outline_item = item

    def clear_toolpaths(self) -> None:
        self._vcarve_chains = []
        self._pocket_chains = []
        self._boundary_chains = []
        for items in (self._vcarve_items, self._pocket_items, self._boundary_items):
            for item in items:
                self._scene.removeItem(item)
            items.clear()

    # ------------------------------------------------------------- view mode

    def set_view_mode(self, mode: ViewMode) -> None:
        if mode not in ("pocket", "plug"):
            return
        self._view_mode = mode
        self._redraw_polygon()

    # -------------------------------------------------------------- internal

    def _redraw_polygon(self) -> None:
        # Plug toolpaths are cut from the mirrored design so a face-down flip
        # registers them with the pocket. Mirror the design outline on the
        # plug side too, so the outline sits on top of its toolpath.
        if self._shape_item is not None:
            self._scene.removeItem(self._shape_item)
            self._shape_item = None
        if not self._poly_rings:
            return

        mirror = self._view_mode == "plug"
        path = QPainterPath()
        for exterior, interiors in self._poly_rings:
            if not exterior:
                continue
            ext_pts = [(-x if mirror else x, y) for x, y in exterior]
            path.moveTo(*ext_pts[0])
            for x, y in ext_pts[1:]:
                path.lineTo(x, y)
            path.closeSubpath()
            for interior in interiors:
                if not interior:
                    continue
                ints = [(-x if mirror else x, y) for x, y in interior]
                path.moveTo(*ints[0])
                for x, y in ints[1:]:
                    path.lineTo(x, y)
                path.closeSubpath()
        pen = QPen(SHAPE_LINE)
        pen.setCosmetic(True)
        pen.setWidthF(1.2)
        self._shape_item = self._scene.addPath(path, pen)
        self._shape_item.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        self._shape_item.setZValue(1)

    def _redraw_layer(
        self,
        chains: list[Chain],
        target: list[QGraphicsPathItem],
        color,
        z: float,
        append: bool = False,
    ) -> None:
        # ``append=True`` is used by the multi-bit renderer so each bit's
        # chains can be drawn in a different colour into the same target list
        # without each call wiping the previous bit's items.
        #
        # All same-color geometry collapses into THREE QGraphicsPathItems per
        # call: one for the cuts (every chain as a sub-path), one for all the
        # chevrons, one for travel moves. Previously we created one item per
        # chain, which meant a complex multi-bit pocket could produce 5 000+
        # items — pan re-rendering all of them was the source of the lag.
        if not append:
            for item in target:
                self._scene.removeItem(item)
            target.clear()
        if not chains:
            return

        cut_pen = QPen(color)
        cut_pen.setCosmetic(True)
        cut_pen.setWidthF(1.6)
        arrow_pen = QPen(color)
        arrow_pen.setCosmetic(True)
        arrow_pen.setWidthF(2.2)

        cuts_path = QPainterPath()
        arrows_path = QPainterPath()
        for chain in chains:
            if len(chain) < 2:
                continue
            x0, y0, _ = chain[0]
            cuts_path.moveTo(x0, y0)
            for x, y, _ in chain[1:]:
                cuts_path.lineTo(x, y)
            for pt, direction in chevron_markers(chain):
                tri = chevron_polyline_2d(pt, direction, size_mm=1.5)
                arrows_path.moveTo(*tri[0])
                for q in tri[1:]:
                    arrows_path.lineTo(*q)

        if not cuts_path.isEmpty():
            item = self._scene.addPath(cuts_path, cut_pen)
            item.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
            item.setZValue(z)
            target.append(item)
        if not arrows_path.isEmpty():
            arrow_item = self._scene.addPath(arrows_path, arrow_pen)
            arrow_item.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
            arrow_item.setZValue(z + 0.1)
            target.append(arrow_item)

        # Travel moves between consecutive chains: one combined path.
        travel_pen = QPen(TRAVEL)
        travel_pen.setStyle(Qt.DashLine)
        travel_pen.setCosmetic(True)
        travel_pen.setWidthF(0.8)
        travel_path = QPainterPath()
        for prev, nxt in zip(chains, chains[1:]):
            if not prev or not nxt:
                continue
            travel_path.moveTo(prev[-1][0], prev[-1][1])
            travel_path.lineTo(nxt[0][0], nxt[0][1])
        if not travel_path.isEmpty():
            item = self._scene.addPath(travel_path, travel_pen)
            item.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
            item.setZValue(z - 0.5)
            target.append(item)

    # ------------------------------------------------------------------- view

    def _fit(self) -> None:
        # Always include the bed envelope so the user can see the design
        # placed in the context of the physical CNC bed.
        bed_rect = QRectF(-BED_WIDTH_MM / 2, -BED_HEIGHT_MM / 2, BED_WIDTH_MM, BED_HEIGHT_MM)
        if self._content_rect is None:
            target = bed_rect
        else:
            target = self._content_rect.united(bed_rect)
        margin = max(target.width(), target.height()) * 0.05 + 4
        target = target.adjusted(-margin, -margin, margin, margin)
        # Generous sceneRect — much bigger than the visible content — so the
        # user can pan freely with the hand cursor when zoomed in. Without
        # this, ScrollHandDrag clamps panning to the content bbox and the
        # user can't move past the bed edges.
        pan_padding = max(bed_rect.width(), bed_rect.height()) * 1.5
        scene_rect = bed_rect.adjusted(
            -pan_padding, -pan_padding, pan_padding, pan_padding
        )
        self._scene.setSceneRect(scene_rect)
        self.fitInView(target, Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()
        self._zoom_toolbar.position_top_right(self.width(), self.height())
        self._info_overlay.position_bottom_left(self.width(), self.height())
        self._position_bit_legend()

    def _position_bit_legend(self) -> None:
        # Don't gate on isVisible(): when the canvas is on the inactive tab
        # at result-time, the legend is .show()n but isVisible() returns
        # False (parent not visible), so an early-out would leave the legend
        # stuck at its default (0,0) — i.e. upper-left — until the user
        # interacts with the canvas. Always compute the correct anchor.
        self._bit_legend.adjustSize()
        # Bottom-right corner. Zoom toolbar lives top-right, info overlay
        # bottom-left, so this is the only free corner.
        margin = 12
        x = max(margin, self.width() - self._bit_legend.width() - margin)
        y = max(margin, self.height() - self._bit_legend.height() - margin)
        self._bit_legend.move(x, y)

    def showEvent(self, event):
        # When the user switches tabs and this canvas becomes visible, the
        # legend may have been positioned against a stale parent size. Snap
        # it back to the bottom-right anchor on every show.
        super().showEvent(event)
        self._position_bit_legend()

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

    def set_sim_position(self, x: float, y: float, _z: float = 0.0) -> None:
        """Subtle green crosshair for simulator playback."""
        self._render_position_marker(x, y, color=TRAVEL, radius_mm=0.6, kind="sim")

    def set_machine_position(
        self, x: float, y: float, _z: float = 0.0, bit_diameter_mm: float = 3.0
    ) -> None:
        """Bright-purple bit-footprint indicator for real-machine streaming.
        Top-down view shows the actual cutter circle plus a centre cross."""
        radius = max(0.5, bit_diameter_mm / 2.0)
        self._render_position_marker(x, y, color=MACHINE_BIT, radius_mm=radius, kind="machine")

    def clear_machine_position(self) -> None:
        if self._machine_marker_item is not None:
            self._scene.removeItem(self._machine_marker_item)
            self._machine_marker_item = None

    def _render_position_marker(self, x: float, y: float, color, radius_mm: float, kind: str) -> None:
        if self._machine_marker_item is not None:
            self._scene.removeItem(self._machine_marker_item)
            self._machine_marker_item = None
        path = QPainterPath()
        path.addEllipse(x - radius_mm, y - radius_mm, radius_mm * 2, radius_mm * 2)
        cross = radius_mm * 0.7
        path.moveTo(x - cross, y); path.lineTo(x + cross, y)
        path.moveTo(x, y - cross); path.lineTo(x, y + cross)
        pen = QPen(color)
        pen.setCosmetic(True)
        pen.setWidthF(2.5 if kind == "machine" else 2.0)
        self._machine_marker_item = self._scene.addPath(path, pen)
        self._machine_marker_item.setZValue(10)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def _zoom_in(self) -> None:
        self.scale(1.25, 1.25)

    def _zoom_out(self) -> None:
        self.scale(0.8, 0.8)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        # 10 mm grid extending across the bed envelope only.
        bed_x0 = -BED_WIDTH_MM / 2
        bed_x1 = BED_WIDTH_MM / 2
        bed_y0 = -BED_HEIGHT_MM / 2
        bed_y1 = BED_HEIGHT_MM / 2
        pen = QPen(GRID)
        pen.setCosmetic(True)
        pen.setWidthF(0.5)
        painter.setPen(pen)
        step = 10.0
        x = bed_x0
        while x <= bed_x1 + 1e-6:
            painter.drawLine(x, bed_y0, x, bed_y1)
            x += step
        y = bed_y0
        while y <= bed_y1 + 1e-6:
            painter.drawLine(bed_x0, y, bed_x1, y)
            y += step
        # Bed envelope boundary in bright accent.
        bed_pen = QPen(QColor(_BED_COLOR))
        bed_pen.setCosmetic(True)
        bed_pen.setWidthF(2.0)
        painter.setPen(bed_pen)
        painter.drawRect(QRectF(bed_x0, bed_y0, BED_WIDTH_MM, BED_HEIGHT_MM))

        # --- Screw holes (clamp mounts), in label coords ---
        # label N → physical (N-1)*10 mm offset from the bed's lower-left.
        hole_pen = QPen(QColor("#5a6068"))
        hole_pen.setCosmetic(True)
        hole_pen.setWidthF(0.7)
        painter.setPen(hole_pen)
        painter.setBrush(QBrush(QColor(28, 30, 34, 180)))
        radius = SCREW_HOLE_DIAMETER_MM / 2.0
        for x_lbl, y_lbl in SCREW_HOLE_LABEL_POSITIONS:
            cx = bed_x0 + (x_lbl - 1) * 10.0
            cy = bed_y0 + (y_lbl - 1) * 10.0
            painter.drawEllipse(QPointF(cx, cy), radius, radius)
        painter.setBrush(Qt.NoBrush)

        # --- Axis labels along the bottom (X) and left (Y) edges ---
        # The view has scale(1, -1) applied so text drawn directly is upside
        # down. Translate to the label position then scale(1, -1) again to
        # un-flip text, draw, then restore.
        label_pen = QPen(QColor("#7a8088"))
        painter.setPen(label_pen)
        font = painter.font()
        # 4.2 pt = ~40% smaller than the previous 7 pt. Cosmetic-sized so it
        # doesn't grow with zoom.
        font.setPointSizeF(4.2)
        painter.setFont(font)
        # X axis: numbers 1..28 below the bed
        for label in range(1, BED_LABEL_X_MAX + 1):
            wx = bed_x0 + (label - 1) * 10.0
            painter.save()
            painter.translate(wx, bed_y0 - 1.5)
            painter.scale(1, -1)
            painter.drawText(QRectF(-12, 0, 24, 8), Qt.AlignHCenter | Qt.AlignTop, str(label))
            painter.restore()
        # Y axis: numbers 1..16 to the left of the bed
        for label in range(1, BED_LABEL_Y_MAX + 1):
            wy = bed_y0 + (label - 1) * 10.0
            painter.save()
            painter.translate(bed_x0 - 1.5, wy)
            painter.scale(1, -1)
            painter.drawText(QRectF(-18, -4, 16, 8), Qt.AlignRight | Qt.AlignVCenter, str(label))
            painter.restore()
