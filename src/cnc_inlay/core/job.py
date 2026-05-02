from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from shapely.affinity import scale as shapely_scale
from shapely.geometry import MultiPolygon, Polygon

from cnc_inlay.core.units import Units


class Origin(Enum):
    CENTER = "center"
    BOTTOM_LEFT = "bottom_left"


@dataclass(frozen=True)
class Bit:
    name: str
    angle_deg: float
    shank_mm: float
    max_rpm: int
    type: str = "vbit"
    # Cutting diameter — for V-bits this is the bit's max diameter at the shank
    # taper; for endmills this is the flute diameter. Used for pocket kerf
    # compensation and tool footprint visualization.
    diameter_mm: float = 3.175
    # Number of cutting edges. Most 1/8" shank wood endmills are 2-flute;
    # single-flute exists for soft woods/plastics. Used for chip-load math.
    flutes: int = 2


@dataclass(frozen=True)
class Material:
    name: str
    feed_mm_min: float
    plunge_mm_min: float
    rpm: int
    max_doc_mm: float
    # Janka hardness in pounds-force — the standard rating for wood. Drives
    # the safe-chip-load calculation in the cutting advisor. Lower = softer.
    # 0 means "unknown / don't use for hardness-sensitive recommendations".
    janka_lbf: float = 0.0


@dataclass
class BitSpeedOverride:
    """Per-bit speed override. Any field set to None falls back to the global
    override on InlayParams, then to the material default.
    """
    rpm: int | None = None
    feed_mm_min: float | None = None
    plunge_mm_min: float | None = None


@dataclass
class InlayParams:
    pocket_depth_mm: float = 3.0
    # Z-axis only. Plug cut depth = pocket_depth + glue_gap_mm, so the plug
    # feature stands proud of the pocket cavity by this much; the operator
    # planes the assembly flush after glue cures. Default 0.4 mm gives
    # enough buffer to absorb hobby-CNC depth variation (~0.05–0.1 mm) +
    # glue layer (~0.05–0.1 mm) + planing stock (~0.1 mm).
    glue_gap_mm: float = 0.4
    # XY-only. Per-side inset of the plug feature so it fits the pocket with
    # a glue layer at every wall. Plug effective size = design − 2 ×
    # xy_tolerance_mm. Smaller than glue_gap because XY needs less buffer
    # (no planing involved on the sides). Default 0.1 mm absorbs typical
    # bit deflection without making the inlay feel sloppy.
    xy_tolerance_mm: float = 0.1
    pass_depth_mm: float = 1.0               # max depth of cut per pass (multi-pass split)
    safe_z_mm: float = 5.0
    resolution_mm: float = 0.1
    origin: Origin = Origin.CENTER
    boundary_offset_mm: float = 5.0          # XY margin around plug for the outer freeing cut
    pocket_step_over_frac: float = 0.5       # endmill step-over as fraction of diameter
    # Z-lift specifically before tool-change M0 pauses. Bigger than safe_z
    # because the user needs vertical clearance to remove the dust shroud
    # and access the spindle collet for the bit swap. Defaults to 60 mm,
    # which clears most aftermarket shrouds on a Lunyee 3018.
    tool_change_z_lift_mm: float = 60.0
    # Legacy global speed overrides — None means "use the material preset's
    # value". Kept for backward compatibility with v5 project files; new code
    # should use ``bit_speed_overrides`` keyed by bit name. When a bit has no
    # entry in ``bit_speed_overrides``, these globals are consulted next, then
    # the material default.
    spindle_rpm_override: int | None = None
    feed_override_mm_min: float | None = None
    plunge_override_mm_min: float | None = None
    # Per-bit speed overrides keyed by bit name. Each entry's None fields fall
    # back to the global override above, then the material default. Lets the
    # operator dial in distinct RPM/feed/plunge for every bit in a multi-bit
    # rest-machining sequence (e.g. roughing 1/8" needs different speeds from
    # finishing 1/16").
    bit_speed_overrides: dict[str, "BitSpeedOverride"] = field(default_factory=dict)
    # G-code emission units. Internal data is always mm regardless.
    gcode_units: Units = Units.METRIC
    # Per-side stock thickness (caliper-measured). 0 disables the safety
    # check on that side. Kept separate per side because the user may use
    # different thicknesses for the pocket and plug stock.
    pocket_thickness_mm: float = 0.0
    plug_thickness_mm: float = 0.0
    # Per-side stock X/Y dimensions. 0 = unspecified (skip fit checks and
    # don't clip the plug toolpath against a stock boundary). When set, the
    # canvases draw the stock outline, the pipeline clips the plug complement
    # to this rectangle so the bit never air-cuts outside the actual wood,
    # and the recommendation panel warns if the design doesn't fit.
    pocket_stock_width_mm: float = 0.0
    pocket_stock_height_mm: float = 0.0
    plug_stock_width_mm: float = 0.0
    plug_stock_height_mm: float = 0.0
    # Legacy single-value field kept only for backward compatibility with
    # older project files; new code should use the per-side values above.
    stock_thickness_mm: float = 0.0
    # Rest-machining roughing bits (largest first). The chosen Endmill is the
    # finishing bit; if these lists are empty, the side runs single-bit mode
    # exactly as before. Pocket and plug are run as separate jobs so the
    # sequences are independent.
    pocket_roughing_bits: list[str] = field(default_factory=list)
    plug_roughing_bits: list[str] = field(default_factory=list)


@dataclass
class Design:
    polygon: Polygon | MultiPolygon
    width_mm: float
    height_mm: float
    source_path: Path | None = None
    # Scaling baseline. Captured once at load time so repeated rescales derive
    # from the original geometry instead of accumulating floating-point drift.
    original_polygon: Polygon | MultiPolygon | None = None
    original_width_mm: float = 0.0
    original_height_mm: float = 0.0

    def __post_init__(self) -> None:
        if self.original_polygon is None:
            self.original_polygon = self.polygon
            self.original_width_mm = self.width_mm
            self.original_height_mm = self.height_mm

    @property
    def basename(self) -> str:
        return self.source_path.stem if self.source_path else "design"

    def rescale(self, target_width_mm: float, target_height_mm: float) -> None:
        """Replace ``polygon`` / dimensions with the original scaled to fit
        the requested width × height (independent X/Y scaling allowed; aspect
        ratio is the caller's concern).
        """
        if self.original_polygon is None or self.original_width_mm <= 0 or self.original_height_mm <= 0:
            return
        sx = target_width_mm / self.original_width_mm
        sy = target_height_mm / self.original_height_mm
        if sx <= 0 or sy <= 0:
            return
        scaled = shapely_scale(self.original_polygon, xfact=sx, yfact=sy, origin=(0, 0, 0))
        self.polygon = scaled
        minx, miny, maxx, maxy = scaled.bounds
        self.width_mm = maxx - minx
        self.height_mm = maxy - miny


@dataclass
class Job:
    design: Design
    endmill: Bit                             # clearance bit — flat-bottoms wide regions and frees plug
    pocket_material: Material
    plug_material: Material
    params: InlayParams = field(default_factory=InlayParams)
    vbit: Bit | None = None                  # optional — only used by V-carve technique (deferred)
