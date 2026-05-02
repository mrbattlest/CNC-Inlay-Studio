"""Procedural per-species wood textures for the Finished preview tab.

We generate small (256×256) tileable images with a species-tuned base colour
and grain pattern. Not photo-realistic — the goal is "the walnut + maple
pairing reads well together" not "this looks indistinguishable from a real
board". One QImage per species, cached forever after first build.

Implementation: vertical grain runs in Y; high-frequency sine waves in X
(displaced by a slowly-varying warp so the grain doesn't look mechanical),
plus a deterministic per-species noise field for tooth/figure. Final tone
is the species base colour multiplied by the brightness map.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QImage


@dataclass(frozen=True)
class _SpeciesParams:
    base_rgb: tuple[int, int, int]   # mid-tone of the species
    grain_amp: float                 # 0–0.25 — how visible the grain is
    grain_freq: float                # 6–25 — line spacing
    warp_amp: float                  # 0–0.4 — how much the lines bend
    noise_amp: float                 # 0–0.10 — high-freq texture
    seed: int                        # deterministic per-species RNG


# Tuned by eye to read as the named species when laid next to each other.
# Colour values are mid-tones; the grain pattern modulates ±~10% around them.
_SPECIES = {
    "Walnut":      _SpeciesParams((85,  55,  38), 0.10, 12.0, 0.25, 0.05,  1),
    "Maple":       _SpeciesParams((220, 195, 155), 0.05, 18.0, 0.15, 0.03,  2),
    "Oak":         _SpeciesParams((170, 130, 90),  0.14, 9.0,  0.30, 0.06,  3),
    "Cherry":      _SpeciesParams((150, 85,  60),  0.07, 14.0, 0.20, 0.04,  4),
    "Birch":       _SpeciesParams((220, 200, 165), 0.04, 20.0, 0.10, 0.03,  5),
    "Padauk":      _SpeciesParams((180, 75,  40),  0.10, 11.0, 0.25, 0.05,  6),
    "Mahogany":    _SpeciesParams((115, 60,  45),  0.08, 13.0, 0.22, 0.04,  7),
    "Purpleheart": _SpeciesParams((95,  60,  110), 0.09, 12.0, 0.28, 0.05,  8),
    "Redwood":     _SpeciesParams((155, 80,  55),  0.13, 10.0, 0.32, 0.06,  9),
    "MDF":         _SpeciesParams((180, 150, 110), 0.02, 30.0, 0.05, 0.04, 10),
    "Plywood":     _SpeciesParams((200, 165, 120), 0.10, 14.0, 0.20, 0.05, 11),
}

# Generic fallback for any species we don't have parameters for yet — neutral
# mid-tone wood with a moderate grain so the preview still renders something.
_FALLBACK = _SpeciesParams((160, 120, 80), 0.08, 14.0, 0.20, 0.05, 0)


_TILE_SIZE = 256
_cache: dict[str, QImage] = {}


def texture_for(species_name: str) -> QImage:
    """Return a cached 256×256 RGB QImage for ``species_name``.

    Unknown species fall back to a generic wood texture (still better than a
    flat colour swatch).
    """
    cached = _cache.get(species_name)
    if cached is not None:
        return cached
    params = _SPECIES.get(species_name, _FALLBACK)
    img = _render_tile(params)
    _cache[species_name] = img
    return img


def _render_tile(params: _SpeciesParams) -> QImage:
    h = w = _TILE_SIZE
    # Y runs along grain; X across grain. Grain lines = sin in X, slowly
    # displaced by sin in Y so they curve like real boards.
    yy, xx = np.meshgrid(
        np.linspace(0.0, 1.0, h, dtype=np.float32),
        np.linspace(0.0, 1.0, w, dtype=np.float32),
        indexing="ij",
    )
    warp = (
        np.sin(yy * 3.7) * 0.6 + np.sin(yy * 11.3) * 0.4
    ) * params.warp_amp
    grain = np.sin((xx + warp) * params.grain_freq * 2 * np.pi)
    rng = np.random.default_rng(params.seed)
    noise = (rng.random((h, w), dtype=np.float32) - 0.5)
    intensity = 1.0 + params.grain_amp * grain + params.noise_amp * noise
    intensity = np.clip(intensity, 0.65, 1.25)

    base = np.array(params.base_rgb, dtype=np.float32)
    rgb = intensity[..., None] * base
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    # Ensure C-contiguous so the QImage byte stride matches.
    rgb = np.ascontiguousarray(rgb)
    qimg = QImage(rgb.tobytes(), w, h, w * 3, QImage.Format_RGB888)
    # .copy() detaches from the temporary buffer so the QImage owns its data.
    return qimg.copy()
