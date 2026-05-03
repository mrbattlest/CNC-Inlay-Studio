"""Sidebar parameter widgets (file, design, material, bits, speeds, inlay)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QColor, QPainter, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


# Mouse-wheel-proof Qt input widgets. By default Qt eats wheel events on
# focused/hovered spinboxes and combos and uses them to change the value —
# which is a footgun when the user is trying to scroll the parent panel and
# their cursor crosses one of these. Overriding wheelEvent to ignore() lets
# the wheel scroll pass through to the parent QScrollArea instead.
class _NoScrollSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class _NoScrollDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class _NoScrollComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


# Public aliases — the rest of this module references these names. Any future
# spinbox/combobox added here is wheel-proof by default.
QSpinBox = _NoScrollSpinBox  # type: ignore[misc]
QDoubleSpinBox = _NoScrollDoubleSpinBox  # type: ignore[misc]
QComboBox = _NoScrollComboBox  # type: ignore[misc]

from cnc_inlay.core.job import Bit, BitSpeedOverride, InlayParams, Material, Origin
from cnc_inlay.ui.theme import color_for_bit_index
from cnc_inlay.core.units import (
    MM_PER_INCH,
    Units,
    feed_to_display,
    feed_to_internal,
    length_to_display,
    length_to_internal,
)


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("sectionTitle")
    return lbl


def _hint(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("hint")
    lbl.setWordWrap(True)
    return lbl


# Help texts for every setting — title + rich-HTML body. Surfaced via the ⓘ
# button next to each spinbox / combo. Keep concise.
HELP_TEXTS: dict[str, tuple[str, str]] = {
    "origin": (
        "Origin point",
        "Where <b>X=0, Y=0</b> sits on your stock when the CNC starts cutting."
        "<ul>"
        "<li><b>Center</b> (recommended): origin at the geometric centre of the "
        "design. Easy to position symmetric designs.</li>"
        "<li><b>Bottom-left</b>: origin at the bottom-left corner of the design's "
        "bounding rectangle. Useful when stock is tight and you want a known "
        "reference corner.</li>"
        "</ul>"
        "You set this physically by jogging the bit on the machine and clicking "
        "<b>Set XY = 0</b> in the Machine panel.",
    ),
    "pocket_material": (
        "Pocket material",
        "The wood the pocket cavity will be cut <b>into</b>. The pocket is the "
        "base piece of your inlay — the contrasting plug drops into this cavity."
        "<br><br>"
        "Each material carries default RPM, feed and plunge rates suitable for "
        "that wood; you can override those in the Speeds group."
        "<br><br>"
        "<b>Recommended</b>: walnut, oak, cherry — denser woods produce cleaner "
        "pocket walls.",
    ),
    "plug_material": (
        "Plug material",
        "The wood that becomes the visible inlay piece. Pick a species visually "
        "different from the pocket wood for contrast."
        "<br><br>"
        "<b>Recommended pairings</b>: walnut + maple, cherry + birch, oak + "
        "purpleheart. Lighter woods inside dark pockets pop visually; the inverse "
        "also works.",
    ),
    "endmill": (
        "Clearance endmill",
        "The flat-bottomed cutting bit that hollows out the pocket interior and "
        "the plug's surrounding waste area."
        "<br><br>"
        "<b>Smaller diameter</b> = finer detail but slower cut.<br>"
        "<b>Larger diameter</b> = faster but can't reach narrow features."
        "<br><br>"
        "<b>Recommended</b>:"
        "<ul>"
        "<li>0.8–1.5 mm for fine letters or thin frame strokes</li>"
        "<li>2.0–3.0 mm for thicker designs or fast clearing</li>"
        "</ul>"
        "If a region of your design is thinner than the bit can clear, the "
        "Recommendation panel will warn you.",
    ),
    "rpm": (
        "Spindle RPM",
        "Bit rotation speed in revolutions per minute."
        "<br><br>"
        "<b>Recommended for the Lunyee 3018 PRO MAX 500W spindle</b>:"
        "<ul>"
        "<li>8,000–10,000 RPM — hardwoods (walnut, oak, maple)</li>"
        "<li>10,000–12,000 RPM — softwoods (pine, cedar)</li>"
        "<li>12,000+ RPM — MDF, plywood</li>"
        "</ul>"
        "Higher RPM = cleaner cut but more heat. Lower RPM = bigger chips, more "
        "load on the bit.",
    ),
    "feed": (
        "Feed rate",
        "How fast the bit moves <b>horizontally</b> through the wood (mm/min)."
        "<br><br>"
        "<b>Recommended</b>:"
        "<ul>"
        "<li>400–600 mm/min — hardwoods</li>"
        "<li>600–900 mm/min — softwoods, MDF</li>"
        "<li>200–400 mm/min — very small bits (≤ 1 mm) — they snap easily</li>"
        "</ul>"
        "If the spindle bogs down or you smell burning, slow down. If chips are "
        "dust-like rather than flakes, speed up or reduce RPM.",
    ),
    "plunge": (
        "Plunge rate",
        "How fast the bit moves <b>vertically</b> when entering the wood (mm/min)."
        "<br><br>"
        "<b>Recommended</b>: 30–50% of the feed rate. "
        "Endmills are designed to cut sideways, not straight down — going too "
        "fast on plunge stresses the bit's tip and can snap small bits.",
    ),
    "pocket_depth": (
        "Pocket depth",
        "How deep the pocket cavity goes — also the thickness of the visible "
        "inlay."
        "<br><br>"
        "<b>Recommended</b>: 3–5 mm.<br>"
        "Deeper inlays look more dimensional and are stronger but take longer "
        "to cut and require thicker stock. Don't exceed half your stock "
        "thickness.",
    ),
    "glue_gap": (
        "Glue gap (Z only)",
        "Extra <b>height</b> on the plug feature so it stands proud of the "
        "pocket surface after assembly. You then plane / sand the assembly "
        "flush after the glue cures."
        "<br><br>"
        "Plug feature height = pocket depth + glue gap. The proud "
        "<i>glue_gap</i> mm is your buffer for: pocket-depth variation "
        "across the workpiece (~0.05–0.10 mm on a hobby CNC), Z-zero "
        "re-zeroing precision (~0.05 mm), bit deflection (~0.05 mm), and "
        "the glue layer thickness (~0.05–0.10 mm)."
        "<br><br>"
        "<b>Recommended:</b> <b>0.4 mm</b> for typical hobby CNC + hardwood. "
        "Drop to 0.3 mm if your machine is well-tuned and you cut clean stock; "
        "raise to 0.5 mm for rough setups or softwood that compresses under "
        "the bit."
        "<br><br>"
        "Too small → plug doesn't seat or sticks up unevenly. Too large → "
        "more material to plane after glue-up, but easily recoverable."
        "<br><br>"
        "This setting is <b>Z only</b>. For XY clearance (the gap at the "
        "plug walls), see <b>XY tolerance</b> below.",
    ),
    "xy_tolerance": (
        "XY tolerance (per-side)",
        "Per-side shrinkage of the plug in X and Y so it fits the pocket "
        "with a glue layer at every wall."
        "<br><br>"
        "Plug effective size = design size − (2 × xy_tolerance). For example "
        "a 100 mm-wide design with xy_tolerance = 0.1 mm produces a plug "
        "99.8 mm wide that fits a 100 mm pocket cavity with a 0.1 mm gap on "
        "each side."
        "<br><br>"
        "<b>Recommended:</b> <b>0.1 mm</b> for typical hobby CNC. Smaller "
        "(0.05 mm) gives a tighter visual seam at the cost of fit margin; "
        "larger (0.2 mm) makes assembly easier but the glue line is more "
        "visible."
        "<br><br>"
        "Set to <b>0</b> for a wood-on-wood interference fit (no glue gap "
        "in XY) — only viable on a tightly-tuned machine with sharp bits "
        "and low-density wood."
        "<br><br>"
        "Different from <b>Glue gap</b> above, which is Z only (controls "
        "how proud the plug stands before planing flush)."
    ),
    "pass_depth": (
        "Pass depth",
        "Maximum depth removed in a single pass. If pocket depth exceeds this, "
        "the toolpath splits into multiple progressive passes."
        "<br><br>"
        "<b>Recommended</b>: 50–80% of bit diameter."
        "<ul>"
        "<li>0.8 mm bit → 0.4–0.6 mm passes</li>"
        "<li>2.0 mm bit → 1.0–1.5 mm passes</li>"
        "<li>3.0 mm bit → 1.5–2.5 mm passes</li>"
        "</ul>"
        "Deeper passes = faster but more bit stress and chatter.",
    ),
    "resolution": (
        "Resolution",
        "Polygon-to-pixel resolution for toolpath calculation. Smaller = more "
        "accurate curves but slower preview computation."
        "<br><br>"
        "<b>Recommended</b>: 0.1 mm. Going below 0.05 mm rarely improves output. "
        "Above 0.2 mm starts rounding off corners.",
    ),
    "boundary_offset": (
        "Plug boundary offset",
        "Margin of waste material to clear <b>around</b> the plug. Determines "
        "how much of the plug stock gets cleared away to free the inlay piece."
        "<br><br>"
        "<b>Recommended</b>: 5–10 mm.<br>"
        "Smaller = less wasted wood but tighter clearance for clamps. Larger = "
        "easier to handle but bigger plug stock required.",
    ),
    "pocket_thickness": (
        "Pocket stock thickness",
        "Caliper-measured thickness of the wood you're cutting the pocket into."
        "<br><br>"
        "<b>What it's used for</b>: a safety guard. The app refuses to run "
        "the pocket cut if the depth would exceed this thickness, and the "
        "<b>Pocket depth</b> spinbox max is dynamically capped to "
        "<code>thickness − safety margin</code>."
        "<br><br>"
        "<b>Set to 0 to disable the check.</b><br>"
        "Always measure your actual stock with calipers — board thickness "
        "varies between batches and over time as wood moves."
    ),
    "tool_change_z_lift": (
        "Tool-change Z lift",
        "How high the bit raises before each tool-change <b>M0</b> pause."
        "<br><br>"
        "Needs to be tall enough for you to remove the dust shroud and "
        "access the spindle collet to swap bits. Default 60 mm clears most "
        "Lunyee-style aftermarket shrouds. If you have no shroud or a "
        "smaller one, you can lower it to e.g. 30 mm; if you have an "
        "extra-tall shroud, raise it to match."
        "<br><br>"
        "Different from the regular <b>safe Z</b> (default 5 mm), which is "
        "used for between-chain rapid moves during cutting. The tool-change "
        "lift is only used at the M0 boundary between bits in a multi-bit "
        "rest-machining job."
    ),
    "stock_size": (
        "Stock dimensions (X × Y)",
        "Physical width and height of the wood you're cutting on this side. "
        "The pocket and plug typically use different pieces of wood, so each "
        "side has its own size."
        "<br><br>"
        "<b>Why it matters:</b>"
        "<ul>"
        "<li>The preview draws a dashed rectangle at the stock outline so "
        "you can see the design's position on the wood before cutting.</li>"
        "<li>The plug toolpath is <b>clipped</b> to the stock rectangle — "
        "the bit won't traverse air outside the actual wood, so cuts run "
        "faster and don't risk hitting clamps or fixturing past the wood "
        "edge.</li>"
        "<li>If the design (plus the plug boundary offset) doesn't fit, the "
        "Recommendation panel will warn you so you can resize or use bigger "
        "stock.</li>"
        "</ul>"
        "<b>Set to 0 to skip these checks</b> — useful when you're cutting "
        "on a much-larger sheet and don't want fit warnings."
    ),
    "roughing_bits": (
        "Roughing bits (rest machining)",
        "Optional. Pick larger endmills to clear the bulk of the pocket "
        "before the finishing endmill comes in for the detail work."
        "<br><br>"
        "<b>Why use this:</b> a small bit (1 mm) running an entire pocket "
        "can take 30–60 minutes. Adding a 3 mm roughing bit drops that to "
        "5–10 minutes — the 3 mm bit does the bulk of the wood removal at "
        "5× the feed rate, then the small bit only cleans up the corners "
        "and thin features it can't reach."
        "<br><br>"
        "<b>How it works:</b> bits are sorted largest-first. Each bit "
        "clears only the area the previous bit couldn't reach. Bits that "
        "wouldn't add any new clearing get auto-skipped (no wasted tool "
        "change). The G-code pauses between bits with an <code>M0</code>, "
        "and a banner appears in the app: install the new bit, jog to the "
        "wood surface, click <b>Set Z = 0</b>, then click <b>Resume</b>. "
        "Re-zeroing Z is required because hand-seating bits varies by 1–3 mm."
        "<br><br>"
        "<b>Tip:</b> only add bits you'll actually save time with. For a "
        "design where the smallest feature is 6 mm wide, just running a "
        "3 mm endmill straight through is faster than 3 mm → 1.5 mm → 1 mm."
    ),
    "size": (
        "Inlay size",
        "Physical width and height of the finished inlay on the wood — set in "
        "millimetres (or inches if you've switched units)."
        "<br><br>"
        "Width and height are <b>aspect-locked by default</b> (changing one "
        "scales the other). Click the chain link icon to unlock and stretch "
        "the design freely."
        "<br><br>"
        "<b>Fit to bed</b> snaps the design back to the largest size that "
        "fits in the 270 × 150 mm Lunyee bed without distorting it."
        "<br><br>"
        "If you load an SVG that's larger than the bed, it gets auto-scaled "
        "down on load and a status message tells you what happened."
    ),
    "plug_thickness": (
        "Plug stock thickness",
        "Caliper-measured thickness of the contrasting wood you're cutting the "
        "plug into."
        "<br><br>"
        "<b>What it's used for</b>: same safety guard, but for the plug side. "
        "The plug cut is deeper than the pocket cut by the glue gap, so the "
        "plug stock needs to be at least <code>pocket_depth + glue_gap + "
        "safety_margin</code> thick."
        "<br><br>"
        "<b>Set to 0 to disable the check.</b>"
    ),
}


def _help_button(title: str, body: str, parent: QWidget) -> QPushButton:
    btn = QPushButton("ⓘ", parent)
    btn.setFixedWidth(22)
    btn.setToolTip(f"What does '{title}' do?")
    btn.setStyleSheet(
        "QPushButton {"
        "  background: transparent;"
        "  color: #f5a623;"
        "  border: 1px solid #3a3f47;"
        "  border-radius: 4px;"
        "  font-size: 14px; font-weight: 700;"
        "  padding: 2px;"
        "}"
        "QPushButton:hover { color: #ffd87a; border-color: #f5a623; }"
    )
    btn.clicked.connect(lambda: _show_help(parent, title, body))
    return btn


def _show_help(parent: QWidget, title: str, body: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Information)
    box.setTextFormat(Qt.RichText)
    box.setText(f"<h3 style='margin:0;'>{title}</h3><div style='margin-top:8px;'>{body}</div>")
    box.setStandardButtons(QMessageBox.Close)
    box.exec()


def _wrap_with_help(widget: QWidget, key: str, parent: QWidget) -> QWidget:
    """Pack ``widget`` and a small ⓘ button into a horizontal container.

    The widget keeps its natural width (no stretch) and the trailing stretch
    absorbs any leftover sidebar space, so combos and spinboxes don't grow
    to fill the full panel.
    """
    if key not in HELP_TEXTS:
        return widget
    title, body = HELP_TEXTS[key]
    container = QWidget(parent)
    h = QHBoxLayout(container)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    h.addWidget(widget, 0)
    h.addWidget(_help_button(title, body, parent))
    h.addStretch(1)
    return container


def _help_inline_label(html: str) -> QLabel:
    lbl = QLabel(html)
    lbl.setTextFormat(Qt.RichText)
    lbl.setStyleSheet("font-size: 10px;")
    return lbl


def _color_swatch_icon(color: QColor, size: int = 14) -> QIcon:
    """Build a small filled-square icon — used as the colour indicator on
    each bit's speeds tab to match its colour in the canvas legend."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.fillRect(1, 1, size - 2, size - 2, color)
    painter.setPen(QColor("#3a3f47"))
    painter.drawRect(0, 0, size - 1, size - 1)
    painter.end()
    return QIcon(pix)


class _BitSpeedTab(QWidget):
    """One tab in the Speeds tab widget — owns the per-bit RPM/feed/plunge
    spinboxes plus their per-field "edited" flags so we know which values
    to persist in ``bit_speed_overrides``.
    """
    edited = Signal()  # any spinbox changed by the user

    def __init__(
        self,
        bit: Bit,
        default_rpm: int,
        default_feed_mm_min: float,
        default_plunge_mm_min: float,
        units: Units,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bit = bit
        self._units = units
        self._default_rpm = int(default_rpm)
        self._default_feed_mm_min = float(default_feed_mm_min)
        self._default_plunge_mm_min = float(default_plunge_mm_min)
        self._suppress = False
        self.rpm_edited = False
        self.feed_edited = False
        self.plunge_edited = False

        self.rpm_spin = QSpinBox()
        self.rpm_spin.setRange(1000, 24000)
        self.rpm_spin.setSingleStep(500)
        self.rpm_spin.setKeyboardTracking(False)
        self.rpm_spin.setSuffix(" RPM")
        self.rpm_spin.setValue(self._default_rpm)

        self.feed_spin = QDoubleSpinBox()
        self.feed_spin.setRange(50, 3000)
        self.feed_spin.setSingleStep(50)
        self.feed_spin.setDecimals(0)
        self.feed_spin.setKeyboardTracking(False)
        self.feed_spin.setSuffix(" mm/min")
        self.feed_spin.setValue(feed_to_display(self._default_feed_mm_min, units))

        self.plunge_spin = QDoubleSpinBox()
        self.plunge_spin.setRange(30, 1000)
        self.plunge_spin.setSingleStep(20)
        self.plunge_spin.setDecimals(0)
        self.plunge_spin.setKeyboardTracking(False)
        self.plunge_spin.setSuffix(" mm/min")
        self.plunge_spin.setValue(feed_to_display(self._default_plunge_mm_min, units))

        layout = QFormLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setLabelAlignment(Qt.AlignLeft)
        layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(4)

        def _row(spin, reset_fn, help_key) -> QWidget:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(4)
            rl.addWidget(spin, 0)
            reset = QPushButton("↺")
            reset.setFixedWidth(22)
            reset.setToolTip("Reset to material default")
            reset.clicked.connect(reset_fn)
            rl.addWidget(reset)
            if help_key in HELP_TEXTS:
                title, body = HELP_TEXTS[help_key]
                rl.addWidget(_help_button(title, body, self))
            rl.addStretch(1)
            return row

        layout.addRow("Spindle:", _row(self.rpm_spin, self._reset_rpm, "rpm"))
        layout.addRow("Feed rate:", _row(self.feed_spin, self._reset_feed, "feed"))
        layout.addRow("Plunge rate:", _row(self.plunge_spin, self._reset_plunge, "plunge"))
        self.hint_label = _hint("Defaults from material preset.")
        layout.addRow(self.hint_label)

        self.rpm_spin.valueChanged.connect(self._on_rpm_edited)
        self.feed_spin.valueChanged.connect(self._on_feed_edited)
        self.plunge_spin.valueChanged.connect(self._on_plunge_edited)

    # --- public API ----------------------------------------------------

    def set_units(self, units: Units) -> None:
        if units == self._units:
            return
        rpm = int(self.rpm_spin.value())
        feed_internal = feed_to_internal(self.feed_spin.value(), self._units)
        plunge_internal = feed_to_internal(self.plunge_spin.value(), self._units)
        self._units = units
        self._suppress = True
        self.feed_spin.setValue(feed_to_display(feed_internal, units))
        self.plunge_spin.setValue(feed_to_display(plunge_internal, units))
        self.rpm_spin.setValue(rpm)
        self._suppress = False

    def set_defaults(
        self, default_rpm: int, default_feed_mm_min: float, default_plunge_mm_min: float
    ) -> None:
        """Update the material-preset defaults and refresh any spinbox the
        user hasn't customised."""
        self._default_rpm = int(default_rpm)
        self._default_feed_mm_min = float(default_feed_mm_min)
        self._default_plunge_mm_min = float(default_plunge_mm_min)
        self._suppress = True
        if not self.rpm_edited:
            self.rpm_spin.setValue(self._default_rpm)
        if not self.feed_edited:
            self.feed_spin.setValue(feed_to_display(self._default_feed_mm_min, self._units))
        if not self.plunge_edited:
            self.plunge_spin.setValue(feed_to_display(self._default_plunge_mm_min, self._units))
        self._suppress = False
        self._refresh_hint()

    def apply_override(self, ov: BitSpeedOverride | None) -> None:
        """Restore the spinbox values + edited flags from a saved project."""
        self._suppress = True
        if ov is None:
            ov = BitSpeedOverride()
        if ov.rpm is not None:
            self.rpm_spin.setValue(int(ov.rpm))
            self.rpm_edited = True
        else:
            self.rpm_spin.setValue(self._default_rpm)
            self.rpm_edited = False
        if ov.feed_mm_min is not None:
            self.feed_spin.setValue(feed_to_display(ov.feed_mm_min, self._units))
            self.feed_edited = True
        else:
            self.feed_spin.setValue(feed_to_display(self._default_feed_mm_min, self._units))
            self.feed_edited = False
        if ov.plunge_mm_min is not None:
            self.plunge_spin.setValue(feed_to_display(ov.plunge_mm_min, self._units))
            self.plunge_edited = True
        else:
            self.plunge_spin.setValue(feed_to_display(self._default_plunge_mm_min, self._units))
            self.plunge_edited = False
        self._suppress = False
        self._refresh_hint()

    def current_override(self) -> BitSpeedOverride:
        """Build a BitSpeedOverride with only the user-edited fields populated."""
        return BitSpeedOverride(
            rpm=int(self.rpm_spin.value()) if self.rpm_edited else None,
            feed_mm_min=(
                feed_to_internal(self.feed_spin.value(), self._units)
                if self.feed_edited else None
            ),
            plunge_mm_min=(
                feed_to_internal(self.plunge_spin.value(), self._units)
                if self.plunge_edited else None
            ),
        )

    # Force values from outside (used by the cutting-recommendation flow).
    def force_values(
        self,
        rpm: int | None = None,
        feed_mm_min: float | None = None,
        plunge_mm_min: float | None = None,
    ) -> None:
        self._suppress = True
        if rpm is not None:
            self.rpm_spin.setValue(int(rpm))
            self.rpm_edited = True
        if feed_mm_min is not None:
            self.feed_spin.setValue(feed_to_display(feed_mm_min, self._units))
            self.feed_edited = True
        if plunge_mm_min is not None:
            self.plunge_spin.setValue(feed_to_display(plunge_mm_min, self._units))
            self.plunge_edited = True
        self._suppress = False
        self._refresh_hint()

    # --- private -------------------------------------------------------

    def _on_rpm_edited(self) -> None:
        if self._suppress:
            return
        self.rpm_edited = True
        self._refresh_hint()
        self.edited.emit()

    def _on_feed_edited(self) -> None:
        if self._suppress:
            return
        self.feed_edited = True
        self._refresh_hint()
        self.edited.emit()

    def _on_plunge_edited(self) -> None:
        if self._suppress:
            return
        self.plunge_edited = True
        self._refresh_hint()
        self.edited.emit()

    def _reset_rpm(self) -> None:
        self._suppress = True
        self.rpm_spin.setValue(self._default_rpm)
        self._suppress = False
        self.rpm_edited = False
        self._refresh_hint()
        self.edited.emit()

    def _reset_feed(self) -> None:
        self._suppress = True
        self.feed_spin.setValue(feed_to_display(self._default_feed_mm_min, self._units))
        self._suppress = False
        self.feed_edited = False
        self._refresh_hint()
        self.edited.emit()

    def _reset_plunge(self) -> None:
        self._suppress = True
        self.plunge_spin.setValue(feed_to_display(self._default_plunge_mm_min, self._units))
        self._suppress = False
        self.plunge_edited = False
        self._refresh_hint()
        self.edited.emit()

    def _refresh_hint(self) -> None:
        custom = []
        if self.rpm_edited:
            custom.append("RPM")
        if self.feed_edited:
            custom.append("feed")
        if self.plunge_edited:
            custom.append("plunge")
        if custom:
            self.hint_label.setText(f"Custom override: {', '.join(custom)}")
        else:
            self.hint_label.setText("Defaults from material preset.")


class ControlsPanel(QWidget):
    fileRequested = Signal()
    generateRequested = Signal()
    parametersChanged = Signal()
    previewRequested = Signal()
    designSizeChanged = Signal(float, float)  # width_mm, height_mm

    def __init__(
        self,
        bits: list[Bit],
        materials: list[Material],
        params: InlayParams,
        parent=None,
    ):
        super().__init__(parent)
        self._bits = bits
        self._materials = materials
        self._params = params
        self._suppress_signals = False
        self._units = Units.METRIC
        # Internal source-of-truth in mm for each spinbox value. Spinbox display
        # values are derived from these and reset whenever units change.
        self._internal_lengths_mm: dict[str, float] = {
            "pocket_depth": params.pocket_depth_mm,
            "glue_gap": params.glue_gap_mm,
            "pass_depth": params.pass_depth_mm,
            "resolution": params.resolution_mm,
            "boundary_offset": params.boundary_offset_mm,
        }
        self._internal_feeds_mm_min: dict[str, float] = {
            "feed": 0.0,
            "plunge": 0.0,
        }
        # Length spinbox (low_mm, high_mm, step_mm, decimals_mm, decimals_in)
        self._length_specs: dict[str, tuple[float, float, float, int, int]] = {
            "pocket_depth": (0.5, 25.0, 0.1, 3, 4),
            "glue_gap": (0.0, 1.0, 0.05, 3, 4),
            "pass_depth": (0.1, 5.0, 0.1, 3, 4),
            "resolution": (0.05, 0.5, 0.05, 3, 4),
            "boundary_offset": (1.0, 30.0, 0.5, 2, 4),
        }
        # RPM is unit-agnostic. Feed and plunge use feed_label.
        self._feed_specs: dict[str, tuple[float, float, float]] = {
            "feed": (50, 3000, 50),
            "plunge": (30, 1000, 20),
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(6)

        # FILE
        layout.addWidget(_section_label("Design file"))
        file_row = QWidget()
        file_h = QHBoxLayout(file_row)
        file_h.setContentsMargins(0, 0, 0, 0)
        file_h.setSpacing(4)
        self.load_button = QPushButton("📂  Load SVG")
        self.load_button.clicked.connect(self.fileRequested.emit)
        self.load_button.setMaximumWidth(140)
        file_h.addWidget(self.load_button, 0)
        self.generate_button = QPushButton("✨  Generate…")
        self.generate_button.setToolTip(
            "Generate an inlay-ready SVG from a natural-language prompt "
            "(uses Google Gemini)"
        )
        self.generate_button.clicked.connect(self.generateRequested.emit)
        self.generate_button.setMaximumWidth(140)
        file_h.addWidget(self.generate_button, 0)
        file_h.addStretch(1)
        layout.addWidget(file_row)
        self.file_label = _hint("No file loaded")
        layout.addWidget(self.file_label)

        # DESIGN
        design_box = QGroupBox("Design")
        df = QFormLayout(design_box)
        df.setLabelAlignment(Qt.AlignLeft)
        # Stack label above field when the row would otherwise overflow the
        # narrow sidebar — keeps everything visible without horizontal scroll.
        df.setRowWrapPolicy(QFormLayout.WrapLongRows)
        df.setHorizontalSpacing(6)
        df.setVerticalSpacing(4)
        # Live width/height spinboxes — replace the read-only labels.
        # Range allows everything from a 1 mm sliver up to the full bed.
        self.width_spin = self._mm_spin(1.0, 270.0, 1.0, 100.0)
        self.height_spin = self._mm_spin(1.0, 150.0, 1.0, 100.0)
        self.width_spin.setEnabled(False)
        self.height_spin.setEnabled(False)
        self.width_spin.valueChanged.connect(self._on_width_changed)
        self.height_spin.valueChanged.connect(self._on_height_changed)
        # Aspect-ratio lock — checkable button keeps width/height linked.
        self.aspect_lock_button = QPushButton("🔗")
        self.aspect_lock_button.setCheckable(True)
        self.aspect_lock_button.setChecked(True)
        self.aspect_lock_button.setFixedWidth(32)
        self.aspect_lock_button.setToolTip(
            "Aspect ratio locked — changing one dimension scales the other.\n"
            "Click to unlock and stretch the design freely."
        )
        self.aspect_lock_button.toggled.connect(self._on_aspect_lock_toggled)
        # Fit-to-bed button.
        self.fit_to_bed_button = QPushButton("Fit to bed")
        self.fit_to_bed_button.setToolTip("Scale to the largest size that fits the 270 × 150 mm bed.")
        self.fit_to_bed_button.setEnabled(False)
        self.fit_to_bed_button.setMaximumWidth(110)
        self.fit_to_bed_button.clicked.connect(self._on_fit_to_bed)

        size_row = QWidget()
        sh = QHBoxLayout(size_row)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(4)
        sh.addWidget(self.width_spin, 0)
        sh.addWidget(QLabel("×"))
        sh.addWidget(self.height_spin, 0)
        sh.addWidget(self.aspect_lock_button)
        title, body = HELP_TEXTS["size"]
        sh.addWidget(_help_button(title, body, self))
        sh.addStretch(1)
        df.addRow("Size:", size_row)
        # Fit-to-bed button on its own row, narrow.
        fit_row = QWidget()
        fh = QHBoxLayout(fit_row)
        fh.setContentsMargins(0, 0, 0, 0)
        fh.setSpacing(4)
        fh.addWidget(self.fit_to_bed_button, 0)
        fh.addStretch(1)
        df.addRow("", fit_row)

        self.origin_combo = QComboBox()
        self.origin_combo.addItem("Center", Origin.CENTER)
        self.origin_combo.addItem("Bottom-left", Origin.BOTTOM_LEFT)
        self.origin_combo.setMaximumWidth(180)
        self.origin_combo.currentIndexChanged.connect(self._on_param_change)
        df.addRow("Origin:", _wrap_with_help(self.origin_combo, "origin", self))
        layout.addWidget(design_box)
        # Aspect-ratio bookkeeping — populated when a design is loaded.
        self._design_aspect: float | None = None  # width / height (mm)
        self._suppress_size_signals = False

        # MATERIAL
        mat_box = QGroupBox("Material")
        mf = QFormLayout(mat_box)
        mf.setLabelAlignment(Qt.AlignLeft)
        mf.setRowWrapPolicy(QFormLayout.WrapLongRows)
        mf.setHorizontalSpacing(6)
        mf.setVerticalSpacing(4)
        self.pocket_material_combo = QComboBox()
        self.plug_material_combo = QComboBox()
        for combo in (self.pocket_material_combo, self.plug_material_combo):
            for m in materials:
                combo.addItem(m.name, m)
            combo.setMaximumWidth(180)
            combo.currentIndexChanged.connect(self._on_param_change)
        self._select_default(self.pocket_material_combo, "Walnut")
        self._select_default(self.plug_material_combo, "Maple")

        # Per-side caliper-measured thickness, alongside each species combo.
        self.pocket_thickness_spin = self._mm_spin(
            0.0, 50.0, 0.1, params.pocket_thickness_mm
        )
        self.plug_thickness_spin = self._mm_spin(
            0.0, 50.0, 0.1, params.plug_thickness_mm
        )
        self.pocket_thickness_spin.valueChanged.connect(self._on_param_change)
        self.plug_thickness_spin.valueChanged.connect(self._on_param_change)
        self.pocket_thickness_spin.valueChanged.connect(self._enforce_pocket_depth_limit)
        self.plug_thickness_spin.valueChanged.connect(self._enforce_pocket_depth_limit)

        # Per-side X/Y stock dimensions. 0 = unspecified. When set, the
        # canvases draw the stock outline + the pipeline clips the plug
        # complement so the bit doesn't air-cut outside the actual wood.
        self.pocket_stock_w_spin = self._mm_spin(
            0.0, 1000.0, 1.0, params.pocket_stock_width_mm
        )
        self.pocket_stock_h_spin = self._mm_spin(
            0.0, 1000.0, 1.0, params.pocket_stock_height_mm
        )
        self.plug_stock_w_spin = self._mm_spin(
            0.0, 1000.0, 1.0, params.plug_stock_width_mm
        )
        self.plug_stock_h_spin = self._mm_spin(
            0.0, 1000.0, 1.0, params.plug_stock_height_mm
        )
        for sp in (
            self.pocket_stock_w_spin, self.pocket_stock_h_spin,
            self.plug_stock_w_spin, self.plug_stock_h_spin,
        ):
            sp.valueChanged.connect(self._on_param_change)

        def _combo_row(combo, help_key) -> QWidget:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            h.addWidget(combo, 0)
            if help_key in HELP_TEXTS:
                title, body = HELP_TEXTS[help_key]
                h.addWidget(_help_button(title, body, self))
            h.addStretch(1)
            return row

        def _stock_dim_row(spin, help_key: str = "stock_size") -> QWidget:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            h.addWidget(spin, 0)
            if help_key in HELP_TEXTS:
                title, body = HELP_TEXTS[help_key]
                h.addWidget(_help_button(title, body, self))
            h.addStretch(1)
            return row

        # Wood selection, thickness, and per-axis stock dimensions each on
        # their own row so the sidebar stays narrow.
        mf.addRow("Pocket:", _combo_row(self.pocket_material_combo, "pocket_material"))
        mf.addRow("Thickness:", _wrap_with_help(self.pocket_thickness_spin, "pocket_thickness", self))
        mf.addRow("Stock W:", _stock_dim_row(self.pocket_stock_w_spin))
        mf.addRow("Stock H:", _stock_dim_row(self.pocket_stock_h_spin))
        mf.addRow("Plug:", _combo_row(self.plug_material_combo, "plug_material"))
        mf.addRow("Thickness:", _wrap_with_help(self.plug_thickness_spin, "plug_thickness", self))
        mf.addRow("Stock W:", _stock_dim_row(self.plug_stock_w_spin))
        mf.addRow("Stock H:", _stock_dim_row(self.plug_stock_h_spin))
        layout.addWidget(mat_box)

        # BITS
        bits_box = QGroupBox("Bits")
        bf = QFormLayout(bits_box)
        bf.setLabelAlignment(Qt.AlignLeft)
        bf.setRowWrapPolicy(QFormLayout.WrapLongRows)
        bf.setHorizontalSpacing(6)
        bf.setVerticalSpacing(4)

        # V-bit dropdown is only shown if the bit library has any V-bits.
        # The current straight-wall pocket-inlay technique doesn't use one;
        # this is wired up for the future V-carve technique.
        vbits_in_library = [b for b in bits if b.type == "vbit"]
        self.vbit_combo = QComboBox()
        self.vbit_combo.setMaximumWidth(180)
        if vbits_in_library:
            for b in vbits_in_library:
                self.vbit_combo.addItem(b.name, b)
            self.vbit_combo.currentIndexChanged.connect(self._on_param_change)
            self.vbit_combo.currentIndexChanged.connect(self._refresh_bit_specs)
            bf.addRow("V-bit:", _combo_row(self.vbit_combo, ""))
            self.vbit_specs_label = _hint("")
            bf.addRow(self.vbit_specs_label)
        else:
            self.vbit_specs_label = _hint("")

        self.endmill_combo = QComboBox()
        self.endmill_combo.setMaximumWidth(180)
        for b in bits:
            if b.type == "endmill":
                self.endmill_combo.addItem(b.name, b)
        self.endmill_combo.currentIndexChanged.connect(self._on_param_change)
        self.endmill_combo.currentIndexChanged.connect(self._refresh_bit_specs)
        self.endmill_combo.currentIndexChanged.connect(self._refresh_roughing_options)
        self.endmill_combo.currentIndexChanged.connect(self._refresh_speeds_tabs)
        bf.addRow("Endmill:", _wrap_with_help(self.endmill_combo, "endmill", self))
        self.endmill_specs_label = _hint("")
        bf.addRow(self.endmill_specs_label)

        # Optional roughing-bit lists per side. Empty by default — single-bit
        # mode is preserved. Each is a stack of checkboxes (one per endmill
        # larger than the finishing bit). Auto-sorted largest-first.
        self._all_endmills: list[Bit] = [b for b in bits if b.type == "endmill"]
        self.pocket_roughing_box = self._build_roughing_box("pocket")
        self.plug_roughing_box = self._build_roughing_box("plug")
        bf.addRow(self.pocket_roughing_box)
        bf.addRow(self.plug_roughing_box)
        self._refresh_roughing_options()

        self.recommendation_label = _hint("Load a design and click Preview to see a recommendation.")
        bf.addRow(self.recommendation_label)
        layout.addWidget(bits_box)
        self._refresh_bit_specs()

        # SPEEDS — one tab per bit currently selected for the cut, each with
        # its own RPM/feed/plunge override on top of the material preset.
        # Tabs are rebuilt whenever the user changes the endmill or any
        # roughing-bit checkbox.
        speeds_box = QGroupBox("Speeds (per bit)")
        sv = QVBoxLayout(speeds_box)
        sv.setContentsMargins(8, 6, 8, 6)
        sv.setSpacing(4)
        speeds_hint = _hint(
            "One tab per bit in the cut. Coloured bullet matches the bit's "
            "colour in the toolpath preview. Defaults come from the side's "
            "material preset; the ↺ button reverts a single value."
        )
        sv.addWidget(speeds_hint)
        self._speeds_tabs = QTabWidget()
        self._speeds_tabs.setDocumentMode(True)
        self._speeds_tabs.setMovable(False)
        sv.addWidget(self._speeds_tabs)
        layout.addWidget(speeds_box)
        # Map: bit_name -> _BitSpeedTab. Survives tab rebuilds so any user
        # edits on a still-selected bit are preserved when other bits change.
        self._bit_speed_tabs: dict[str, _BitSpeedTab] = {}
        # Track restore-from-project pending values so that a project loaded
        # before the bits are rebuilt still applies its overrides.
        self._pending_bit_overrides: dict[str, BitSpeedOverride] = {}
        self.pocket_material_combo.currentIndexChanged.connect(self._refresh_speeds_tabs)
        self.plug_material_combo.currentIndexChanged.connect(self._refresh_speeds_tabs)
        self._refresh_speeds_tabs()

        # INLAY
        inlay_box = QGroupBox("Inlay")
        inf = QFormLayout(inlay_box)
        inf.setLabelAlignment(Qt.AlignLeft)
        inf.setRowWrapPolicy(QFormLayout.WrapLongRows)
        inf.setHorizontalSpacing(6)
        inf.setVerticalSpacing(4)
        self.depth_spin = self._mm_spin(0.5, 25.0, 0.1, params.pocket_depth_mm)
        # Glue gap (Z) bumped to a wider range — 2 mm covers very rough
        # setups. Default 0.4 mm.
        self.glue_spin = self._mm_spin(0.0, 2.0, 0.05, params.glue_gap_mm)
        # XY tolerance — per-side plug shrink. 0–2 mm range covers everything
        # from interference fit (0) to very loose (2 mm).
        self.xy_tol_spin = self._mm_spin(0.0, 2.0, 0.05, params.xy_tolerance_mm)
        self.pass_spin = self._mm_spin(0.1, 5.0, 0.1, params.pass_depth_mm)
        self.res_spin = self._mm_spin(0.05, 0.5, 0.05, params.resolution_mm)
        self.boundary_offset_spin = self._mm_spin(1.0, 30.0, 0.5, params.boundary_offset_mm)
        # Tool-change Z lift — needs to clear dust shroud + give room to swap
        # bits. Allow up to 200 mm in case someone has a tall fixture.
        self.tool_change_z_spin = self._mm_spin(
            0.0, 200.0, 1.0, params.tool_change_z_lift_mm
        )
        for spin in (
            self.depth_spin,
            self.glue_spin,
            self.xy_tol_spin,
            self.pass_spin,
            self.res_spin,
            self.boundary_offset_spin,
            self.tool_change_z_spin,
        ):
            spin.valueChanged.connect(self._on_param_change)
        # Glue gap also feeds into the pocket-depth cap.
        self.glue_spin.valueChanged.connect(self._enforce_pocket_depth_limit)
        inf.addRow("Pocket depth:", _wrap_with_help(self.depth_spin, "pocket_depth", self))
        inf.addRow("Glue gap (Z):", _wrap_with_help(self.glue_spin, "glue_gap", self))
        inf.addRow("XY tolerance:",
                   _wrap_with_help(self.xy_tol_spin, "xy_tolerance", self))
        inf.addRow("Pass depth:", _wrap_with_help(self.pass_spin, "pass_depth", self))
        inf.addRow("Resolution:", _wrap_with_help(self.res_spin, "resolution", self))
        inf.addRow("Plug margin:",
                   _wrap_with_help(self.boundary_offset_spin, "boundary_offset", self))
        inf.addRow("Tool-change Z:",
                   _wrap_with_help(self.tool_change_z_spin, "tool_change_z_lift", self))
        layout.addWidget(inlay_box)
        # Apply default units now that all spinboxes are constructed.
        self._apply_units(self._units)
        # Apply the stock-thickness depth cap once at startup.
        self._enforce_pocket_depth_limit()

        # PREVIEW
        layout.addSpacing(4)
        self.preview_button = QPushButton("Preview Toolpaths")
        self.preview_button.setObjectName("primary")
        self.preview_button.setMaximumWidth(180)
        self.preview_button.clicked.connect(self.previewRequested.emit)
        preview_row = QWidget()
        ph = QHBoxLayout(preview_row)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(0)
        ph.addWidget(self.preview_button, 0)
        ph.addStretch(1)
        layout.addWidget(preview_row)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("summary")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        layout.addStretch(1)

    # ------------------------------------------------------------------ utils

    def _mm_spin(
        self, lo: float, hi: float, step: float, value: float
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(3)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        # Cap spinbox width so the form layout doesn't stretch them across
        # the full sidebar — keeps the panel narrow.
        spin.setMaximumWidth(110)
        return spin

    def _select_default(self, combo: QComboBox, name: str) -> None:
        for i in range(combo.count()):
            data = combo.itemData(i)
            if combo.itemText(i) == name or (data is not None and data.name == name):
                combo.setCurrentIndex(i)
                return

    def _on_param_change(self) -> None:
        if self._suppress_signals:
            return
        self.parametersChanged.emit()

    # ------------------------------------------------------------- units

    def set_units(self, units: Units) -> None:
        if units == self._units:
            return
        # Convert each spinbox display value (in old units) to mm, then re-render
        # in the new units.
        snapshot_mm = self._snapshot_values_mm()
        self._units = units
        self._apply_units(units)
        self._restore_values_mm(snapshot_mm)

    def _snapshot_values_mm(self) -> dict:
        return {
            "pocket_depth": length_to_internal(self.depth_spin.value(), self._units),
            "glue_gap": length_to_internal(self.glue_spin.value(), self._units),
            "xy_tolerance": length_to_internal(self.xy_tol_spin.value(), self._units),
            "pass_depth": length_to_internal(self.pass_spin.value(), self._units),
            "resolution": length_to_internal(self.res_spin.value(), self._units),
            "boundary_offset": length_to_internal(self.boundary_offset_spin.value(), self._units),
            "tool_change_z_lift": length_to_internal(self.tool_change_z_spin.value(), self._units),
            "pocket_thickness": length_to_internal(self.pocket_thickness_spin.value(), self._units),
            "plug_thickness": length_to_internal(self.plug_thickness_spin.value(), self._units),
            "pocket_stock_w": length_to_internal(self.pocket_stock_w_spin.value(), self._units),
            "pocket_stock_h": length_to_internal(self.pocket_stock_h_spin.value(), self._units),
            "plug_stock_w": length_to_internal(self.plug_stock_w_spin.value(), self._units),
            "plug_stock_h": length_to_internal(self.plug_stock_h_spin.value(), self._units),
            "design_width": length_to_internal(self.width_spin.value(), self._units),
            "design_height": length_to_internal(self.height_spin.value(), self._units),
        }

    def _apply_units(self, units: Units) -> None:
        """Reconfigure every spinbox's range / step / decimals / suffix to match ``units``.

        Stored values must be re-set by the caller after this — this only sets
        up the spinbox metadata, not the displayed value.
        """
        self._suppress_signals = True
        # Length spinboxes: (spin, lo_mm, hi_mm, step_mm)
        length_specs = [
            (self.depth_spin, 0.5, 25.0, 0.1),
            (self.glue_spin, 0.0, 2.0, 0.05),
            (self.xy_tol_spin, 0.0, 2.0, 0.05),
            (self.pass_spin, 0.1, 5.0, 0.1),
            (self.res_spin, 0.05, 0.5, 0.05),
            (self.boundary_offset_spin, 1.0, 30.0, 0.5),
            (self.tool_change_z_spin, 0.0, 200.0, 1.0),
            (self.pocket_thickness_spin, 0.0, 50.0, 0.1),
            (self.plug_thickness_spin, 0.0, 50.0, 0.1),
            (self.pocket_stock_w_spin, 0.0, 1000.0, 1.0),
            (self.pocket_stock_h_spin, 0.0, 1000.0, 1.0),
            (self.plug_stock_w_spin, 0.0, 1000.0, 1.0),
            (self.plug_stock_h_spin, 0.0, 1000.0, 1.0),
            (self.width_spin, 1.0, 270.0, 1.0),
            (self.height_spin, 1.0, 150.0, 1.0),
        ]
        for spin, lo_mm, hi_mm, step_mm in length_specs:
            if units == Units.METRIC:
                spin.setRange(lo_mm, hi_mm)
                spin.setSingleStep(step_mm)
                spin.setDecimals(3)
                spin.setSuffix(" mm")
            else:
                spin.setRange(lo_mm / MM_PER_INCH, hi_mm / MM_PER_INCH)
                spin.setSingleStep(max(0.001, step_mm / MM_PER_INCH))
                spin.setDecimals(4)
                spin.setSuffix(" in")
        # Per-bit Speeds tabs each own their own feed/plunge spinboxes.
        for tab in self._bit_speed_tabs.values():
            tab.set_units(units)
        self._suppress_signals = False

    def _restore_values_mm(self, snapshot: dict) -> None:
        self._suppress_signals = True
        self.depth_spin.setValue(length_to_display(snapshot["pocket_depth"], self._units))
        self.glue_spin.setValue(length_to_display(snapshot["glue_gap"], self._units))
        self.xy_tol_spin.setValue(length_to_display(snapshot["xy_tolerance"], self._units))
        self.pass_spin.setValue(length_to_display(snapshot["pass_depth"], self._units))
        self.res_spin.setValue(length_to_display(snapshot["resolution"], self._units))
        self.boundary_offset_spin.setValue(length_to_display(snapshot["boundary_offset"], self._units))
        self.tool_change_z_spin.setValue(length_to_display(snapshot["tool_change_z_lift"], self._units))
        self.pocket_thickness_spin.setValue(length_to_display(snapshot["pocket_thickness"], self._units))
        self.plug_thickness_spin.setValue(length_to_display(snapshot["plug_thickness"], self._units))
        self.pocket_stock_w_spin.setValue(length_to_display(snapshot["pocket_stock_w"], self._units))
        self.pocket_stock_h_spin.setValue(length_to_display(snapshot["pocket_stock_h"], self._units))
        self.plug_stock_w_spin.setValue(length_to_display(snapshot["plug_stock_w"], self._units))
        self.plug_stock_h_spin.setValue(length_to_display(snapshot["plug_stock_h"], self._units))
        self._suppress_size_signals = True
        self.width_spin.setValue(length_to_display(snapshot["design_width"], self._units))
        self.height_spin.setValue(length_to_display(snapshot["design_height"], self._units))
        self._suppress_size_signals = False
        self._suppress_signals = False

    @property
    def units(self) -> Units:
        return self._units

    # ---------------------------------------------------- safety: depth cap

    _SAFETY_MARGIN_MM = 0.5  # don't let the bit get within this much of stock-bottom
    _ABSOLUTE_MAX_POCKET_MM = 25.0

    def _enforce_pocket_depth_limit(self) -> None:
        """Cap the pocket-depth spinbox using whichever side's stock is the
        binding constraint:

          - Pocket cut depth  ≤  pocket_thickness − safety
          - Plug cut depth (= pocket_depth + glue) ≤ plug_thickness − safety

        The smaller of the two upper bounds wins. Either being 0 disables
        that constraint.
        """
        if not hasattr(self, "depth_spin"):
            return
        if not hasattr(self, "pocket_thickness_spin") or not hasattr(self, "plug_thickness_spin"):
            return
        u = self._units
        pocket_thick_mm = length_to_internal(self.pocket_thickness_spin.value(), u)
        plug_thick_mm = length_to_internal(self.plug_thickness_spin.value(), u)
        glue_mm = length_to_internal(self.glue_spin.value(), u)

        candidates: list[float] = [self._ABSOLUTE_MAX_POCKET_MM]
        if pocket_thick_mm > 0:
            candidates.append(max(0.1, pocket_thick_mm - self._SAFETY_MARGIN_MM))
        if plug_thick_mm > 0:
            candidates.append(max(0.1, plug_thick_mm - glue_mm - self._SAFETY_MARGIN_MM))
        max_mm = min(candidates)
        new_max_display = length_to_display(max_mm, u)
        self._suppress_signals = True
        self.depth_spin.setMaximum(new_max_display)
        if self.depth_spin.value() > new_max_display:
            self.depth_spin.setValue(new_max_display)
        self._suppress_signals = False

    # ----------------------------------------------------------- speeds

    def _active_bits_in_cut_order(self) -> list[Bit]:
        """Compute the union of bits across both sides, sorted largest-first
        so the colour ordering matches what the canvas legend uses for the
        side that has the most bits."""
        finishing = self.endmill_combo.currentData()
        if finishing is None:
            return []
        seen: dict[str, Bit] = {finishing.name: finishing}
        # Pull checked roughing bits from both sides.
        for side in ("pocket", "plug"):
            checks: dict[str, QCheckBox] = getattr(
                self, f"_{side}_roughing_checks"
            )
            for name, cb in checks.items():
                if not cb.isChecked():
                    continue
                bit = next(
                    (b for b in self._all_endmills if b.name == name), None
                )
                if bit is not None:
                    seen.setdefault(name, bit)
        return sorted(seen.values(), key=lambda b: -b.diameter_mm)

    def _bit_default_material(self, bit: Bit) -> Material | None:
        """Pick the side material that drives this bit's defaults.

        A roughing bit only used on one side gets that side's material; the
        finishing bit and any bit used on both sides default to the pocket
        material (the user typically tunes on the harder side first).
        """
        finishing = self.endmill_combo.currentData()
        in_pocket = (
            bit.name == (finishing.name if finishing else None)
            or self._pocket_roughing_checks.get(bit.name) is not None
            and self._pocket_roughing_checks[bit.name].isChecked()
        )
        in_plug = (
            bit.name == (finishing.name if finishing else None)
            or self._plug_roughing_checks.get(bit.name) is not None
            and self._plug_roughing_checks[bit.name].isChecked()
        )
        if in_pocket:
            return self.pocket_material_combo.currentData()
        if in_plug:
            return self.plug_material_combo.currentData()
        return self.pocket_material_combo.currentData()

    def _refresh_speeds_tabs(self) -> None:
        """Rebuild the Speeds tab widget so it has exactly one tab per active
        bit. Preserves prior tabs (with their edits) for bits still active;
        creates new tabs for newly-added bits, applying any pending project
        override; drops tabs whose bit is no longer selected anywhere.
        """
        if not hasattr(self, "_speeds_tabs"):
            return  # called during __init__ before construction completed
        active = self._active_bits_in_cut_order()
        active_names = {b.name for b in active}
        # Remove tabs for bits no longer in the cut.
        for name in list(self._bit_speed_tabs.keys()):
            if name not in active_names:
                tab = self._bit_speed_tabs.pop(name)
                idx = self._speeds_tabs.indexOf(tab)
                if idx != -1:
                    self._speeds_tabs.removeTab(idx)
                tab.deleteLater()
        # Add or refresh tabs in order.
        for i, bit in enumerate(active):
            mat = self._bit_default_material(bit)
            default_rpm = mat.rpm if mat else 10000
            default_feed = mat.feed_mm_min if mat else 600.0
            default_plunge = mat.plunge_mm_min if mat else 200.0
            color = color_for_bit_index(i)
            if bit.name in self._bit_speed_tabs:
                tab = self._bit_speed_tabs[bit.name]
                tab.set_defaults(default_rpm, default_feed, default_plunge)
            else:
                tab = _BitSpeedTab(
                    bit, default_rpm, default_feed, default_plunge,
                    self._units, parent=self._speeds_tabs,
                )
                tab.edited.connect(self._on_param_change)
                self._bit_speed_tabs[bit.name] = tab
                # Restore any pending override from a recently-loaded project.
                if bit.name in self._pending_bit_overrides:
                    tab.apply_override(self._pending_bit_overrides.pop(bit.name))
            # Ensure the tab sits at position i with the right title + icon.
            # Short tab text (just the diameter) keeps the tab bar narrow;
            # the full bit name is on the tooltip.
            current_idx = self._speeds_tabs.indexOf(tab)
            label = f"{bit.diameter_mm:.1f} mm"
            tooltip = f"{bit.name} (Ø {bit.diameter_mm:.2f} mm)"
            icon = _color_swatch_icon(color)
            if current_idx == -1:
                idx_now = self._speeds_tabs.insertTab(i, tab, icon, label)
                self._speeds_tabs.setTabToolTip(idx_now, tooltip)
            else:
                self._speeds_tabs.setTabIcon(current_idx, icon)
                self._speeds_tabs.setTabText(current_idx, label)
                self._speeds_tabs.setTabToolTip(current_idx, tooltip)
                if current_idx != i:
                    # Move the tab. QTabWidget has no direct move; remove + reinsert.
                    self._speeds_tabs.removeTab(current_idx)
                    idx_now = self._speeds_tabs.insertTab(i, tab, icon, label)
                    self._speeds_tabs.setTabToolTip(idx_now, tooltip)
        if not active:
            # No bits selected — show an empty placeholder so the area isn't blank.
            placeholder = self._speeds_tabs.findChild(QLabel, "noBitsHint")
            if placeholder is None:
                placeholder = QLabel("Pick a finishing endmill to set speeds.")
                placeholder.setObjectName("noBitsHint")
                placeholder.setAlignment(Qt.AlignCenter)
                placeholder.setStyleSheet("color: #9aa0a6; padding: 12px;")
                self._speeds_tabs.addTab(placeholder, "—")
        self._enforce_pocket_depth_limit()

    def apply_cutting_recommendation(
        self,
        rpm: int,
        feed_mm_min: float,
        plunge_mm_min: float,
    ) -> None:
        """Push recommended values into the FINISHING bit's speeds tab. Used
        by the cutting-advisor "apply" flow."""
        finishing = self.endmill_combo.currentData()
        if finishing is None:
            return
        tab = self._bit_speed_tabs.get(finishing.name)
        if tab is None:
            return
        tab.force_values(rpm=rpm, feed_mm_min=feed_mm_min, plunge_mm_min=plunge_mm_min)
        self._on_param_change()

    # ---------------------------------------------------- roughing bits UI

    def _build_roughing_box(self, side: str) -> QGroupBox:
        """A small group box with one checkbox per endmill larger than the
        finishing bit, plus a header explaining the rest-machining flow.

        Stored on self as ``self._<side>_roughing_checks: dict[str, QCheckBox]``
        keyed by bit name.
        """
        title_label = f"{side.title()} roughing bits"
        box = QGroupBox(title_label)
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 4, 8, 4)
        v.setSpacing(2)
        # Header row with help button.
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        hint_lbl = _hint(
            "(Faster cuts: tick larger endmills to clear bulk before the "
            "finishing bit)"
        )
        header_row.addWidget(hint_lbl, 1)
        title, body = HELP_TEXTS["roughing_bits"]
        header_row.addWidget(_help_button(title, body, self))
        v.addLayout(header_row)
        # Checkboxes container (populated by _refresh_roughing_options).
        cb_container = QWidget()
        cb_layout = QVBoxLayout(cb_container)
        cb_layout.setContentsMargins(0, 0, 0, 0)
        cb_layout.setSpacing(0)
        v.addWidget(cb_container)
        # Stash references so _refresh_roughing_options can rebuild this side.
        setattr(self, f"_{side}_roughing_layout", cb_layout)
        setattr(self, f"_{side}_roughing_checks", {})
        return box

    def _refresh_roughing_options(self) -> None:
        """Rebuild the checkbox list on each side so only endmills LARGER
        than the current finishing bit appear. Preserves any prior selection
        state for bits that remain visible.
        """
        finishing = self.endmill_combo.currentData()
        finishing_d = finishing.diameter_mm if finishing else 0.0
        for side in ("pocket", "plug"):
            layout: QVBoxLayout = getattr(self, f"_{side}_roughing_layout")
            checks: dict[str, QCheckBox] = getattr(
                self, f"_{side}_roughing_checks"
            )
            prior_selection = {
                name: cb.isChecked() for name, cb in checks.items()
            }
            # Wipe existing checkboxes.
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            checks.clear()
            # Re-add only bits larger than the finishing bit, sorted desc.
            larger = sorted(
                [b for b in self._all_endmills if b.diameter_mm > finishing_d],
                key=lambda b: -b.diameter_mm,
            )
            if not larger:
                msg = _hint(
                    "<i>No larger endmills available — pick a smaller "
                    "finishing bit to enable roughing.</i>"
                )
                layout.addWidget(msg)
                continue
            for bit in larger:
                cb = QCheckBox(f"{bit.name}  ({bit.diameter_mm:.1f} mm)")
                cb.setChecked(prior_selection.get(bit.name, False))
                cb.toggled.connect(self._on_param_change)
                cb.toggled.connect(self._refresh_speeds_tabs)
                layout.addWidget(cb)
                checks[bit.name] = cb
        self._refresh_speeds_tabs()

    def _refresh_bit_specs(self) -> None:
        vbit = self.vbit_combo.currentData()
        if vbit is not None:
            self.vbit_specs_label.setText(
                f"V-bit: {vbit.angle_deg:g}° • Ø {vbit.diameter_mm:.2f} mm • "
                f"max {vbit.max_rpm:,} RPM"
            )
        else:
            self.vbit_specs_label.setText("")
        endmill = self.endmill_combo.currentData()
        if endmill is not None:
            self.endmill_specs_label.setText(
                f"Endmill: Ø {endmill.diameter_mm:.2f} mm • "
                f"max {endmill.max_rpm:,} RPM"
            )
        else:
            self.endmill_specs_label.setText("")

    # ----------------------------------------------------------- public state

    def set_design_summary(
        self, filename: str, width_mm: float, height_mm: float
    ) -> None:
        self.file_label.setText(filename)
        self._design_aspect = width_mm / height_mm if height_mm > 0 else None
        self._suppress_size_signals = True
        self.width_spin.setEnabled(True)
        self.height_spin.setEnabled(True)
        self.fit_to_bed_button.setEnabled(True)
        self.width_spin.setValue(length_to_display(width_mm, self._units))
        self.height_spin.setValue(length_to_display(height_mm, self._units))
        self._suppress_size_signals = False

    # ----------------------------------------------------------- size editing

    def _on_aspect_lock_toggled(self, checked: bool) -> None:
        self.aspect_lock_button.setText("🔗" if checked else "🔓")
        if checked and self.width_spin.isEnabled():
            # Re-derive aspect from the current values so unlocking, stretching,
            # then re-locking treats the stretched proportion as the new norm.
            w_mm = length_to_internal(self.width_spin.value(), self._units)
            h_mm = length_to_internal(self.height_spin.value(), self._units)
            self._design_aspect = w_mm / h_mm if h_mm > 0 else None

    def _on_width_changed(self) -> None:
        if self._suppress_size_signals:
            return
        if self.aspect_lock_button.isChecked() and self._design_aspect:
            new_h_mm = length_to_internal(self.width_spin.value(), self._units) / self._design_aspect
            self._suppress_size_signals = True
            self.height_spin.setValue(length_to_display(new_h_mm, self._units))
            self._suppress_size_signals = False
        self._emit_size_changed()

    def _on_height_changed(self) -> None:
        if self._suppress_size_signals:
            return
        if self.aspect_lock_button.isChecked() and self._design_aspect:
            new_w_mm = length_to_internal(self.height_spin.value(), self._units) * self._design_aspect
            self._suppress_size_signals = True
            self.width_spin.setValue(length_to_display(new_w_mm, self._units))
            self._suppress_size_signals = False
        self._emit_size_changed()

    def _emit_size_changed(self) -> None:
        w_mm = length_to_internal(self.width_spin.value(), self._units)
        h_mm = length_to_internal(self.height_spin.value(), self._units)
        if w_mm > 0 and h_mm > 0:
            self.designSizeChanged.emit(w_mm, h_mm)

    def _on_fit_to_bed(self) -> None:
        from cnc_inlay.ui.canvas import BED_HEIGHT_MM, BED_WIDTH_MM
        if not self._design_aspect or self._design_aspect <= 0:
            return
        # Scale the original aspect ratio (not the current possibly-stretched
        # state) to fit. If the user has unlocked + stretched, that's their
        # choice but Fit goes back to true aspect.
        bed_aspect = BED_WIDTH_MM / BED_HEIGHT_MM
        if self._design_aspect >= bed_aspect:
            new_w = BED_WIDTH_MM
            new_h = BED_WIDTH_MM / self._design_aspect
        else:
            new_h = BED_HEIGHT_MM
            new_w = BED_HEIGHT_MM * self._design_aspect
        # Re-lock aspect (Fit to bed never wants distortion).
        self.aspect_lock_button.setChecked(True)
        self._suppress_size_signals = True
        self.width_spin.setValue(length_to_display(new_w, self._units))
        self.height_spin.setValue(length_to_display(new_h, self._units))
        self._suppress_size_signals = False
        self._emit_size_changed()

    def set_recommendation(self, text: str) -> None:
        self.recommendation_label.setText(text)

    def set_summary(self, text: str) -> None:
        self.summary_label.setText(text)

    # -------------------------------------------------------------- read-out

    # ----------------------------------------------------------- project I/O

    def select_pocket_material(self, name: str) -> bool:
        return self._select_default(self.pocket_material_combo, name) or False

    def select_plug_material(self, name: str) -> bool:
        return self._select_default(self.plug_material_combo, name) or False

    def select_endmill(self, name: str) -> bool:
        for i in range(self.endmill_combo.count()):
            if self.endmill_combo.itemText(i) == name:
                self.endmill_combo.setCurrentIndex(i)
                return True
        return False

    def select_vbit(self, name: str) -> bool:
        for i in range(self.vbit_combo.count()):
            if self.vbit_combo.itemText(i) == name:
                self.vbit_combo.setCurrentIndex(i)
                return True
        return False

    def apply_params(self, params: InlayParams) -> None:
        """Push an InlayParams into every spinbox + the units toggle."""
        # Switch to the project's gcode_units first so the spinbox values land
        # in the correct display unit when we set them.
        self.set_units(params.gcode_units)
        self._suppress_signals = True
        u = self._units
        self.depth_spin.setValue(length_to_display(params.pocket_depth_mm, u))
        self.glue_spin.setValue(length_to_display(params.glue_gap_mm, u))
        self.xy_tol_spin.setValue(length_to_display(params.xy_tolerance_mm, u))
        self.pass_spin.setValue(length_to_display(params.pass_depth_mm, u))
        self.res_spin.setValue(length_to_display(params.resolution_mm, u))
        self.boundary_offset_spin.setValue(length_to_display(params.boundary_offset_mm, u))
        self.tool_change_z_spin.setValue(length_to_display(params.tool_change_z_lift_mm, u))
        # New per-side fields. Fall back to the legacy stock_thickness_mm
        # if the project pre-dates the split.
        legacy = params.stock_thickness_mm
        self.pocket_thickness_spin.setValue(
            length_to_display(params.pocket_thickness_mm or legacy, u)
        )
        self.plug_thickness_spin.setValue(
            length_to_display(params.plug_thickness_mm or legacy, u)
        )
        self.pocket_stock_w_spin.setValue(length_to_display(params.pocket_stock_width_mm, u))
        self.pocket_stock_h_spin.setValue(length_to_display(params.pocket_stock_height_mm, u))
        self.plug_stock_w_spin.setValue(length_to_display(params.plug_stock_width_mm, u))
        self.plug_stock_h_spin.setValue(length_to_display(params.plug_stock_height_mm, u))
        # Origin
        for i in range(self.origin_combo.count()):
            if self.origin_combo.itemData(i) == params.origin:
                self.origin_combo.setCurrentIndex(i)
                break
        # Roughing-bit checkbox state — restore from the project. The list of
        # available checkboxes depends on the finishing endmill; refresh first
        # so we tick the boxes that actually exist.
        self._refresh_roughing_options()
        for name, cb in self._pocket_roughing_checks.items():
            cb.setChecked(name in params.pocket_roughing_bits)
        for name, cb in self._plug_roughing_checks.items():
            cb.setChecked(name in params.plug_roughing_bits)
        # Per-bit speed overrides — stash any whose tab doesn't exist yet
        # (new bits will get them on the next _refresh_speeds_tabs).
        self._pending_bit_overrides = dict(params.bit_speed_overrides)
        self._suppress_signals = False
        self._refresh_speeds_tabs()
        # Apply existing overrides to existing tabs.
        for name, ov in list(self._pending_bit_overrides.items()):
            tab = self._bit_speed_tabs.get(name)
            if tab is not None:
                tab.apply_override(ov)
                self._pending_bit_overrides.pop(name, None)

    def edit_state(self) -> tuple[bool, bool, bool]:
        # Legacy global flags — true if ANY tab has an edited field of that kind.
        rpm = any(t.rpm_edited for t in self._bit_speed_tabs.values())
        feed = any(t.feed_edited for t in self._bit_speed_tabs.values())
        plunge = any(t.plunge_edited for t in self._bit_speed_tabs.values())
        return (rpm, feed, plunge)

    def current_params(self) -> InlayParams:
        u = self._units
        return InlayParams(
            pocket_depth_mm=length_to_internal(self.depth_spin.value(), u),
            glue_gap_mm=length_to_internal(self.glue_spin.value(), u),
            xy_tolerance_mm=length_to_internal(self.xy_tol_spin.value(), u),
            pass_depth_mm=length_to_internal(self.pass_spin.value(), u),
            resolution_mm=length_to_internal(self.res_spin.value(), u),
            safe_z_mm=self._params.safe_z_mm,
            origin=self.origin_combo.currentData(),
            tool_change_z_lift_mm=length_to_internal(self.tool_change_z_spin.value(), u),
            boundary_offset_mm=length_to_internal(self.boundary_offset_spin.value(), u),
            pocket_step_over_frac=self._params.pocket_step_over_frac,
            spindle_rpm_override=None,
            feed_override_mm_min=None,
            plunge_override_mm_min=None,
            bit_speed_overrides={
                **{
                    name: ov
                    for name, ov in self._pending_bit_overrides.items()
                    if ov.rpm is not None
                    or ov.feed_mm_min is not None
                    or ov.plunge_mm_min is not None
                },
                **{
                    name: tab.current_override()
                    for name, tab in self._bit_speed_tabs.items()
                    if (ov := tab.current_override())
                    and (
                        ov.rpm is not None
                        or ov.feed_mm_min is not None
                        or ov.plunge_mm_min is not None
                    )
                },
            },
            gcode_units=u,
            pocket_thickness_mm=length_to_internal(self.pocket_thickness_spin.value(), u),
            plug_thickness_mm=length_to_internal(self.plug_thickness_spin.value(), u),
            pocket_stock_width_mm=length_to_internal(self.pocket_stock_w_spin.value(), u),
            pocket_stock_height_mm=length_to_internal(self.pocket_stock_h_spin.value(), u),
            plug_stock_width_mm=length_to_internal(self.plug_stock_w_spin.value(), u),
            plug_stock_height_mm=length_to_internal(self.plug_stock_h_spin.value(), u),
            pocket_roughing_bits=[
                name for name, cb in self._pocket_roughing_checks.items() if cb.isChecked()
            ],
            plug_roughing_bits=[
                name for name, cb in self._plug_roughing_checks.items() if cb.isChecked()
            ],
        )

    def current_vbit(self) -> Bit:
        return self.vbit_combo.currentData()

    def current_endmill(self) -> Bit:
        return self.endmill_combo.currentData()

    def current_pocket_material(self) -> Material:
        return self.pocket_material_combo.currentData()

    def current_plug_material(self) -> Material:
        return self.plug_material_combo.currentData()


def ask_design_path(parent: QWidget) -> str | None:
    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Load design",
        "",
        "SVG (*.svg);;All files (*)",
    )
    return path or None


def ask_save_path(parent: QWidget, suggested: str) -> str | None:
    path, _ = QFileDialog.getSaveFileName(
        parent,
        "Export G-code",
        suggested,
        "G-code (*.gcode *.nc);;All files (*)",
    )
    return path or None
