"""One-click "Improve": a gentle automatic correction of light and colour.

It looks at the photo the way a person would - is it too dark, too flat, are
the shadows lost, is there a colour cast - and answers with the same Tune
Image and White Balance edits the editor already has, so the result can be
changed or removed like any other.
"""
from __future__ import annotations

import numpy as np

from . import ops, tools


def _clip(v: float, lo: float, hi: float) -> int:
    return int(round(max(lo, min(hi, v))))


def suggest(img: np.ndarray) -> list[tuple[str, dict]]:
    """The edits that improve ``img`` (float RGB, 0-1), as (tool id, params)."""
    small = ops.fit_within(img, 256)
    luma = ops.luma(small)
    p2, p50, p98 = (float(v) for v in np.percentile(luma, (2, 50, 98)))
    mean = float(luma.mean())

    # Light: aim the middle of the photo at a comfortable grey, then open the
    # range if it is flat, lift lost shadows and calm blown highlights.
    brightness = _clip((0.46 - p50) * 120, -30, 35)
    contrast = _clip((0.90 - (p98 - p2)) * 60, 0, 28)
    shadows = _clip((0.07 - p2) * 400, 0, 35)
    highlights = _clip((p98 - 0.96) * 600, 0, 30)

    # Colour: richer, without overdoing what is already vivid.
    chroma = float((np.max(small, axis=2) - np.min(small, axis=2)).mean())
    saturation = _clip((0.22 - chroma) * 90, 4, 18)

    out: list[tuple[str, dict]] = [("tune", {
        "brightness": brightness, "contrast": contrast, "shadows": shadows,
        "highlights": highlights, "saturation": saturation})]

    # A colour cast is only fixed when it is clear, and only part of the way.
    temp, tint = tools.auto_white_balance(img)
    if abs(temp) >= 6 or abs(tint) >= 6:
        out.append(("white_balance", {
            "temperature": _clip(temp * 0.6, -25, 25),
            "tint": _clip(tint * 0.6, -25, 25)}))
    return out
