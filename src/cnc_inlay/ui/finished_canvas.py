"""Finished-inlay preview: pocket wood with the plug filled in.

A flat, top-down render of what the assembled inlay should look like once
glued and planed flush. The pocket wood texture covers the whole stock
rectangle; the design polygon is filled with the plug wood texture and
edged with a thin glue-line shadow.

This is a sanity-check view, not a photorealistic render. It exists so the
user can spot bad colour pairings (e.g. cherry-on-walnut where the contrast
is too low) before cutting any wood.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
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


class FinishedCanvas(QWidget):
    """Top-down render of the finished inlay using the chosen wood textures."""

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

        # Compute the stock rectangle (design bounds + margin) in mm.
        # Use the IDEAL design extent so the framing doesn't shift when
        # the achievable plug is slightly smaller than the design.
        bounds_geom = self._ideal if self._ideal is not None else self._design
        minx, miny, maxx, maxy = bounds_geom.bounds
        stock = QRectF(
            minx - self._margin_mm,
            miny - self._margin_mm,
            (maxx - minx) + 2 * self._margin_mm,
            (maxy - miny) + 2 * self._margin_mm,
        )

        # Fit the stock rectangle into the widget viewport with a small inset.
        viewport = QRectF(self.rect()).adjusted(20, 20, -20, -40)
        if stock.width() <= 0 or stock.height() <= 0:
            return
        sx = viewport.width() / stock.width()
        sy = viewport.height() / stock.height()
        scale = min(sx, sy)
        # Center the scaled stock in the viewport.
        scaled_w = stock.width() * scale
        scaled_h = stock.height() * scale
        offset_x = viewport.left() + (viewport.width() - scaled_w) / 2
        offset_y = viewport.top() + (viewport.height() - scaled_h) / 2

        # mm → widget pixels. Y is flipped because Qt's Y grows downward but
        # design Y grows upward.
        def to_widget(x_mm: float, y_mm: float) -> QPointF:
            px = offset_x + (x_mm - stock.left()) * scale
            # Flip Y around the stock centre so the design renders right-side up.
            py = offset_y + (stock.bottom() - y_mm) * scale
            return QPointF(px, py)

        stock_rect_widget = QRectF(
            to_widget(stock.left(), stock.bottom()),
            to_widget(stock.right(), stock.top()),
        )

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
        # can't actually cut. Hatched red so the operator sees exactly
        # where the finished piece will deviate from the drawing.
        shortfall = self._compute_shortfall()
        if shortfall is not None and not shortfall.is_empty:
            sf_path = self._polygon_to_path(shortfall, to_widget)
            painter.save()
            painter.setClipPath(sf_path)
            # Solid red wash + diagonal hatch on top for an unmistakable
            # "this won't cut" marker that still lets the wood show.
            painter.fillRect(stock_rect_widget, QColor(255, 40, 60, 90))
            hatch_pen = QPen(QColor(255, 60, 80, 200), 1.0)
            painter.setPen(hatch_pen)
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
        edge_pen = QPen(QColor(0, 0, 0, 180), 1.0)
        painter.setPen(edge_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(stock_rect_widget)

        # 5. Caption underneath naming the pairing + total finished size,
        # plus a shortfall note when the bits can't cut the full design.
        size_w_mm = maxx - minx
        size_h_mm = maxy - miny
        caption = (
            f"Pocket: {self._pocket_species or '—'}    "
            f"Plug: {self._plug_species or '—'}    "
            f"Inlay: {size_w_mm:.0f} × {size_h_mm:.0f} mm"
        )
        shortfall = self._compute_shortfall()
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
