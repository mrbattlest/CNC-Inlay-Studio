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
        # _design = the ACHIEVABLE plug geometry (what the bits can
        # actually cut). _ideal = the raw design as drawn. The render
        # fills _design with plug wood and hatches _ideal − _design in
        # red — the spots that won't come out as designed.
        self._design: Polygon | MultiPolygon | None = None
        self._ideal: Polygon | MultiPolygon | None = None
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

    # --------------------------------------------------------- public API

    def set_design(
        self,
        geom: Polygon | MultiPolygon | None,
        *,
        ideal: Polygon | MultiPolygon | None = None,
        preview_pending: bool = False,
    ) -> None:
        """``geom`` is the achievable plug geometry (built from the real
        toolpaths). ``ideal`` is the design as drawn — the difference is
        hatched red so the user sees where the cut falls short."""
        self._design = geom
        self._ideal = ideal
        self._preview_pending = preview_pending
        self.update()

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
        bounds_geom = self._ideal if self._ideal is not None else self._design
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

    def wheelEvent(self, event) -> None:
        transform = self._compute_transform()
        if transform is None:
            return
        cur = event.position()
        # mm point under the cursor BEFORE the zoom change.
        mm = self._to_mm(transform, cur.x(), cur.y())
        factor = 1.2 if event.angleDelta().y() > 0 else 1.0 / 1.2
        new_zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, self._user_zoom * factor))
        if new_zoom == self._user_zoom:
            return
        self._user_zoom = new_zoom
        # Shift pan so the same mm point stays under the cursor.
        t2 = self._compute_transform()
        if t2 is not None:
            new_pos = self._to_widget(t2, mm[0], mm[1])
            self._pan += QPointF(cur.x() - new_pos.x(), cur.y() - new_pos.y())
        self.update()

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
        if self._design is None or self._design.is_empty:
            painter.fillRect(self.rect(), _BG_COLOR)
            painter.setPen(_LABEL_COLOR)
            if self._preview_pending:
                msg = (
                    "Click Preview Toolpaths to see the finished inlay.\n\n"
                    "The preview reflects what the bits can actually cut —\n"
                    "thin features the bit can't reach will stay as pocket wood."
                )
            else:
                msg = "Load a design and pick materials\nto preview the finished inlay."
            painter.drawText(self.rect(), Qt.AlignCenter, msg)
            return

        painter.fillRect(self.rect(), _BG_COLOR)

        transform = self._compute_transform()
        if transform is None:
            return
        scale, _ox, _oy, stock = transform

        def to_widget(x_mm: float, y_mm: float) -> QPointF:
            return self._to_widget(transform, x_mm, y_mm)

        stock_rect_widget = QRectF(
            to_widget(stock.left(), stock.bottom()),
            to_widget(stock.right(), stock.top()),
        )

        # Clip drawing to the visible widget so a zoomed-in render
        # doesn't spill the tiled textures past the edges.
        painter.setClipRect(self.rect())

        # 1. Pocket wood: tile across the whole stock rectangle.
        pocket_pix = QPixmap.fromImage(texture_for(self._pocket_species))
        painter.drawTiledPixmap(stock_rect_widget, pocket_pix)

        # 2. Plug wood: build the polygon path and clip-fill it.
        path = self._polygon_to_path(self._design, to_widget)
        plug_pix = QPixmap.fromImage(texture_for(self._plug_species))
        painter.save()
        painter.setClipPath(path)
        painter.drawTiledPixmap(stock_rect_widget, plug_pix)
        painter.restore()

        # 2b. Shortfall — areas the design wants as plug but the bits
        # can't actually cut. Hatched red.
        shortfall = self._compute_shortfall()
        if shortfall is not None and not shortfall.is_empty:
            sf_path = self._polygon_to_path(shortfall, to_widget)
            painter.save()
            painter.setClipPath(sf_path)
            painter.fillRect(stock_rect_widget, QColor(255, 40, 60, 90))
            painter.setPen(QPen(QColor(255, 60, 80, 200), 1.0))
            step = 7
            x0 = int(stock_rect_widget.left())
            x1 = int(stock_rect_widget.right())
            y0 = int(stock_rect_widget.top())
            y1 = int(stock_rect_widget.bottom())
            for d in range(0, (x1 - x0) + (y1 - y0), step):
                painter.drawLine(x0 + d, y0, x0, y0 + d)
            painter.restore()
            painter.setPen(QPen(QColor(255, 60, 80, 230), 1.4))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(sf_path)

        # 3. Glue-seam shadow — a thin darker outline where plug meets pocket.
        shadow_pen = QPen(_SHADOW_COLOR, 1.5)
        shadow_pen.setCapStyle(Qt.RoundCap)
        shadow_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(shadow_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # 4. Stock-edge outline so the user sees the wood boundary.
        painter.setPen(QPen(QColor(0, 0, 0, 180), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(stock_rect_widget)

        # 5. Selection rectangle (live drag or committed).
        sel_widget = self._sel_rect_widget
        if sel_widget is None and self._sel_committed_mm is not None:
            m = self._sel_committed_mm
            sel_widget = QRectF(
                to_widget(m.left(), m.top()),
                to_widget(m.right(), m.bottom()),
            ).normalized()
        if sel_widget is not None:
            sel_pen = QPen(_SELECT_COLOR, 1.5, Qt.DashLine)
            painter.setPen(sel_pen)
            painter.setBrush(QColor(245, 166, 35, 50))
            painter.drawRect(sel_widget)

        # 6. Caption underneath: pairing + size + shortfall + zoom hint.
        minx, miny, maxx, maxy = (
            self._ideal if self._ideal is not None else self._design
        ).bounds
        caption = (
            f"Pocket: {self._pocket_species or '—'}    "
            f"Plug: {self._plug_species or '—'}    "
            f"Inlay: {maxx - minx:.0f} × {maxy - miny:.0f} mm"
        )
        caption_rect = QRectF(0, self.height() - 26, self.width(), 22)
        if shortfall is not None and not shortfall.is_empty:
            painter.setPen(QColor(255, 90, 110))
            painter.drawText(
                caption_rect, Qt.AlignCenter,
                caption + f"    ⚠ red = {shortfall.area:.1f} mm² the bits can't cut",
            )
        else:
            painter.setPen(_LABEL_COLOR)
            painter.drawText(caption_rect, Qt.AlignCenter, caption)
        # Zoom hint, top-left.
        painter.setPen(_LABEL_COLOR)
        painter.drawText(
            QRectF(10, 6, self.width() - 20, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            "Scroll = zoom · drag = pan · Shift+drag = capture area · double-click = reset",
        )
        painter.end()

    def _compute_shortfall(self):
        """Region the design wants as plug but the achievable geometry
        can't deliver = ideal − achievable. None when no ideal is set."""
        if self._ideal is None or self._design is None:
            return None
        try:
            diff = self._ideal.difference(self._design)
            if diff.is_empty:
                return None
            return diff
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
