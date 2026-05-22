"""Finished-inlay preview: pocket wood with the plug filled in.

A flat, top-down render of what the assembled inlay should look like once
glued and planed flush. The pocket wood texture covers the whole stock
rectangle; the achievable plug geometry is filled with the plug wood
texture and edged with a thin glue-line shadow. Areas the bits can't cut
are hatched red.

Supports mouse-wheel zoom, drag-to-pan, and Shift+drag area selection —
the selection emits ``feedbackSelectionMade`` so MainWindow can build a
clipboard snippet covering BOTH the pocket and plug toolpaths there.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget
from shapely.geometry import MultiPolygon, Polygon

from cnc_inlay.ui.wood_textures import texture_for
from cnc_inlay.ui.zoom_toolbar import ZoomToolbar


_BG_COLOR = QColor("#15171b")
_LABEL_COLOR = QColor("#9aa0a6")
_SHADOW_COLOR = QColor(0, 0, 0, 110)  # subtle darker line at glue seam
_SELECT_COLOR = QColor("#f5a623")
_MIN_ZOOM = 0.25
_MAX_ZOOM = 30.0


class FinishedCanvas(QWidget):
    """Top-down render of the finished inlay using the chosen wood textures."""

    # Emitted on Shift+drag release with the selected area in design
    # work-coord mm (Y up). MainWindow opens the feedback dialog.
    feedbackSelectionMade = Signal(QRectF)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setAutoFillBackground(False)
        # _plug_feature = the plug lump the bits actually leave standing.
        # _pocket_cavity = the cavity the pocket bits actually hollow out.
        # Both in the pocket frame. The render fills _plug_feature with
        # plug wood, paints pocket_cavity − plug_feature green (tolerable
        # glue gap) and plug_feature − pocket_cavity red (interference —
        # the plug is oversize and won't physically seat).
        self._plug_feature: Polygon | MultiPolygon | None = None
        self._pocket_cavity: Polygon | MultiPolygon | None = None
        self._preview_pending: bool = False
        self._pocket_species: str = ""
        self._plug_species: str = ""
        # Stock rectangle around the design — design bounds expanded by margin.
        self._margin_mm: float = 20.0
        # View transform: user zoom (× the fit-scale) and pan (px).
        self._user_zoom: float = 1.0
        self._pan: QPointF = QPointF(0.0, 0.0)
        # Drag state. _pan_anchor is set during a plain drag; _sel_anchor
        # during a Shift+drag. _sel_rect_widget is the live rectangle;
        # _sel_committed_mm keeps the last selection visible until the
        # feedback dialog closes (cleared via clear_feedback_selection).
        self._pan_anchor: QPointF | None = None
        self._sel_anchor: QPointF | None = None
        self._sel_rect_widget: QRectF | None = None
        self._sel_committed_mm: QRectF | None = None
        self.setMouseTracking(True)

        # +/− /⌂ zoom toolbar, anchored top-right — same widget and
        # placement the Pocket and Plug previews use.
        self._zoom_toolbar = ZoomToolbar(self)
        self._zoom_toolbar.zoomIn.connect(self._zoom_in)
        self._zoom_toolbar.zoomOut.connect(self._zoom_out)
        self._zoom_toolbar.fit.connect(self.reset_view)
        self._zoom_toolbar.show()
        self._zoom_toolbar.raise_()

    # --------------------------------------------------------- public API

    def set_geometry(
        self,
        pocket_cavity: Polygon | MultiPolygon | None,
        plug_feature: Polygon | MultiPolygon | None,
        *,
        preview_pending: bool = False,
    ) -> None:
        """``pocket_cavity`` and ``plug_feature`` are both in the pocket
        frame, derived from the real toolpaths. The render overlays them:
        cavity − feature = green glue gap, feature − cavity = red
        interference."""
        self._pocket_cavity = pocket_cavity
        self._plug_feature = plug_feature
        self._preview_pending = preview_pending
        self.update()

    def interference_area_mm2(self) -> float:
        """Total area where the plug is oversize vs the pocket (red).
        > 0 means the inlay will not physically seat."""
        red = self._compute_interference()
        return red.area if red is not None and not red.is_empty else 0.0

    def set_materials(self, pocket_species: str, plug_species: str) -> None:
        self._pocket_species = pocket_species
        self._plug_species = plug_species
        self.update()

    def clear_feedback_selection(self) -> None:
        """Drop the visible selection overlay (called after the feedback
        dialog closes)."""
        self._sel_committed_mm = None
        self._sel_rect_widget = None
        self.update()

    def reset_view(self) -> None:
        """Snap zoom + pan back to the fit-to-window default."""
        self._user_zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()

    # -------------------------------------------------------- view transform

    def _compute_transform(self):
        """Return (scale, offset_x, offset_y, stock_QRectF) for the current
        widget size + zoom + pan, or None if there's nothing to draw."""
        bounds_geom = self._bounds_geom()
        if bounds_geom is None or bounds_geom.is_empty:
            return None
        minx, miny, maxx, maxy = bounds_geom.bounds
        stock = QRectF(
            minx - self._margin_mm,
            miny - self._margin_mm,
            (maxx - minx) + 2 * self._margin_mm,
            (maxy - miny) + 2 * self._margin_mm,
        )
        viewport = QRectF(self.rect()).adjusted(20, 20, -20, -40)
        if (
            stock.width() <= 0 or stock.height() <= 0
            or viewport.width() <= 0 or viewport.height() <= 0
        ):
            return None
        base = min(
            viewport.width() / stock.width(),
            viewport.height() / stock.height(),
        )
        scale = base * self._user_zoom
        scaled_w = stock.width() * scale
        scaled_h = stock.height() * scale
        offset_x = (
            viewport.left() + (viewport.width() - scaled_w) / 2 + self._pan.x()
        )
        offset_y = (
            viewport.top() + (viewport.height() - scaled_h) / 2 + self._pan.y()
        )
        return (scale, offset_x, offset_y, stock)

    @staticmethod
    def _to_widget(transform, x_mm: float, y_mm: float) -> QPointF:
        scale, offset_x, offset_y, stock = transform
        return QPointF(
            offset_x + (x_mm - stock.left()) * scale,
            offset_y + (stock.bottom() - y_mm) * scale,
        )

    @staticmethod
    def _to_mm(transform, px: float, py: float) -> tuple[float, float]:
        scale, offset_x, offset_y, stock = transform
        return (
            stock.left() + (px - offset_x) / scale,
            stock.bottom() - (py - offset_y) / scale,
        )

    # --------------------------------------------------------- mouse / zoom

    def _zoom_by(self, factor: float, anchor: QPointF) -> None:
        """Multiply the user zoom by ``factor``, keeping the mm point
        currently under ``anchor`` (widget px) fixed under it."""
        transform = self._compute_transform()
        if transform is None:
            return
        mm = self._to_mm(transform, anchor.x(), anchor.y())
        new_zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, self._user_zoom * factor))
        if new_zoom == self._user_zoom:
            return
        self._user_zoom = new_zoom
        t2 = self._compute_transform()
        if t2 is not None:
            new_pos = self._to_widget(t2, mm[0], mm[1])
            self._pan += QPointF(
                anchor.x() - new_pos.x(), anchor.y() - new_pos.y()
            )
        self.update()

    def _zoom_in(self) -> None:
        self._zoom_by(1.25, QPointF(self.width() / 2.0, self.height() / 2.0))

    def _zoom_out(self) -> None:
        self._zoom_by(0.8, QPointF(self.width() / 2.0, self.height() / 2.0))

    def wheelEvent(self, event) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1.0 / 1.2
        self._zoom_by(factor, event.position())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._zoom_toolbar.position_top_right(self.width(), self.height())

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        if event.modifiers() & Qt.ShiftModifier:
            self._sel_anchor = event.position()
            self._sel_rect_widget = QRectF(self._sel_anchor, self._sel_anchor)
        else:
            self._pan_anchor = event.position()
            self.setCursor(Qt.ClosedHandCursor)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._sel_anchor is not None:
            self._sel_rect_widget = QRectF(self._sel_anchor, pos).normalized()
            self.update()
        elif self._pan_anchor is not None:
            self._pan += QPointF(
                pos.x() - self._pan_anchor.x(),
                pos.y() - self._pan_anchor.y(),
            )
            self._pan_anchor = pos
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._sel_anchor is not None:
            rect = QRectF(self._sel_anchor, event.position()).normalized()
            self._sel_anchor = None
            self._sel_rect_widget = None
            transform = self._compute_transform()
            # Ignore accidental tiny shift-clicks.
            if transform is not None and rect.width() > 4 and rect.height() > 4:
                x0, y0 = self._to_mm(transform, rect.left(), rect.bottom())
                x1, y1 = self._to_mm(transform, rect.right(), rect.top())
                mm_rect = QRectF(
                    QPointF(min(x0, x1), min(y0, y1)),
                    QPointF(max(x0, x1), max(y0, y1)),
                )
                self._sel_committed_mm = mm_rect
                self.update()
                self.feedbackSelectionMade.emit(mm_rect)
            else:
                self.update()
        elif self._pan_anchor is not None:
            self._pan_anchor = None
            self.unsetCursor()

    def mouseDoubleClickEvent(self, event) -> None:
        # Double-click anywhere resets zoom + pan to fit-to-window.
        self.reset_view()

    # ------------------------------------------------------------- paint

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # Empty-state — show a hint and bail.
        if self._plug_feature is None or self._plug_feature.is_empty:
            painter.fillRect(self.rect(), _BG_COLOR)
            painter.setPen(_LABEL_COLOR)
            if self._preview_pending:
                msg = (
                    "Click Preview Toolpaths to see the finished inlay.\n\n"
                    "The preview reflects what the bits can actually cut,\n"
                    "and how the plug seats into the pocket."
                )
            else:
                msg = "Load a design and pick materials\nto preview the finished inlay."
            painter.drawText(self.rect(), Qt.AlignCenter, msg)
            return

        painter.fillRect(self.rect(), _BG_COLOR)

        transform = self._compute_transform()
        if transform is None:
            return
        _scale, _ox, _oy, stock = transform

        def to_widget(x_mm: float, y_mm: float) -> QPointF:
            return self._to_widget(transform, x_mm, y_mm)

        stock_rect_widget = QRectF(
            to_widget(stock.left(), stock.bottom()),
            to_widget(stock.right(), stock.top()),
        )
        painter.setClipRect(self.rect())

        # 1. Pocket wood: tile across the whole stock rectangle.
        pocket_pix = QPixmap.fromImage(texture_for(self._pocket_species))
        painter.drawTiledPixmap(stock_rect_widget, pocket_pix)

        # 2. Plug wood: clip-fill the plug feature with the plug texture.
        plug_path = self._polygon_to_path(self._plug_feature, to_widget)
        plug_pix = QPixmap.fromImage(texture_for(self._plug_species))
        painter.save()
        painter.setClipPath(plug_path)
        painter.drawTiledPixmap(stock_rect_widget, plug_pix)
        painter.restore()

        # 3. Green = tolerable glue gap (pocket cavity wider than plug).
        gap = self._compute_gap()
        if gap is not None and not gap.is_empty:
            g_path = self._polygon_to_path(gap, to_widget)
            painter.fillPath(g_path, QColor(40, 230, 90, 150))
            painter.setPen(QPen(QColor(40, 230, 90, 230), 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(g_path)

        # 4. Red = interference (plug oversize, runs past the pocket wall).
        interference = self._compute_interference()
        if interference is not None and not interference.is_empty:
            r_path = self._polygon_to_path(interference, to_widget)
            painter.fillPath(r_path, QColor(255, 35, 55, 180))
            painter.setPen(QPen(QColor(255, 60, 80, 255), 1.6))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(r_path)

        # 5. Glue-seam shadow — a thin darker outline of the plug feature.
        shadow_pen = QPen(_SHADOW_COLOR, 1.5)
        shadow_pen.setCapStyle(Qt.RoundCap)
        shadow_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(shadow_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(plug_path)

        # 6. Stock-edge outline.
        painter.setPen(QPen(QColor(0, 0, 0, 180), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(stock_rect_widget)

        # 7. Selection rectangle (live drag or committed).
        sel_widget = self._sel_rect_widget
        if sel_widget is None and self._sel_committed_mm is not None:
            m = self._sel_committed_mm
            sel_widget = QRectF(
                to_widget(m.left(), m.top()),
                to_widget(m.right(), m.bottom()),
            ).normalized()
        if sel_widget is not None:
            painter.setPen(QPen(_SELECT_COLOR, 1.5, Qt.DashLine))
            painter.setBrush(QColor(245, 166, 35, 50))
            painter.drawRect(sel_widget)

        # 8. Legend (top-right) + zoom hint (top-left).
        self._draw_legend(painter, gap, interference)
        painter.setPen(_LABEL_COLOR)
        painter.drawText(
            QRectF(10, 6, self.width() - 20, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            "Scroll = zoom · drag = pan · Shift+drag = capture area · double-click = reset",
        )

        # 9. Caption underneath: pairing + size + verdict.
        bg = self._bounds_geom()
        minx, miny, maxx, maxy = bg.bounds
        caption = (
            f"Pocket: {self._pocket_species or '—'}    "
            f"Plug: {self._plug_species or '—'}    "
            f"Inlay: {maxx - minx:.0f} × {maxy - miny:.0f} mm"
        )
        red_area = interference.area if (
            interference is not None and not interference.is_empty
        ) else 0.0
        caption_rect = QRectF(0, self.height() - 26, self.width(), 22)
        if red_area > 0.05:
            painter.setPen(QColor(255, 90, 110))
            painter.drawText(
                caption_rect, Qt.AlignCenter,
                caption + f"    ⛔ {red_area:.1f} mm² interference — plug WILL NOT seat",
            )
        else:
            painter.setPen(QColor(80, 220, 120))
            painter.drawText(
                caption_rect, Qt.AlignCenter,
                caption + "    ✓ plug seats — gaps are within glue tolerance",
            )
        painter.end()

    def _draw_legend(self, painter: QPainter, gap, interference) -> None:
        """Colour key, anchored top-right."""
        red_area = interference.area if (
            interference is not None and not interference.is_empty
        ) else 0.0
        gap_area = gap.area if (gap is not None and not gap.is_empty) else 0.0
        rows = [
            (QColor(40, 230, 90, 200),
             f"Glue gap — plug fits ({gap_area:.0f} mm²)"),
            (QColor(255, 45, 65, 220),
             f"Interference — won't fit ({red_area:.0f} mm²)"),
        ]
        pad = 8
        sw = 14      # swatch
        row_h = 18
        painter.setFont(painter.font())
        fm = painter.fontMetrics()
        text_w = max(fm.horizontalAdvance(t) for _c, t in rows)
        box_w = pad + sw + 6 + text_w + pad
        box_h = pad + len(rows) * row_h + pad
        # Top-left, below the zoom-hint line — keeps clear of the zoom
        # toolbar (+/−/⌂) anchored at the top-right.
        x = 12
        y = 28
        painter.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
        painter.setBrush(QColor(20, 22, 26, 225))
        painter.drawRoundedRect(QRectF(x, y, box_w, box_h), 6, 6)
        for i, (color, text) in enumerate(rows):
            ry = y + pad + i * row_h
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRect(QRectF(x + pad, ry + 2, sw, sw))
            painter.setPen(_LABEL_COLOR)
            painter.drawText(
                QRectF(x + pad + sw + 6, ry, text_w + 4, sw + 2),
                Qt.AlignLeft | Qt.AlignVCenter, text,
            )

    def _bounds_geom(self):
        """Geometry used for framing — union of cavity + plug feature."""
        geoms = [g for g in (self._pocket_cavity, self._plug_feature)
                 if g is not None and not g.is_empty]
        if not geoms:
            return None
        if len(geoms) == 1:
            return geoms[0]
        try:
            return geoms[0].union(geoms[1])
        except Exception:
            return geoms[0]

    def _compute_gap(self):
        """Pocket cavity wider than the plug = tolerable glue gap (green)."""
        if self._pocket_cavity is None or self._plug_feature is None:
            return None
        try:
            diff = self._pocket_cavity.difference(self._plug_feature)
            return None if diff.is_empty else diff
        except Exception:
            return None

    def _compute_interference(self):
        """Plug feature past the pocket wall = interference (red)."""
        if self._pocket_cavity is None or self._plug_feature is None:
            return None
        try:
            diff = self._plug_feature.difference(self._pocket_cavity)
            return None if diff.is_empty else diff
        except Exception:
            return None

    # ---------------------------------------------------- path conversion

    def _polygon_to_path(self, geom, to_widget) -> QPainterPath:
        """Build a single QPainterPath covering the geometry's exterior(s) and
        subtracting any interior holes — uses the OddEvenFill rule so nested
        rings render correctly."""
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
        for poly in polys:
            self._add_ring(path, poly.exterior.coords, to_widget)
            for interior in poly.interiors:
                self._add_ring(path, interior.coords, to_widget)
        return path

    @staticmethod
    def _add_ring(path: QPainterPath, coords, to_widget) -> None:
        coords = list(coords)
        if len(coords) < 3:
            return
        first = to_widget(coords[0][0], coords[0][1])
        path.moveTo(first)
        for x, y in coords[1:]:
            path.lineTo(to_widget(x, y))
        path.closeSubpath()
