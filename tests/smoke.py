"""Quick end-to-end smoke check of the backend pipeline."""
from __future__ import annotations

from pathlib import Path

from cnc_inlay.core.job import InlayParams, Job
from cnc_inlay.core.pipeline import run_pipeline
from cnc_inlay.ingest.svg_loader import load_svg
from cnc_inlay.presets.bits import load_bits
from cnc_inlay.presets.materials import load_materials


def main() -> None:
    fixture = Path(__file__).parent / "fixtures" / "star.svg"
    design = load_svg(fixture)
    bits = load_bits()
    materials = load_materials()
    job = Job(
        design=design,
        bit=next(b for b in bits if b.angle_deg == 60),
        pocket_material=next(m for m in materials if m.name == "Walnut"),
        plug_material=next(m for m in materials if m.name == "Maple"),
        params=InlayParams(),
    )
    result = run_pipeline(job, bit_library=bits)
    print(f"design: {design.width_mm:.1f} x {design.height_mm:.1f} mm")
    print(f"pocket chains preview: {len(result.pocket.chains_preview)}")
    print(f"pocket chains full   : {len(result.pocket.chains_full)}")
    print(f"pocket cut length    : {result.pocket.cut_length_mm:.1f} mm")
    print(f"pocket air length    : {result.pocket.air_length_mm:.1f} mm")
    print(f"plug   chains preview: {len(result.plug.chains_preview)}")
    print(f"plug   cut length    : {result.plug.cut_length_mm:.1f} mm")
    if result.recommendation:
        r = result.recommendation
        print(f"recommendation       : {r.bit.name}  ({r.rationale})")
    print("--- pocket gcode (first 12 lines) ---")
    print("\n".join(result.pocket.gcode.splitlines()[:12]))
    print("--- plug gcode (first 12 lines) ---")
    print("\n".join(result.plug.gcode.splitlines()[:12]))


if __name__ == "__main__":
    main()
