from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from cnc_inlay.core.job import Material

USER_DATA_DIR = Path.home() / "Library" / "Application Support" / "CNC Inlay Studio"
USER_MATERIALS_PATH = USER_DATA_DIR / "materials.json"


def _from_dict(d: dict) -> Material:
    return Material(
        name=d["name"],
        feed_mm_min=float(d["feed_mm_min"]),
        plunge_mm_min=float(d["plunge_mm_min"]),
        rpm=int(d["rpm"]),
        max_doc_mm=float(d["max_doc_mm"]),
        janka_lbf=float(d.get("janka_lbf", 0.0)),
    )


def _to_dict(m: Material) -> dict:
    return {
        "name": m.name,
        "feed_mm_min": m.feed_mm_min,
        "plunge_mm_min": m.plunge_mm_min,
        "rpm": m.rpm,
        "max_doc_mm": m.max_doc_mm,
        "janka_lbf": m.janka_lbf,
    }


def load_materials() -> list[Material]:
    if USER_MATERIALS_PATH.exists():
        raw = json.loads(USER_MATERIALS_PATH.read_text())
    else:
        raw = json.loads(
            files("cnc_inlay.presets.data")
            .joinpath("materials.default.json")
            .read_text()
        )
    return [_from_dict(d) for d in raw]


def save_materials(materials: list[Material]) -> None:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_MATERIALS_PATH.write_text(
        json.dumps([_to_dict(m) for m in materials], indent=2)
    )
