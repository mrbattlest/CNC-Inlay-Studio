"""Generate a black-silhouette PNG via Google Gemini.

Uses the image-generation model (``gemini-2.5-flash-image``) so the model
produces actual pixels rather than trying to write SVG path commands by
hand — which is what made the previous Claude-based approach unreliable.

Five detail levels (``DetailLevel``) drive the prompt; each maps to a
different minimum-feature-width target and a richer style anchor as the
slider climbs. The structural silhouette/centered/no-text/no-shadow rules
stay constant across levels.

  * ``BOLD``      — 8% min, no internal cutouts. Safe with any ≥6 mm bit.
  * ``SIMPLE``    — 5% min, sparse cutouts. For 3–4 mm endmills.
  * ``DETAILED``  — 3% min, several cutouts. For 2 mm endmills. (default)
  * ``RICH``      — 2% min, many cutouts. For 1.5 mm endmills.
  * ``ORNATE``    — 1% min, ornate cutouts. For 1 mm endmills + V-bit
                    pre-pass.
"""
from __future__ import annotations

from enum import Enum

from google import genai
from google.genai import types

from cnc_inlay.ai.secrets_store import get_model


class DetailLevel(str, Enum):
    BOLD = "bold"
    SIMPLE = "simple"
    DETAILED = "detailed"
    RICH = "rich"
    ORNATE = "ornate"


# Per-level prompt fragments. Three knobs change with detail:
#   * THICKNESS_RULE — minimum solid-feature width as a percent of image
#     width, plus the matching CNC bit-size hint that grounds the model.
#   * INTERNAL_RULE  — whether internal negative-space (white "holes"
#     inside the black silhouette) is forbidden, sparse, several, many,
#     or ornate.
#   * STYLE_ANCHOR   — a style reference that nudges the model toward
#     the right richness. Plain stencil at the bottom, scherenschnitte
#     / Beardsley at the top.
_THICKNESS_RULE = {
    DetailLevel.BOLD: (
        "EVERY part of the silhouette must be at least 8% of the image width "
        "thick — no thin lines, no thin connectors, no thin appendages, no "
        "filigree. Prefer chunky, blocky, solid forms. If depicting an animal "
        "with thin legs, antennae, tails, or whiskers, thicken them or merge "
        "them into the body. Imagine cutting this from wood with a 6 mm "
        "router bit; anything thinner will break."
    ),
    DetailLevel.SIMPLE: (
        "Every solid part of the silhouette and every connector must be at "
        "least 5% of the image width thick — no hair-thin lines, no "
        "filaments, no filigree. Any internal cutouts must each be at "
        "least 5% of the image width across. Imagine cutting this from "
        "wood with a 4 mm router bit; thinner features will break."
    ),
    DetailLevel.DETAILED: (
        "Every solid part of the silhouette and every connector must be at "
        "least 3% of the image width thick. Any internal cutouts must each "
        "be at least 3% of the image width across. Imagine cutting this "
        "from wood with a 2 mm router bit; thinner features will break."
    ),
    DetailLevel.RICH: (
        "Solid black portions and connectors must each be at least 2% of "
        "the image width thick. Any internal cutouts must each be at "
        "least 2% of the image width across. Imagine cutting this from "
        "wood with a 1.5 mm router bit; finer than that will not survive."
    ),
    DetailLevel.ORNATE: (
        "Solid black portions and connectors must each be at least 1% of "
        "the image width thick. Internal cutouts must each be at least 1% "
        "of the image width across. Imagine cutting this from wood with "
        "a 1 mm router bit and a V-bit pre-pass; finer than that will not "
        "survive."
    ),
}

_INTERNAL_RULE = {
    DetailLevel.BOLD: (
        "Filled in pure black (#000000) with NO internal detail, no negative "
        "space, no holes — a single solid blob."
    ),
    DetailLevel.SIMPLE: (
        "Filled in pure black (#000000) with at most one or two internal "
        "cutouts (white holes WITHIN the black silhouette) — only enough "
        "to read the most recognizable feature, like an eye on an animal "
        "or a window on a building. Otherwise solid."
    ),
    DetailLevel.DETAILED: (
        "Filled in pure black (#000000) with several well-placed internal "
        "cutouts (white holes WITHIN the black silhouette) to express "
        "character: a clear eye on an animal, a window in a building, "
        "separations between leaves on a branch. The cutouts are how the "
        "design earns its identity — use them deliberately."
    ),
    DetailLevel.RICH: (
        "Filled in pure black (#000000) with many internal cutouts (white "
        "holes WITHIN the black silhouette) expressing features and "
        "decoration: eyes, suggested scales or feathers as repeating "
        "shapes, separations between fingers/petals, paper-cut-style "
        "negative-space patterns. The cutouts are the visual highlight."
    ),
    DetailLevel.ORNATE: (
        "Filled in pure black (#000000) with rich, ornate internal cutouts "
        "(white holes WITHIN the black silhouette) expressing features and "
        "decoration densely: eyes, scales, feathers as repeating shapes, "
        "fur direction as parallel slits, ornamental flourishes, paper-cut "
        "negative-space patterns, decorative borders inside the form. The "
        "internal cutouts are the entire point — the silhouette is a "
        "frame for them."
    ),
}

_STYLE_ANCHOR = {
    DetailLevel.BOLD: (
        "Style: a bold stencil or road-sign pictogram. Maximum recognition, "
        "minimum decoration."
    ),
    DetailLevel.SIMPLE: (
        "Style: a clean stencil silhouette with one or two defining cutouts "
        "— like a logo or trail-sign icon."
    ),
    DetailLevel.DETAILED: (
        "Style: a paper-cut silhouette with deliberate internal negative "
        "space — clean, decorative, instantly recognizable."
    ),
    DetailLevel.RICH: (
        "Style: a decorative paper-cut with multiple internal cutouts — "
        "between a clean stencil and a full scherenschnitte. Patterned but "
        "still readable as one subject."
    ),
    DetailLevel.ORNATE: (
        "Style: a Victorian scherenschnitte papercut, a vintage wood-block "
        "print, or an Aubrey Beardsley silhouette — graceful, ornate, with "
        "rich internal cutout detail and decorative flourishes."
    ),
}


def _build_prompt(subject: str, level: DetailLevel) -> str:
    return (
        f"A single-subject silhouette of {subject}, designed to be milled "
        "as a wood inlay. Solid black silhouette on a pure white background. "
        "No outlines, no shading, no gradients, no text, no border, no "
        "frame, no background scenery, no other objects, no shadow. "
        "Centered in the frame with comfortable white margin around all "
        "edges. Flat 2D vector-style illustration. Side profile when "
        "depicting an animal. "
        f"{_INTERNAL_RULE[level]} "
        f"{_THICKNESS_RULE[level]} "
        f"{_STYLE_ANCHOR[level]}"
    )


def generate_silhouette_png(
    prompt: str,
    *,
    api_key: str,
    detail: DetailLevel = DetailLevel.DETAILED,
    timeout_s: float = 120.0,
) -> bytes:
    """Call Gemini image generation and return the PNG bytes.

    ``detail`` selects the prompt template — see ``DetailLevel`` for the
    tradeoff between CNC-safety and visual richness.

    Raises ``ValueError`` if the response contains no image data, or any
    exception bubbled up from the SDK on transport / auth failures.
    """
    if not prompt.strip():
        raise ValueError("Empty prompt")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
    )
    full_prompt = _build_prompt(prompt.strip(), detail)
    # response_modalities is REQUIRED for image-capable Gemini models — without
    # it the model defaults to text-only output and you get either a 400 or
    # just a textual description of the image instead of pixels.
    response = client.models.generate_content(
        model=get_model(),
        contents=full_prompt,
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
        ),
    )

    # Walk the response candidates for the first inline image part.
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return bytes(inline.data)

    # No image came back — surface whatever text/refusal the model returned
    # so the user can see why.
    text_pieces = []
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                text_pieces.append(text)
    detail = " ".join(text_pieces).strip() or "no detail provided"
    raise ValueError(f"Gemini returned no image. Model said: {detail}")
