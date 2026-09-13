"""One-tap Looks.

A Look is just a named edit stack, so applying one drops real, editable
layers into the photo's stack rather than baking anything in.  Every
slider stays adjustable afterwards and any layer can be deleted - which
is the whole point of a non-destructive editor, and the thing one-tap
filters usually take away.
"""
from __future__ import annotations

LOOKS: dict[str, list[tuple[str, dict]]] = {
    "Portrait": [
        ("portrait", dict(spotlight=35, skin_smoothing=45, eye_clarity=28,
                          style="Smooth")),
        ("tune", dict(brightness=6, contrast=8, saturation=-4, warmth=8)),
    ],
    "Smooth": [
        ("glamour", dict(glow=42, saturation=-12, warmth=8, style="2")),
        ("tune", dict(contrast=-6, shadows=12)),
    ],
    "Pop": [
        ("tune", dict(contrast=22, saturation=28, ambiance=24, shadows=10)),
        ("details", dict(structure=28, sharpening=18)),
    ],
    "Accentuate": [
        ("tune", dict(brightness=8, contrast=14, ambiance=32, warmth=6)),
        ("details", dict(structure=18)),
    ],
    "Faded Glow": [
        ("curves", dict(curves={"rgb": [(0.0, 0.11), (0.5, 0.53), (1.0, 0.93)]})),
        ("glamour", dict(glow=38, saturation=-22, warmth=14, style="3")),
        ("tune", dict(saturation=-14)),
    ],
    "Morning": [
        ("tune", dict(brightness=12, shadows=22, saturation=-8, warmth=22)),
        ("curves", dict(curves={"rgb": [(0.0, 0.07), (0.5, 0.55), (1.0, 0.97)]})),
        ("vignette", dict(outer_brightness=-18, size=75)),
    ],
    "Bright": [
        ("tune", dict(brightness=24, shadows=18, highlights=-12, ambiance=14)),
    ],
    "Fine Art": [
        ("black_white", dict(filter="Yellow", style="Film", contrast=14)),
        ("details", dict(structure=32)),
        ("vignette", dict(outer_brightness=-28, size=68)),
    ],
    "Push": [
        ("drama", dict(strength=42, saturation=-28, style="Drama 1")),
        ("tune", dict(contrast=12, shadows=-10)),
    ],
    "Structure": [
        ("details", dict(structure=58, sharpening=24)),
        ("tonal_contrast", dict(high_tones=22, mid_tones=38, low_tones=26)),
    ],
    "Silhouette": [
        ("tune", dict(contrast=34, shadows=-42, highlights=14, saturation=12)),
        ("curves", dict(curves={"rgb": [(0.0, 0.0), (0.32, 0.10), (0.78, 0.88),
                                        (1.0, 1.0)]})),
    ],
    "Bright Spark": [
        ("tune", dict(brightness=14, contrast=18, saturation=22, ambiance=28)),
        ("glamour", dict(glow=24, saturation=8, style="4")),
        ("details", dict(structure=16)),
    ],
    "Warm Film": [
        ("grainy_film", dict(style="X2", grain=32, style_strength=62)),
        ("tune", dict(warmth=14, shadows=10)),
    ],
    "Noir Classic": [
        ("noir", dict(style="N2", wash=18, grain=42, strength=75)),
    ],
    "Faded Vintage": [
        ("vintage", dict(style="V4", style_strength=68, center_focus=25,
                         vignette_strength=45)),
    ],
    "Cold Steel": [
        ("white_balance", dict(temperature=-26, tint=-8)),
        ("tune", dict(contrast=18, saturation=-18, shadows=-8)),
        ("tonal_contrast", dict(high_tones=28, mid_tones=22, low_tones=14)),
    ],
    "Golden Hour": [
        ("white_balance", dict(temperature=26, tint=6)),
        ("tune", dict(brightness=6, shadows=18, saturation=14, ambiance=18)),
        ("glamour", dict(glow=22, warmth=18, style="2")),
    ],
    "High Key": [
        ("tune", dict(brightness=32, contrast=-14, shadows=34, highlights=-8)),
        ("curves", dict(curves={"rgb": [(0.0, 0.14), (0.5, 0.60), (1.0, 1.0)]})),
    ],
}


def apply_look(name: str) -> list[tuple[str, dict]]:
    """The layers a Look contributes, safe to mutate afterwards."""
    return [(tid, dict(params)) for tid, params in LOOKS.get(name, [])]


def names() -> list[str]:
    return list(LOOKS)
