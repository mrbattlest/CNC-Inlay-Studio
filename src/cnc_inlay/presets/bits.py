from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from cnc_inlay.core.job import Bit

USER_DATA_DIR = Path.home() / "Library" / "Application Support" / "CNC Inlay Studio"
USER_BITS_PATH = USER_DATA_DIR / "bits.json"


def _from_dict(d: dict) -> Bit:
    return Bit(
        name=d["name"],
        angle_deg=float(d["angle_deg"]),
        shank_mm=float(d["shank_mm"]),
        diameter_mm=float(d.get("diameter_mm", d["shank_mm"])),
        max_rpm=int(d["max_rpm"]),
        type=d.get("type", "vbit"),
        flutes=int(d.get("flutes", 2)),
    )


def _to_dict(b: Bit) -> dict:
    return {
        "name": b.name,
        "type": b.type,
        "angle_deg": b.angle_deg,
        "shank_mm": b.shank_mm,
        "diameter_mm": b.diameter_mm,
        "max_rpm": b.max_rpm,
        "flutes": b.flutes,
    }


def load_bits() -> list[Bit]:
    if USER_BITS_PATH.exists():
        raw = json.loads(USER_BITS_PATH.read_text())
    else:
        raw = json.loads(
            files("cnc_inlay.presets.data").joinpath("bits.default.json").read_text()
        )
    return [_from_dict(d) for d in raw]


def save_bits(bits: list[Bit]) -> None:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_BITS_PATH.write_text(json.dumps([_to_dict(b) for b in bits], indent=2))
