"""Generate a black-silhouette PNG via Google Gemini.

Uses the image-generation model (``gemini-2.5-flash-image-preview``) so the
model produces actual pixels rather than trying to write SVG path commands by
hand — which is what made the previous Claude-based approach unreliable.

The prompt is shaped to elicit a clean black-on-white silhouette with a
single subject, suitable for thresholding and contour-tracing into an inlay
SVG. We don't ask the model to think about CNC tooling; we just ask for the
right pixels and handle the geometry on our side.
"""
from __future__ import annotations

from google import genai
from google.genai import types

from cnc_inlay.ai.secrets_store import get_model

# Long, prescriptive, deliberately repetitive — image models obey concrete
# visual instructions much better than abstract intent.
_PROMPT_TEMPLATE = (
    "A clean, simple, single-subject silhouette of {subject}, designed to be "
    "milled as a wood inlay. "
    "Solid black silhouette on a pure white background. "
    "No outlines, no shading, no gradients, no text, no border, no frame. "
    "Filled in pure black (#000000) with no internal detail, no negative space, no holes. "
    "Centered in the frame with comfortable white margin around all edges. "
    "Flat 2D vector-style illustration. "
    "Bold and recognizable shape — like a stencil or wood-inlay silhouette. "
    "Side profile when depicting an animal. "
    "No background scenery, no other objects, no shadow. "
    "EVERY part of the silhouette must be at least 8% of the image width thick — "
    "no thin lines, no thin connectors, no thin appendages, no filigree, "
    "no fine details. Imagine the design will be cut from wood with a 3 mm "
    "router bit; anything thinner than that will break. "
    "Prefer chunky, blocky, solid forms over delicate or detailed ones. "
    "If depicting an animal with thin legs, antennae, tails, or whiskers, "
    "thicken them or merge them into the body. "
    "Just one subject as a solid black silhouette on plain white."
)


def generate_silhouette_png(prompt: str, *, api_key: str, timeout_s: float = 120.0) -> bytes:
    """Call Gemini image generation and return the PNG bytes.

    Raises ``ValueError`` if the response contains no image data, or any
    exception bubbled up from the SDK on transport / auth failures.
    """
    if not prompt.strip():
        raise ValueError("Empty prompt")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
    )
    full_prompt = _PROMPT_TEMPLATE.format(subject=prompt.strip())
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
