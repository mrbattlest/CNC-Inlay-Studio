"""Pre-cut setup guide — wood dimensions, machine placement, bit zero procedure.

Renders as a non-modal panel that lives in a right-hand dock so the user can
keep it visible while editing settings on the left.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from cnc_inlay.core.job import Bit, InlayParams, Material
from cnc_inlay.core.units import Units, fmt_dimensions, fmt_feed, fmt_length


def _format_minutes(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    minutes = int(seconds // 60)
    seconds_int = int(seconds % 60)
    return f"{minutes} min {seconds_int:02d} sec"


def _build_html(
    *,
    design_name: str,
    pocket_dim: tuple[float, float],
    plug_dim: tuple[float, float],
    endmill: Bit,
    pocket_material: Material,
    plug_material: Material,
    params: InlayParams,
    pocket_seconds: float,
    plug_seconds: float,
    units: Units,
) -> str:
    SAFETY = 0.5
    pocket_min_thickness_mm = params.pocket_depth_mm + SAFETY
    plug_min_thickness_mm = params.pocket_depth_mm + params.glue_gap_mm + SAFETY
    pocket_actual_mm = params.pocket_thickness_mm
    plug_actual_mm = params.plug_thickness_mm

    def thickness_with_warning(actual_mm: float, min_mm: float) -> str:
        if actual_mm <= 0:
            return (
                f"<span style='color:#f5a623'>"
                f"need at least {fmt_length(min_mm, units, decimals=2)}"
                f"</span> (no stock thickness configured)"
            )
        if actual_mm < min_mm:
            return (
                f"<span style='color:#e0524a; font-weight:700'>"
                f"⚠ {fmt_length(actual_mm, units, decimals=2)} TOO THIN — "
                f"need at least {fmt_length(min_mm, units, decimals=2)}"
                f"</span>"
            )
        return (
            f"<span style='color:#f5a623'>"
            f"{fmt_length(actual_mm, units, decimals=2)}"
            f"</span> (min {fmt_length(min_mm, units, decimals=2)})"
        )

    warnings_html = ""
    warning_lines: list[str] = []
    if pocket_actual_mm > 0 and pocket_actual_mm < pocket_min_thickness_mm:
        warning_lines.append(
            f"Pocket stock is only {fmt_length(pocket_actual_mm, units, decimals=2)}; "
            f"the pocket cut goes {fmt_length(params.pocket_depth_mm, units, decimals=2)} deep — "
            f"you'd be left with only "
            f"{fmt_length(max(0.0, pocket_actual_mm - params.pocket_depth_mm), units, decimals=2)} of wood underneath. "
            f"Use thicker stock or reduce pocket depth."
        )
    if plug_actual_mm > 0 and plug_actual_mm < plug_min_thickness_mm:
        warning_lines.append(
            f"Plug stock is only {fmt_length(plug_actual_mm, units, decimals=2)}; "
            f"the plug cut goes {fmt_length(params.pocket_depth_mm + params.glue_gap_mm, units, decimals=2)} deep "
            f"(includes glue gap). Plug needs at least "
            f"{fmt_length(plug_min_thickness_mm, units, decimals=2)} of stock."
        )
    if warning_lines:
        items = "".join(f"<li>{w}</li>" for w in warning_lines)
        warnings_html = (
            f"<h2>⚠ Stock thickness warning</h2>"
            f"<div style='background:#33181a; border:1px solid #e0524a; "
            f"border-radius:6px; padding:10px; margin-bottom:10px;'>"
            f"<ul style='margin:0 0 0 16px; line-height:160%; color:#e0524a;'>"
            f"{items}</ul></div>"
        )

    return f"""
<div style='font-family:-apple-system,BlinkMacSystemFont,sans-serif; font-size:12px;'>
<h1 style='font-size:16px; color:#f5a623; margin:0 0 8px 0;'>Setup for: {design_name}</h1>

<h2 style='font-size:13px; color:#e8e8ea; margin:12px 0 4px;'>1. Materials you'll need</h2>
<ul style='margin-left:16px; line-height:160%; color:#d8dce2;'>
  <li><b>Pocket wood ({pocket_material.name})</b> — at least
    <span style='color:#f5a623'><b>{fmt_dimensions(pocket_dim[0], pocket_dim[1], units)}</b></span>
    plus margin for clamps. Thickness: {thickness_with_warning(pocket_actual_mm, pocket_min_thickness_mm)}.</li>
  <li><b>Plug wood ({plug_material.name})</b> — at least
    <span style='color:#f5a623'><b>{fmt_dimensions(plug_dim[0], plug_dim[1], units)}</b></span>
    plus margin for clamps. Thickness: {thickness_with_warning(plug_actual_mm, plug_min_thickness_mm)}.</li>
  <li><b>Endmill</b> — {endmill.name}
    (Ø {fmt_length(endmill.diameter_mm, units, decimals=3)},
    shank {fmt_length(endmill.shank_mm, units, decimals=3)}).</li>
  <li><b>Wood glue</b> (Titebond II/III or similar PVA) for assembly.</li>
  <li><b>Hand plane or sander</b> for finishing the inlay flush after glue-up.</li>
</ul>

{warnings_html}

<h2 style='font-size:13px; color:#e8e8ea; margin:12px 0 4px;'>2. Mount the wood on the Lunyee bed</h2>
<ul style='margin-left:16px; line-height:160%; color:#d8dce2;'>
  <li>Clamp the wood securely with the long edge along the X axis (left↔right).</li>
  <li>Make sure the wood is flat against the bed and won't shift mid-cut.
    Add a sacrificial spoilboard underneath if you'd otherwise cut into the bed.</li>
  <li>Leave at least 10 mm of clamp-clearance around the design's bounding rectangle.</li>
  <li>Origin choice: <b>{params.origin.value}</b>.
    If 'center', jog the bit to the geometric centre of the design area on the wood.
    If 'bottom-left', jog to the front-left corner of the design area.</li>
</ul>

<h2 style='font-size:13px; color:#e8e8ea; margin:12px 0 4px;'>3. Install the bit and zero the machine</h2>
<ul style='margin-left:16px; line-height:160%; color:#d8dce2;'>
  <li>Install the <b>{endmill.name}</b> in the spindle and tighten firmly.</li>
  <li>In the Machine panel, click <b>Connect</b>. Confirm state shows <i>Idle</i>.</li>
  <li>Jog the bit to your chosen X/Y zero point on the wood. Click <b>Set XY = 0</b>.</li>
  <li>Slide a thin piece of paper between the bit and the wood.
    Jog Z down in 0.1 mm steps until the paper just barely drags. Click <b>Set Z = 0</b>.</li>
  <li>Confirm WPos shows <code>X 0.000  Y 0.000  Z 0.000</code>.</li>
  <li>Strongly recommended: click <b>👁 Dry Run</b> first — bit traces the toolpath at safe Z without cutting,
    so you can verify alignment and Z-zero without risking the wood.</li>
</ul>

<h2 style='font-size:13px; color:#e8e8ea; margin:12px 0 4px;'>4. Cutting parameters</h2>
<ul style='margin-left:16px; line-height:160%; color:#d8dce2;'>
  <li>Pocket depth: <b>{fmt_length(params.pocket_depth_mm, units, decimals=3)}</b>
    (plug cuts {fmt_length(params.glue_gap_mm, units, decimals=3)} deeper for glue gap)</li>
  <li>Spindle: <b>{pocket_material.rpm:,} RPM</b></li>
  <li>Cut feed: <b>{fmt_feed(pocket_material.feed_mm_min, units)}</b>
    &nbsp;•&nbsp; Plunge: <b>{fmt_feed(pocket_material.plunge_mm_min, units)}</b></li>
  <li>Pass depth: {fmt_length(params.pass_depth_mm, units, decimals=3)}
    (toolpath splits into multiple passes if total depth exceeds this)</li>
</ul>

<h2 style='font-size:13px; color:#e8e8ea; margin:12px 0 4px;'>5. Estimated run time</h2>
<ul style='margin-left:16px; line-height:160%; color:#d8dce2;'>
  <li><b>Pocket cut</b>: ~{_format_minutes(pocket_seconds)}</li>
  <li><b>Plug cut</b>: ~{_format_minutes(plug_seconds)}</li>
  <li><i>Estimates use programmed feed rates and assume 3000 mm/min rapid moves; actual time depends on your machine.</i></li>
</ul>

<h2 style='font-size:13px; color:#e8e8ea; margin:12px 0 4px;'>6. Pre-flight checklist</h2>
<ul style='margin-left:16px; line-height:160%; color:#d8dce2;'>
  <li>Bit is fully seated and the collet is tight.</li>
  <li>Wood is clamped — push on it; nothing should move.</li>
  <li>Workspace is clear — no offcuts, tools, or fingers in the cut path.</li>
  <li>Dust collection (if available) is on; otherwise wear a mask.</li>
  <li>Emergency stop is within reach.</li>
  <li>Stay with the machine for the duration of the cut.</li>
</ul>

<h2 style='font-size:13px; color:#e8e8ea; margin:12px 0 4px;'>7. Order of operations</h2>
<ul style='margin-left:16px; line-height:160%; color:#d8dce2;'>
  <li>Mount pocket wood. Set XY/Z zero. Click <b>Run Pocket</b>. Wait for completion.</li>
  <li>Lift bit, disconnect (or stay connected), unclamp pocket.
    Mount plug wood. Re-zero XY/Z. Click <b>Run Plug</b>.</li>
  <li>Slice the cleared backer wood off the plug if needed (band saw / table saw).</li>
  <li>Apply glue inside the pocket cavity, drop the plug in, clamp until cured.</li>
  <li>Once dry, plane or sand the plug's surrounding wood flush with the pocket surface.</li>
</ul>
</div>
"""


class SetupGuide(QWidget):
    """A non-modal setup guide. Lives inside a right-side QDockWidget; refreshes
    its contents whenever the project parameters change so the user can read
    while configuring."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(True)
        self._browser.setStyleSheet(
            "QTextBrowser { background:#1d1f24; color:#d8dce2; border:none; padding:12px; }"
        )
        layout.addWidget(self._browser)
        self.set_empty_state()

    def set_empty_state(self) -> None:
        self._browser.setHtml(
            "<div style='font-family:-apple-system,sans-serif; color:#9aa0a6; "
            "padding:16px;'>"
            "<p>Load an SVG and click <b>Preview Toolpaths</b> to see step-by-step "
            "setup instructions tailored to your project — wood dimensions, bit "
            "zero procedure, recommended speeds, and a pre-flight checklist.</p>"
            "</div>"
        )

    def refresh(
        self,
        *,
        design_name: str,
        pocket_dim: tuple[float, float],
        plug_dim: tuple[float, float],
        endmill: Bit,
        pocket_material: Material,
        plug_material: Material,
        params: InlayParams,
        pocket_seconds: float,
        plug_seconds: float,
        units: Units,
    ) -> None:
        html = _build_html(
            design_name=design_name,
            pocket_dim=pocket_dim,
            plug_dim=plug_dim,
            endmill=endmill,
            pocket_material=pocket_material,
            plug_material=plug_material,
            params=params,
            pocket_seconds=pocket_seconds,
            plug_seconds=plug_seconds,
            units=units,
        )
        self._browser.setHtml(html)
