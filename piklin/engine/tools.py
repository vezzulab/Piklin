"""The edit tools.

Every tool is a pure function ``(image, params, ctx) -> image`` plus a
declaration of its controls.  The UI is generated from those
declarations, the edit stack serialises them to JSON, and the renderer
just walks the list - so adding a tool means adding one entry here and
nothing else anywhere.

Slider ranges are -100..100 or 0..100 to match what people expect from a
mobile photo editor; the mapping into the engine's natural units happens
inside each tool, which keeps the stored edit files readable.

Resolution independence is a hard rule.  A preview is rendered at a
fraction of full size, and an effect whose radius is in pixels would
otherwise look different in the export than it did on screen.  Radii are
therefore expressed as fractions of the image's short side, or scaled by
``ctx.scale`` where a true pixel radius is meant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from . import ops
from .ops import F32, gray3
from ..i18n import N_

# -- declaration types ---------------------------------------------------
SLIDER, CHOICE, TOGGLE, POINT, TEXT, COLOR, CURVE, STROKES, IMAGE = (
    "slider", "choice", "toggle", "point", "text", "color", "curve",
    "strokes", "image")


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    kind: str = SLIDER
    lo: float = -100.0
    hi: float = 100.0
    default: Any = 0.0
    choices: tuple = ()
    step: float = 1.0
    # Sliders that read as "amount of effect" start at their midpoint in
    # Snapseed rather than at zero; this records the neutral value so the
    # UI can draw the tick and double-click can reset to it.
    neutral: float | None = None


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    group: str
    params: tuple[Param, ...]
    apply: Callable
    # global   - whole-frame colour/tone, safe to mask
    # geometry - changes pixel dimensions
    # local    - carries its own mask/stroke data
    # overlay  - draws on top (text, frames)
    kind: str = "global"
    maskable: bool = True
    描述: str = ""
    description: str = ""

    def defaults(self) -> dict:
        # A fresh copy every time: the defaults of strokes and points are
        # lists, and sharing one list meant a stroke painted in one Brush
        # layer showed up in every Brush added after it, on any photo.
        import copy
        return {p.key: copy.deepcopy(p.default) for p in self.params}


@dataclass
class Ctx:
    """What a tool needs to know about the surface it is drawing on."""
    # Whether the user allows local face detection. The Portrait and
    # Head Pose tools need it; a privacy switch that the tools ignored
    # would be worse than not offering one.
    face_detection: bool = True
    # preview width / full width.  1.0 when exporting.
    scale: float = 1.0
    full_size: tuple[int, int] = (0, 0)
    # stable per-instance seed so procedural texture does not crawl
    # between the preview and the export, or while a slider moves
    seed: int = 7
    preview: bool = True
    # Draft renders trade quality for latency while a slider is moving.
    # Tools that do something genuinely expensive (inpainting an exposed
    # edge, a large bokeh kernel) substitute a cheap approximation and
    # the real result arrives when the drag settles.
    draft: bool = False

    def px(self, pixels: float) -> int:
        """Convert a full-resolution pixel radius to this surface."""
        return max(1, int(round(pixels * self.scale)))


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.id] = tool
    return tool


def n1(v: float) -> float:
    """-100..100 slider -> -1..1."""
    return float(v) / 100.0


def u1(v: float) -> float:
    """0..100 slider -> 0..1."""
    return max(0.0, float(v)) / 100.0


# ========================================================================
# Tune Image
# ========================================================================
def _tune(img, p, ctx):
    out = img
    if p["brightness"]:
        out = ops.brightness(out, n1(p["brightness"]) * 0.8)
    if p["contrast"]:
        out = ops.contrast(out, n1(p["contrast"]))
    if p["ambiance"]:
        out = ops.ambiance(out, n1(p["ambiance"]))
    if p["shadows"] or p["highlights"]:
        out = ops.shadows_highlights(out, n1(p["shadows"]) * 0.9,
                                     -n1(p["highlights"]) * 0.9)
    if p["saturation"]:
        s = n1(p["saturation"])
        # negative goes to true grey, positive stays vibrance-weighted so
        # skies saturate before skin does
        out = ops.saturation(out, s) if s < 0 else ops.vibrance(out, s * 1.25)
    if p["warmth"]:
        out = ops.warmth(out, n1(p["warmth"]))
    return out


register(Tool(
    id="tune", name=N_("Tune Image"), group=N_("Adjust"), kind="global",
    description=N_("Adjust brightness, contrast, color and more."),
    params=(
        Param("brightness", N_("Brightness")),
        Param("contrast", N_("Contrast")),
        Param("saturation", N_("Saturation")),
        Param("ambiance", N_("Ambiance")),
        Param("highlights", N_("Highlights")),
        Param("shadows", N_("Shadows")),
        Param("warmth", N_("Warmth")),
    ),
    apply=_tune))


# ========================================================================
# Details
# ========================================================================
def _details(img, p, ctx):
    out = img
    if p["structure"]:
        out = ops.structure(out, n1(p["structure"]))
    if p["sharpening"]:
        out = ops.sharpen(out, u1(p["sharpening"]),
                          radius=max(0.6, 1.1 * ctx.scale + 0.4))
    return out


register(Tool(
    id="details", name=N_("Details"), group=N_("Adjust"), kind="global",
    description=N_("Bring out texture and make the photo sharper."),
    params=(
        Param("structure", N_("Structure")),
        Param("sharpening", N_("Sharpening"), lo=0, hi=100, default=0),
    ),
    apply=_details))


# ========================================================================
# Curves
# ========================================================================
CURVE_PRESETS = {
    N_("Neutral"): [(0, 0), (1, 1)],
    N_("Soft Contrast"): [(0, 0), (0.25, 0.21), (0.75, 0.79), (1, 1)],
    N_("Hard Contrast"): [(0, 0), (0.25, 0.14), (0.75, 0.86), (1, 1)],
    N_("Bright"): [(0, 0.04), (0.5, 0.58), (1, 1)],
    N_("Dark"): [(0, 0), (0.5, 0.42), (1, 0.96)],
    N_("Film"): [(0, 0.05), (0.25, 0.22), (0.75, 0.80), (1, 0.96)],
    N_("Faded"): [(0, 0.12), (0.5, 0.52), (1, 0.92)],
    N_("Matte"): [(0, 0.10), (0.3, 0.34), (0.7, 0.74), (1, 0.95)],
    N_("Lift Shadows"): [(0, 0.09), (0.35, 0.38), (1, 1)],
    N_("Crush Blacks"): [(0, 0), (0.18, 0.06), (0.6, 0.62), (1, 1)],
}


def _curves(img, p, ctx):
    out = img
    curves = p.get("curves") or {}
    # RGB (composite) first, then per-channel, matching every other editor
    rgb = curves.get("rgb")
    if rgb and len(rgb) >= 2:
        out = ops.apply_lut(out, ops.spline_lut(rgb, ops.LUT_N))
    for i, ch in enumerate(("r", "g", "b")):
        pts = curves.get(ch)
        if pts and len(pts) >= 2:
            lut = ops.spline_lut(pts, ops.LUT_N)
            out = out.copy()
            out[..., i] = ops.apply_lut(out[..., i], lut)
    lum = curves.get("luma")
    if lum and len(lum) >= 2:
        l = ops.luma(out)
        new_l = ops.apply_lut(l, ops.spline_lut(lum, ops.LUT_N))
        out = np.clip(out * gray3((new_l + ops.EPS) / (l + ops.EPS)), 0, 1)
    return out.astype(F32)


register(Tool(
    id="curves", name=N_("Curves"), group=N_("Adjust"), kind="global",
    description=N_("Fine-tune light and color with curves."),
    params=(
        Param("curves", N_("Curves"), kind=CURVE,
              default={"rgb": [(0.0, 0.0), (1.0, 1.0)]}),
        Param("preset", N_("Preset"), kind=CHOICE, default=N_("Neutral"),
              choices=tuple(CURVE_PRESETS)),
    ),
    apply=_curves))


# ========================================================================
# White Balance
# ========================================================================
def _white_balance(img, p, ctx):
    out = img
    pick = p.get("picker")
    if pick and pick != [0, 0]:
        # neutralise the sampled patch: divide by its colour, renormalised
        h, w = img.shape[:2]
        px = int(np.clip(pick[0], 0, 1) * (w - 1))
        py = int(np.clip(pick[1], 0, 1) * (h - 1))
        y0, y1 = max(0, py - 3), min(h, py + 4)
        x0, x1 = max(0, px - 3), min(w, px + 4)
        patch = ops.srgb_to_linear(img[y0:y1, x0:x1]).reshape(-1, 3).mean(0)
        patch = np.maximum(patch, 1e-4)
        gain = (float(np.dot(patch, ops.LUMA_W)) / patch).astype(F32)
        out = ops.linear_to_srgb(np.clip(ops.srgb_to_linear(out) * gain, 0, 1))
    if p["temperature"] or p["tint"]:
        out = ops.temperature_tint(out, n1(p["temperature"]), n1(p["tint"]))
    return out


def auto_white_balance(img: np.ndarray) -> tuple[float, float]:
    """Estimate temperature/tint corrections via grey-world on highlights.

    Averaging the whole frame fails on a picture that is mostly one
    colour; weighting toward bright, low-saturation pixels finds
    something that was actually near-neutral in the scene.
    """
    lin = ops.srgb_to_linear(ops.fit_within(img, 256))
    l = ops.luma(lin)
    sat = np.max(lin, axis=2) - np.min(lin, axis=2)
    w = np.clip(l, 0, 1) ** 1.5 * np.clip(1.0 - sat * 3.0, 0.0, 1.0)
    if float(w.sum()) < 1e-3:
        return 0.0, 0.0
    avg = (lin * gray3(w)).reshape(-1, 3).sum(0) / float(w.sum())
    avg = np.maximum(avg, 1e-4)
    # positive temp slider warms, so a blue-heavy average needs +temp
    temp = float(np.clip((avg[2] - avg[0]) / max(avg.mean(), 1e-4) * 1.6, -1, 1))
    tint = float(np.clip((avg[1] - (avg[0] + avg[2]) / 2)
                         / max(avg.mean(), 1e-4) * -1.6, -1, 1))
    return temp * 100.0, tint * 100.0


register(Tool(
    id="white_balance", name=N_("White Balance"), group=N_("Adjust"), kind="global",
    description=N_("Make colors warmer or cooler, or fix a color cast."),
    params=(
        Param("temperature", N_("Temperature")),
        Param("tint", N_("Tint")),
        Param("picker", N_("Pick something gray"), kind=POINT, default=[0, 0]),
    ),
    apply=_white_balance))


# ========================================================================
# Tonal Contrast
# ========================================================================
TONAL_STYLES = {
    "S1": dict(high=25, mid=35, low=25), "S2": dict(high=40, mid=25, low=15),
    "S3": dict(high=15, mid=45, low=35), "H1": dict(high=55, mid=30, low=10),
    "H2": dict(high=70, mid=20, low=5),  "H3": dict(high=45, mid=55, low=25),
}


def _tonal_contrast(img, p, ctx):
    """Boost local contrast independently in three tonal bands.

    Each band gets its own edge-aware detail layer, masked to the part of
    the tone range it owns, so shadows can be crunched without touching a
    sky - which a single clarity slider cannot do.
    """
    high, mid, low = n1(p["high_tones"]), n1(p["mid_tones"]), n1(p["low_tones"])
    if not any((high, mid, low)):
        return img
    h, w = img.shape[:2]
    r = max(2, int(min(h, w) * 0.02))
    l = ops.luma(img)
    base = ops.guided_filter(l, l, r, 0.008)
    detail = l - base

    m_low = np.clip(1.0 - base * 2.2, 0, 1) ** 1.4
    m_high = np.clip((base - 0.55) * 2.2, 0, 1) ** 1.4
    m_mid = np.clip(1.0 - m_low - m_high, 0, 1)

    gain = m_low * F32(low) + m_mid * F32(mid) + m_high * F32(high)
    new_l = l + detail * gain * F32(2.4)

    # protection sliders pull the extremes back toward the original
    ps, ph = u1(p["protect_shadows"]), u1(p["protect_highlights"])
    if ps:
        new_l = new_l * (1 - m_low * ps) + l * (m_low * ps)
    if ph:
        new_l = new_l * (1 - m_high * ph) + l * (m_high * ph)

    new_l = np.clip(new_l, 0, 1)
    return np.clip(img * gray3((new_l + ops.EPS) / (l + ops.EPS)), 0, 1)


register(Tool(
    id="tonal_contrast", name=N_("Tonal Contrast"), group=N_("Effects"), kind="global",
    description=N_("Add punch to the dark, middle and bright parts separately."),
    params=(
        Param("high_tones", N_("High Tones"), lo=-100, hi=100, default=25),
        Param("mid_tones", N_("Mid Tones"), lo=-100, hi=100, default=35),
        Param("low_tones", N_("Low Tones"), lo=-100, hi=100, default=25),
        Param("protect_shadows", N_("Protect Shadows"), lo=0, hi=100, default=0),
        Param("protect_highlights", N_("Protect Highlights"), lo=0, hi=100, default=0),
        Param("style", N_("Style"), kind=CHOICE, default="S1",
              choices=tuple(TONAL_STYLES)),
    ),
    apply=_tonal_contrast))


# ========================================================================
# HDR Scape
# ========================================================================
HDR_STYLES = {
    N_("Nature"): dict(strength=45, brightness=0, saturation=10, smoothing=50),
    N_("People"): dict(strength=35, brightness=5, saturation=0, smoothing=70),
    N_("Fine"):   dict(strength=55, brightness=0, saturation=5, smoothing=30),
    N_("Strong"): dict(strength=80, brightness=-5, saturation=20, smoothing=25),
}


def _hdr(img, p, ctx):
    """Tone-mapped local-contrast expansion.

    Compresses the global tone range while amplifying local detail - the
    look of a bracketed HDR merge, produced from one exposure.
    """
    strength = u1(p["strength"])
    if strength <= 0.001:
        return img
    h, w = img.shape[:2]
    smooth = u1(p["smoothing"])
    r = max(3, int(min(h, w) * (0.02 + 0.10 * smooth)))
    eps = 0.002 + 0.02 * smooth

    l = np.maximum(ops.luma(img), ops.EPS)
    base = np.maximum(ops.guided_filter(l, l, r, eps), ops.EPS)
    detail = l / base                                   # multiplicative detail

    # compress the base range, expand the detail
    comp = np.power(base, F32(1.0 - 0.55 * strength))
    det = np.power(detail, F32(1.0 + 1.25 * strength))
    new_l = np.clip(comp * det, 0, 1)

    out = np.clip(img * gray3((new_l + ops.EPS) / (l + ops.EPS)), 0, 1)
    # HDR pulls colour out of the midtones; give some back
    out = ops.vibrance(out, 0.18 * strength + n1(p["saturation"]))
    if p["brightness"]:
        out = ops.brightness(out, n1(p["brightness"]) * 0.6)
    return out


register(Tool(
    id="hdr", name=N_("HDR Scape"), group=N_("Effects"), kind="global",
    description=N_("Bring out detail in both bright and dark areas."),
    params=(
        Param("strength", N_("Filter Strength"), lo=0, hi=100, default=45),
        Param("brightness", N_("Brightness")),
        Param("saturation", N_("Saturation")),
        Param("smoothing", N_("Smoothing"), lo=0, hi=100, default=50),
        Param("style", N_("Style"), kind=CHOICE, default=N_("Nature"),
              choices=tuple(HDR_STYLES)),
    ),
    apply=_hdr))


# ========================================================================
# Glamour Glow
# ========================================================================
GLAMOUR_STYLES = {
    "1": dict(glow=35, saturation=0, warmth=0),
    "2": dict(glow=55, saturation=-15, warmth=10),
    "3": dict(glow=70, saturation=-30, warmth=20),
    "4": dict(glow=45, saturation=15, warmth=-10),
    "5": dict(glow=85, saturation=-40, warmth=25),
}


def _glamour(img, p, ctx):
    """Orton-style bloom: a blurred, brightened copy screened back on.

    The blur radius is a fraction of the frame, so the glow covers the
    same proportion of the picture at preview and export resolution.
    """
    glow = u1(p["glow"])
    if glow <= 0.001:
        return img
    h, w = img.shape[:2]
    sigma = max(1.5, min(h, w) * 0.022)
    bright = ops.brightness(img, 0.22 * glow)
    blurred = ops.gaussian_blur(bright, sigma)
    out = ops.blend(img, blurred, "screen", 0.55 * glow)
    # the bloom washes out contrast; a touch back keeps it from going flat
    out = ops.contrast(out, 0.10 * glow)
    if p["saturation"]:
        out = ops.saturation(out, n1(p["saturation"]))
    if p["warmth"]:
        out = ops.warmth(out, n1(p["warmth"]))
    return out


register(Tool(
    id="glamour", name=N_("Glamour Glow"), group=N_("Effects"), kind="global",
    description=N_("Add a soft, dreamy glow."),
    params=(
        Param("glow", N_("Glow"), lo=0, hi=100, default=35),
        Param("saturation", N_("Saturation")),
        Param("warmth", N_("Warmth")),
        Param("style", N_("Style"), kind=CHOICE, default="1",
              choices=tuple(GLAMOUR_STYLES)),
    ),
    apply=_glamour))


# ========================================================================
# Drama
# ========================================================================
DRAMA_STYLES = {
    N_("Drama 1"): dict(strength=50, saturation=-20, bright=0, dark=0),
    N_("Drama 2"): dict(strength=70, saturation=-40, bright=0, dark=10),
    N_("Bright 1"): dict(strength=45, saturation=-10, bright=25, dark=0),
    N_("Bright 2"): dict(strength=60, saturation=-25, bright=40, dark=0),
    N_("Dark 1"): dict(strength=55, saturation=-30, bright=0, dark=30),
    N_("Dark 2"): dict(strength=75, saturation=-50, bright=-10, dark=45),
}


def _drama(img, p, ctx):
    """Heavy local-contrast grunge with desaturated, crushed tone."""
    strength = u1(p["strength"])
    if strength <= 0.001:
        return img
    style = DRAMA_STYLES.get(p.get("style", "Drama 1"), DRAMA_STYLES["Drama 1"])
    h, w = img.shape[:2]

    l = ops.luma(img)
    acc = l.copy()
    residual = l
    base_r = max(2, int(min(h, w) * 0.010))
    for i, wgt in enumerate((1.0, 0.75, 0.5, 0.3)):
        r = base_r * (2 ** i)
        if r >= min(h, w) // 2:
            break
        sm = ops.guided_filter(residual, residual, int(r), 0.004 + 0.006 * i)
        acc = acc + (residual - sm) * F32(strength * 1.6 * wgt)
        residual = sm
    new_l = np.clip(acc, 0, 1)
    out = np.clip(img * gray3((new_l + ops.EPS) / (l + ops.EPS)), 0, 1)

    out = ops.saturation(out, n1(p["saturation"]))
    if style["bright"]:
        out = ops.brightness(out, style["bright"] / 100.0 * strength)
    if style["dark"]:
        out = ops.shadows_highlights(out, -style["dark"] / 100.0 * strength, 0)
    return ops.contrast(out, 0.18 * strength)


register(Tool(
    id="drama", name=N_("Drama"), group=N_("Effects"), kind="global",
    description=N_("A bold, moody look with strong detail."),
    params=(
        Param("strength", N_("Filter Strength"), lo=0, hi=100, default=50),
        Param("saturation", N_("Saturation"), default=-20),
        Param("style", N_("Style"), kind=CHOICE, default=N_("Drama 1"),
              choices=tuple(DRAMA_STYLES)),
    ),
    apply=_drama))


# ========================================================================
# Vignette
# ========================================================================
def _vignette(img, p, ctx):
    outer, inner = n1(p["outer_brightness"]), n1(p["inner_brightness"])
    if not outer and not inner:
        return img
    h, w = img.shape[:2]
    cx, cy = p.get("center", [0.5, 0.5])
    size = u1(p.get("size", 62)) or 0.62
    m_out = ops.vignette_mask((h, w), float(cx), float(cy), size)
    out = img
    if outer:
        # darkening multiplies; brightening screens, so highlights survive
        if outer < 0:
            out = out * gray3(1.0 + m_out * F32(outer))
        else:
            out = 1.0 - (1.0 - out) * gray3(1.0 - m_out * F32(outer) * 0.85)
    if inner:
        m_in = 1.0 - m_out
        if inner < 0:
            out = out * gray3(1.0 + m_in * F32(inner))
        else:
            out = 1.0 - (1.0 - out) * gray3(1.0 - m_in * F32(inner) * 0.85)
    return np.clip(out, 0, 1).astype(F32)


register(Tool(
    id="vignette", name=N_("Vignette"), group=N_("Effects"), kind="global",
    description=N_("Darken or brighten the edges of the photo."),
    params=(
        Param("outer_brightness", N_("Outer Brightness"), default=-50),
        Param("inner_brightness", N_("Inner Brightness"), default=0),
        Param("size", N_("Size"), lo=10, hi=100, default=62),
        Param("center", N_("Center"), kind=POINT, default=[0.5, 0.5]),
    ),
    apply=_vignette))


# ========================================================================
# Shared grading helpers for the film-look tools
# ========================================================================
def split_tone(img: np.ndarray, shadow_rgb, highlight_rgb,
               strength: float = 1.0) -> np.ndarray:
    """Tint shadows and highlights toward different colours.

    This is what separates a convincing film emulation from a global
    colour cast: real stocks shift the toe and the shoulder in opposite
    directions.
    """
    if strength <= 0.001:
        return img
    l = ops.luma(img)
    sh = gray3(np.clip(1.0 - l * 1.7, 0, 1) ** 1.2)
    hi = gray3(np.clip((l - 0.42) * 1.7, 0, 1) ** 1.2)
    s = np.asarray(shadow_rgb, F32) - F32(0.5)
    h = np.asarray(highlight_rgb, F32) - F32(0.5)
    out = img + (sh * s + hi * h) * F32(strength * 0.55)
    return np.clip(out, 0, 1).astype(F32)


def channel_mix_mono(img: np.ndarray, weights) -> np.ndarray:
    """Monochrome conversion through a coloured filter.

    A red filter darkens a blue sky and lightens skin, exactly as the
    glass filter in front of a black-and-white lens did.
    """
    w = np.asarray(weights, F32)
    w = w / max(float(w.sum()), 1e-4)
    return np.clip(img @ w, 0, 1).astype(F32)


BW_FILTERS = {
    N_("Neutral"): (0.2126, 0.7152, 0.0722),
    N_("Red"):     (0.80, 0.20, 0.02),
    N_("Orange"):  (0.60, 0.38, 0.04),
    N_("Yellow"):  (0.42, 0.53, 0.07),
    N_("Green"):   (0.16, 0.74, 0.12),
    N_("Blue"):    (0.10, 0.28, 0.64),
}


def _apply_grade(img, grade, ctx, strength=1.0, seed=7):
    """Run one named film grade at a given strength."""
    out = img
    if grade.get("curve"):
        graded = ops.apply_lut(out, ops.spline_lut(grade["curve"], ops.LUT_N))
        out = out * F32(1 - strength) + graded * F32(strength)
    if grade.get("split"):
        out = split_tone(out, grade["split"][0], grade["split"][1],
                         grade["split"][2] * strength)
    if grade.get("saturation"):
        out = ops.saturation(out, grade["saturation"] * strength)
    if grade.get("contrast"):
        out = ops.contrast(out, grade["contrast"] * strength)
    if grade.get("warmth"):
        out = ops.warmth(out, grade["warmth"] * strength)
    return np.clip(out, 0, 1).astype(F32)


# ========================================================================
# Vintage
# ========================================================================
VINTAGE_STYLES = {
    "V1": dict(curve=[(0, .10), (.5, .52), (1, .93)], split=((.52, .48, .42), (.55, .52, .44), .8), saturation=-.25, contrast=.05, warmth=.15),
    "V2": dict(curve=[(0, .14), (.5, .50), (1, .90)], split=((.44, .48, .56), (.58, .54, .46), .9), saturation=-.35, contrast=.10, warmth=.05),
    "V3": dict(curve=[(0, .06), (.5, .55), (1, .96)], split=((.55, .46, .40), (.52, .53, .50), .7), saturation=-.15, contrast=.15, warmth=.25),
    "V4": dict(curve=[(0, .18), (.5, .48), (1, .86)], split=((.48, .50, .54), (.60, .52, .40), 1.0), saturation=-.45, contrast=.00, warmth=.10),
    "V5": dict(curve=[(0, .08), (.5, .53), (1, .94)], split=((.56, .50, .44), (.48, .52, .58), .8), saturation=-.20, contrast=.12, warmth=-.10),
    "V6": dict(curve=[(0, .12), (.5, .51), (1, .91)], split=((.42, .46, .55), (.56, .53, .45), .85), saturation=-.30, contrast=.08, warmth=.00),
    "V7": dict(curve=[(0, .05), (.5, .56), (1, .97)], split=((.58, .48, .38), (.54, .54, .48), .75), saturation=-.10, contrast=.18, warmth=.30),
    "V8": dict(curve=[(0, .20), (.5, .47), (1, .84)], split=((.46, .49, .53), (.62, .51, .38), 1.0), saturation=-.50, contrast=-.05, warmth=.18),
    "V9": dict(curve=[(0, .09), (.5, .54), (1, .95)], split=((.50, .52, .50), (.56, .50, .44), .6), saturation=-.18, contrast=.14, warmth=.08),
    "V10": dict(curve=[(0, .15), (.5, .49), (1, .88)], split=((.54, .47, .46), (.50, .54, .52), .9), saturation=-.38, contrast=.06, warmth=.12),
    "V11": dict(curve=[(0, .07), (.5, .55), (1, .96)], split=((.44, .50, .58), (.58, .52, .42), .8), saturation=-.22, contrast=.16, warmth=-.05),
    "V12": dict(curve=[(0, .16), (.5, .50), (1, .89)], split=((.56, .52, .42), (.52, .50, .52), .95), saturation=-.42, contrast=.04, warmth=.22),
}


def _vintage(img, p, ctx):
    strength = u1(p["style_strength"])
    grade = VINTAGE_STYLES.get(p.get("style", "V1"), VINTAGE_STYLES["V1"])
    out = _apply_grade(img, grade, ctx, strength, ctx.seed)
    if p["brightness"]:
        out = ops.brightness(out, n1(p["brightness"]) * 0.7)
    if p["saturation"]:
        out = ops.saturation(out, n1(p["saturation"]))

    # Period lenses were soft at the edges and dark in the corners.
    blur_amt = u1(p["center_focus"])
    if blur_amt > 0.01:
        h, w = out.shape[:2]
        soft = ops.gaussian_blur(out, max(1.2, min(h, w) * 0.006 * blur_amt * 3))
        edge = gray3(ops.vignette_mask((h, w), 0.5, 0.5, 0.55))
        out = np.clip(out * (1 - edge * blur_amt) + soft * (edge * blur_amt), 0, 1)
    vig = u1(p["vignette_strength"])
    if vig > 0.01:
        h, w = out.shape[:2]
        out = np.clip(out * gray3(1.0 - ops.vignette_mask((h, w), size=0.6) * vig * 0.8), 0, 1)
    return out.astype(F32)


register(Tool(
    id="vintage", name=N_("Vintage"), group=N_("Looks"), kind="global",
    description=N_("Old-photo color styles."),
    params=(
        Param("brightness", N_("Brightness")),
        Param("saturation", N_("Saturation")),
        Param("style_strength", N_("Style Strength"), lo=0, hi=100, default=60),
        Param("center_focus", N_("Center Focus"), lo=0, hi=100, default=30),
        Param("vignette_strength", N_("Vignette Strength"), lo=0, hi=100, default=40),
        Param("style", N_("Style"), kind=CHOICE, default="V1",
              choices=tuple(VINTAGE_STYLES)),
    ),
    apply=_vintage))


# ========================================================================
# Grainy Film
# ========================================================================
FILM_STYLES = {
    "X1": dict(curve=[(0, .04), (.3, .30), (.7, .74), (1, .98)], split=((.48, .50, .54), (.54, .52, .46), .5), saturation=-.10, contrast=.20),
    "X2": dict(curve=[(0, .07), (.3, .28), (.7, .76), (1, .96)], split=((.46, .51, .56), (.56, .51, .44), .7), saturation=-.22, contrast=.26),
    "X3": dict(curve=[(0, .02), (.3, .33), (.7, .72), (1, 1.0)], split=((.52, .50, .48), (.50, .53, .52), .4), saturation=.05, contrast=.14),
    "L1": dict(curve=[(0, .10), (.3, .34), (.7, .72), (1, .94)], split=((.50, .52, .52), (.54, .53, .48), .6), saturation=-.30, contrast=.08),
    "L2": dict(curve=[(0, .12), (.3, .32), (.7, .70), (1, .92)], split=((.54, .50, .46), (.50, .52, .54), .8), saturation=-.40, contrast=.05),
    "L3": dict(curve=[(0, .08), (.3, .35), (.7, .74), (1, .95)], split=((.48, .52, .55), (.55, .52, .45), .6), saturation=-.18, contrast=.12),
    "N1": dict(curve=[(0, .00), (.3, .26), (.7, .78), (1, 1.0)], split=((.50, .50, .50), (.50, .50, .50), .0), saturation=-.05, contrast=.30),
    "N2": dict(curve=[(0, .03), (.3, .29), (.7, .75), (1, .99)], split=((.49, .50, .53), (.53, .51, .47), .45), saturation=-.14, contrast=.22),
}


def _grainy_film(img, p, ctx):
    strength = u1(p["style_strength"])
    grade = FILM_STYLES.get(p.get("style", "X1"), FILM_STYLES["X1"])
    out = _apply_grade(img, grade, ctx, strength, ctx.seed)
    g = u1(p["grain"])
    if g > 0.001:
        # Grain is a property of the film, not of the print size: it must
        # be generated at a constant size relative to the full image, so
        # the preview predicts the export.
        out = ops.grain(out, g * 1.35, size=max(1.0, 1.4 * ctx.scale + 0.2),
                        seed=ctx.seed)
    return out


register(Tool(
    id="grainy_film", name=N_("Grainy Film"), group=N_("Looks"), kind="global",
    description=N_("Film looks with grain."),
    params=(
        Param("grain", N_("Grain"), lo=0, hi=100, default=45),
        Param("style_strength", N_("Style Strength"), lo=0, hi=100, default=55),
        Param("style", N_("Style"), kind=CHOICE, default="X1",
              choices=tuple(FILM_STYLES)),
    ),
    apply=_grainy_film))


# ========================================================================
# Retrolux
# ========================================================================
RETRO_STYLES = {f"R{i}": dict(
    curve=[(0, .05 + .015 * (i % 5)), (.5, .50 + .02 * ((i % 3) - 1)),
           (1, .97 - .015 * (i % 4))],
    split=(((.40 + .03 * (i % 4)), .48, (.58 - .02 * (i % 3))),
           ((.58 - .02 * (i % 3)), .52, (.40 + .03 * (i % 5)))  , .8),
    saturation=-.15 - .04 * (i % 5), contrast=.10 + .03 * (i % 4),
    warmth=.20 - .06 * (i % 6)) for i in range(1, 14)}


def _retrolux(img, p, ctx):
    strength = u1(p["style_strength"])
    grade = RETRO_STYLES.get(p.get("style", "R1"), RETRO_STYLES["R1"])
    out = _apply_grade(img, grade, ctx, strength, ctx.seed)
    if p["brightness"]:
        out = ops.brightness(out, n1(p["brightness"]) * 0.7)
    if p["contrast"]:
        out = ops.contrast(out, n1(p["contrast"]))
    if p["saturation"]:
        out = ops.saturation(out, n1(p["saturation"]))
    sc = u1(p["scratches"])
    if sc > 0.01:
        out = ops.texture_overlay(out, "scratches", sc * 0.8, seed=ctx.seed)
    ll = u1(p["light_leaks"])
    if ll > 0.01:
        out = ops.texture_overlay(out, "leak", ll, seed=ctx.seed + 11)
    bl = u1(p["blur"])
    if bl > 0.01:
        h, w = out.shape[:2]
        soft = ops.gaussian_blur(out, max(1.0, min(h, w) * 0.005 * bl * 3))
        edge = gray3(ops.vignette_mask((h, w), 0.5, 0.5, 0.5))
        out = np.clip(out * (1 - edge * bl) + soft * (edge * bl), 0, 1)
    return out.astype(F32)


register(Tool(
    id="retrolux", name=N_("Retrolux"), group=N_("Looks"), kind="global",
    description=N_("Retro looks with scratches and light leaks."),
    params=(
        Param("brightness", N_("Brightness")),
        Param("contrast", N_("Contrast")),
        Param("saturation", N_("Saturation")),
        Param("style_strength", N_("Style Strength"), lo=0, hi=100, default=60),
        Param("scratches", N_("Scratches"), lo=0, hi=100, default=25),
        Param("light_leaks", N_("Light Leaks"), lo=0, hi=100, default=40),
        Param("blur", N_("Blur"), lo=0, hi=100, default=0),
        Param("style", N_("Style"), kind=CHOICE, default="R1",
              choices=tuple(RETRO_STYLES)),
    ),
    apply=_retrolux))


# ========================================================================
# Grunge
# ========================================================================
GRUNGE_STYLES = {f"G{i}": dict(
    curve=[(0, .02 + .03 * (i % 4)), (.35, .30 + .04 * (i % 3)),
           (.7, .72 - .03 * (i % 5)), (1, .95 - .02 * (i % 3))],
    split=(((.36 + .05 * (i % 5)), (.44 + .03 * (i % 3)), (.56 - .04 * (i % 4))),
           ((.62 - .04 * (i % 4)), (.52 + .02 * (i % 3)), (.36 + .05 * (i % 5))), 1.1),
    saturation=-.35 - .05 * (i % 4), contrast=.28 + .05 * (i % 5),
    warmth=.10 * ((i % 3) - 1)) for i in range(1, 13)}


def _grunge(img, p, ctx):
    strength = u1(p["style_strength"])
    grade = GRUNGE_STYLES.get(p.get("style", "G1"), GRUNGE_STYLES["G1"])
    out = _apply_grade(img, grade, ctx, strength, ctx.seed)
    tex = u1(p["texture_strength"])
    if tex > 0.01:
        out = ops.texture_overlay(out, "paper", tex, seed=ctx.seed + 5)
        out = ops.texture_overlay(out, "vignette_dirt", tex * 0.7, seed=ctx.seed + 9)
    if p["brightness"]:
        out = ops.brightness(out, n1(p["brightness"]) * 0.7)
    if p["contrast"]:
        out = ops.contrast(out, n1(p["contrast"]))
    if p["saturation"]:
        out = ops.saturation(out, n1(p["saturation"]))
    bl = u1(p["blur"])
    if bl > 0.01:
        h, w = out.shape[:2]
        out = ops.gaussian_blur(out, max(0.6, min(h, w) * 0.002 * bl * 3))
    return np.clip(out, 0, 1).astype(F32)


register(Tool(
    id="grunge", name=N_("Grunge"), group=N_("Looks"), kind="global",
    description=N_("Rough, textured looks."),
    params=(
        Param("style_strength", N_("Style Strength"), lo=0, hi=100, default=65),
        Param("texture_strength", N_("Texture Strength"), lo=0, hi=100, default=50),
        Param("brightness", N_("Brightness")),
        Param("contrast", N_("Contrast")),
        Param("saturation", N_("Saturation")),
        Param("blur", N_("Blur"), lo=0, hi=100, default=0),
        Param("style", N_("Style"), kind=CHOICE, default="G1",
              choices=tuple(GRUNGE_STYLES)),
    ),
    apply=_grunge))


# ========================================================================
# Black & White
# ========================================================================
BW_STYLES = {
    N_("Neutral"): dict(contrast=.00, curve=None),
    N_("Contrast"): dict(contrast=.35, curve=[(0, 0), (.25, .17), (.75, .83), (1, 1)]),
    N_("Bright"): dict(contrast=.10, curve=[(0, .05), (.5, .58), (1, 1)]),
    N_("Dark"): dict(contrast=.20, curve=[(0, 0), (.5, .41), (1, .95)]),
    N_("Film"): dict(contrast=.22, curve=[(0, .04), (.3, .28), (.7, .76), (1, .97)]),
    N_("Smooth"): dict(contrast=-.10, curve=[(0, .08), (.5, .52), (1, .93)]),
}


def _black_white(img, p, ctx):
    weights = BW_FILTERS.get(p.get("filter", "Neutral"), BW_FILTERS["Neutral"])
    mono = channel_mix_mono(img, weights)
    out = np.repeat(mono[..., None], 3, axis=2)
    style = BW_STYLES.get(p.get("style", "Neutral"), BW_STYLES["Neutral"])
    if style.get("curve"):
        out = ops.apply_lut(out, ops.spline_lut(style["curve"], ops.LUT_N))
    if style.get("contrast"):
        out = ops.contrast(out, style["contrast"])
    if p["brightness"]:
        out = ops.brightness(out, n1(p["brightness"]) * 0.8)
    if p["contrast"]:
        out = ops.contrast(out, n1(p["contrast"]))
    g = u1(p["grain"])
    if g > 0.001:
        out = ops.grain(out, g * 1.2, size=max(1.0, 1.3 * ctx.scale + 0.2),
                        seed=ctx.seed)
    return out


register(Tool(
    id="black_white", name=N_("Black & White"), group=N_("Looks"), kind="global",
    description=N_("Turn the photo black and white."),
    params=(
        Param("brightness", N_("Brightness")),
        Param("contrast", N_("Contrast")),
        Param("grain", N_("Grain"), lo=0, hi=100, default=0),
        Param("filter", N_("Color Filter"), kind=CHOICE, default=N_("Neutral"),
              choices=tuple(BW_FILTERS)),
        Param("style", N_("Style"), kind=CHOICE, default=N_("Neutral"),
              choices=tuple(BW_STYLES)),
    ),
    apply=_black_white))


# ========================================================================
# Noir
# ========================================================================
NOIR_STYLES = {
    f"{pre}{i}": dict(weights=BW_FILTERS[flt], contrast=c, wash=wash)
    for pre, flt, c, wash, i in (
        ("N", N_("Neutral"), .45, .00, 1), ("N", N_("Neutral"), .60, .08, 2),
        ("N", N_("Neutral"), .75, .00, 3), ("S", N_("Yellow"), .50, .12, 1),
        ("S", N_("Yellow"), .65, .20, 2), ("F", N_("Red"), .55, .05, 1),
        ("F", N_("Red"), .70, .15, 2), ("H", N_("Green"), .80, .00, 1),
        ("H", N_("Green"), .90, .10, 2), ("B", N_("Blue"), .55, .18, 1),
    )}


def _noir(img, p, ctx):
    """High-contrast monochrome with a 'wash' - a faded, hazy overlay."""
    style = NOIR_STYLES.get(p.get("style", "N1"), NOIR_STYLES["N1"])
    strength = u1(p["strength"])
    mono = channel_mix_mono(img, style["weights"])
    out = np.repeat(mono[..., None], 3, axis=2)
    out = ops.contrast(out, style["contrast"] * strength)

    wash = u1(p["wash"]) + style["wash"]
    if wash > 0.001:
        # lift the black point and desaturate the toe: the classic
        # silver-print fade, done as a curve rather than a flat add
        out = ops.apply_lut(out, ops.spline_lut(
            [(0, 0.02 + 0.22 * wash), (0.5, 0.5 + 0.03 * wash),
             (1, 1.0 - 0.05 * wash)], ops.LUT_N))
    if p["brightness"]:
        out = ops.brightness(out, n1(p["brightness"]) * 0.8)
    g = u1(p["grain"])
    if g > 0.001:
        out = ops.grain(out, g * 1.5, size=max(1.0, 1.5 * ctx.scale + 0.2),
                        seed=ctx.seed)
    return out


register(Tool(
    id="noir", name=N_("Noir"), group=N_("Looks"), kind="global",
    description=N_("A dark, classic black and white look."),
    params=(
        Param("brightness", N_("Brightness")),
        Param("wash", N_("Wash"), lo=0, hi=100, default=20),
        Param("grain", N_("Grain"), lo=0, hi=100, default=40),
        Param("strength", N_("Filter Strength"), lo=0, hi=100, default=70),
        Param("style", N_("Style"), kind=CHOICE, default="N1",
              choices=tuple(NOIR_STYLES)),
    ),
    apply=_noir))


# ========================================================================
# Lens Blur
# ========================================================================
def _lens_blur(img, p, ctx):
    """Depth-of-field simulation with a real bokeh kernel.

    A Gaussian blur does not look like a lens: out-of-focus highlights
    form discs, not smooth falloff.  Blurring in linear light with a
    disc kernel is what makes small bright points bloom into circles
    the way they do optically.
    """
    strength = u1(p["blur_strength"])
    if strength <= 0.001:
        return img
    h, w = img.shape[:2]
    radius = max(1.0, min(h, w) * 0.035 * strength)

    lin = ops.srgb_to_linear(img)
    if ops.HAVE_CV and radius >= 2:
        import cv2 as _cv
        k = int(radius) * 2 + 1
        k = min(k, (min(h, w) // 2) * 2 - 1)
        if k >= 3:
            kern = np.zeros((k, k), np.float32)
            _cv.circle(kern, (k // 2, k // 2), max(1, k // 2), 1.0, -1)
            kern /= float(kern.sum())
            blurred = _cv.filter2D(np.ascontiguousarray(lin), -1, kern,
                                   borderType=_cv.BORDER_REFLECT_101)
        else:
            blurred = ops.gaussian_blur(lin, radius)
    else:
        blurred = ops.gaussian_blur(lin, radius)
    blurred = ops.linear_to_srgb(np.clip(blurred, 0, 1))

    cx, cy = p.get("center", [0.5, 0.5])
    size = u1(p.get("size", 40)) or 0.4
    transition = max(0.05, u1(p["transition"]))
    aspect = 1.0 if p.get("shape", "Circle") == "Circle" else 2.1
    focus = ops.radial_mask((h, w), float(cx), float(cy), size,
                            feather=transition * 1.6, aspect=aspect)
    out = ops.composite(blurred, img, focus)

    vig = u1(p["vignette_strength"])
    if vig > 0.01:
        out = np.clip(out * gray3(1.0 - ops.vignette_mask((h, w), float(cx), float(cy), 0.7) * vig * 0.75), 0, 1)
    return out.astype(F32)


register(Tool(
    id="lens_blur", name=N_("Lens Blur"), group=N_("Effects"), kind="global",
    description=N_("Blur the background so your subject stands out."),
    params=(
        Param("blur_strength", N_("Blur Strength"), lo=0, hi=100, default=50),
        Param("transition", N_("Transition"), lo=0, hi=100, default=50),
        Param("vignette_strength", N_("Vignette Strength"), lo=0, hi=100, default=30),
        Param("size", N_("Size"), lo=5, hi=100, default=40),
        Param("center", N_("Center"), kind=POINT, default=[0.5, 0.5]),
        Param("shape", N_("Shape"), kind=CHOICE, default=N_("Circle"),
              choices=(N_("Circle"), N_("Planar"))),
    ),
    apply=_lens_blur))


# ========================================================================
# Geometry: Crop
# ========================================================================
CROP_ASPECTS = {
    N_("Free"): None, N_("Original"): 0.0, N_("Square"): 1.0, "3:2": 3 / 2, "2:3": 2 / 3,
    "4:3": 4 / 3, "3:4": 3 / 4, "5:4": 5 / 4, "4:5": 4 / 5, "7:5": 7 / 5,
    "5:7": 5 / 7, "16:9": 16 / 9, "9:16": 9 / 16, "DIN": 1.4142, N_("Golden"): 1.618,
}


def _crop(img, p, ctx):
    """Crop to a normalised rectangle.

    Stored in 0..1 image units rather than pixels so the same edit
    applies identically to the preview and to the full-resolution
    export, and survives the source being re-scanned.
    """
    h, w = img.shape[:2]
    rect = p.get("rect") or [0.0, 0.0, 1.0, 1.0]
    x, y, rw, rh = (float(v) for v in rect)
    x0 = int(round(np.clip(x, 0, 1) * w))
    y0 = int(round(np.clip(y, 0, 1) * h))
    x1 = int(round(np.clip(x + rw, 0, 1) * w))
    y1 = int(round(np.clip(y + rh, 0, 1) * h))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return img
    return np.ascontiguousarray(img[y0:y1, x0:x1])


register(Tool(
    id="crop", name=N_("Crop"), group=N_("Geometry"), kind="geometry", maskable=False,
    description=N_("Cut the photo to a new shape."),
    params=(
        Param("rect", N_("Rectangle"), kind=POINT, default=[0.0, 0.0, 1.0, 1.0]),
        Param("aspect", N_("Aspect"), kind=CHOICE, default=N_("Free"),
              choices=tuple(CROP_ASPECTS)),
    ),
    apply=_crop))


# ========================================================================
# Geometry: Rotate
# ========================================================================
def _rotate(img, p, ctx):
    out = img
    quarters = int(p.get("quarter_turns", 0)) % 4
    if quarters:
        out = np.ascontiguousarray(np.rot90(out, -quarters))
    if p.get("flip_h"):
        out = np.ascontiguousarray(out[:, ::-1])
    if p.get("flip_v"):
        out = np.ascontiguousarray(out[::-1, :])
    angle = float(p.get("straighten", 0.0))
    if abs(angle) > 1e-3:
        # Straightening rotates then crops back to the largest rectangle
        # that contains no empty corner, which is what people expect from
        # a horizon fix: the frame stays full, it just gets slightly
        # tighter.
        out = ops.rotate(out, angle, expand=False, edge="clamp")
        h, w = out.shape[:2]
        a = abs(np.deg2rad(angle))
        ca, sa = float(np.cos(a)), float(np.sin(a))
        denom = ca * ca - sa * sa
        if denom > 1e-6:
            iw = (w * ca - h * sa) / denom
            ih = (h * ca - w * sa) / denom
            iw, ih = min(iw, w), min(ih, h)
            if iw > 8 and ih > 8:
                x0 = int(round((w - iw) / 2))
                y0 = int(round((h - ih) / 2))
                out = np.ascontiguousarray(
                    out[y0:y0 + int(round(ih)), x0:x0 + int(round(iw))])
    return out


register(Tool(
    id="rotate", name=N_("Rotate"), group=N_("Geometry"), kind="geometry", maskable=False,
    description=N_("Turn, straighten or mirror the photo."),
    params=(
        Param("quarter_turns", N_("Rotate"), kind=CHOICE, default=0,
              choices=(0, 1, 2, 3)),
        Param("straighten", N_("Straighten"), lo=-45, hi=45, default=0, step=0.1),
        Param("flip_h", N_("Mirror"), kind=TOGGLE, default=False),
        Param("flip_v", N_("Flip"), kind=TOGGLE, default=False),
    ),
    apply=_rotate))


# ========================================================================
# Geometry: Perspective
# ========================================================================
def _fill_edges(img: np.ndarray, empty: np.ndarray, mode: str,
                draft: bool = False) -> np.ndarray:
    """Fill the regions a warp left empty.

    'Smart' inpaints from surrounding content, which is what makes a
    keystone correction usable without a second crop.  Inpainting is the
    single most expensive thing in the pipeline, so a draft render
    substitutes a mirrored, blurred edge - close enough to judge the
    framing while dragging.
    """
    if not empty.any():
        return img
    if mode == "White":
        return np.where(gray3(empty) > 0.5, F32(1.0), img).astype(F32)
    if mode == "Black":
        return np.where(gray3(empty) > 0.5, F32(0.0), img).astype(F32)
    if draft:
        soft = ops.gaussian_blur(img, max(3.0, min(img.shape[:2]) * 0.01))
        m = gray3(ops.gaussian_blur(empty, 4.0))
        return np.clip(img * (1 - m) + soft * m, 0, 1).astype(F32)
    # Smart: grow the mask slightly so the seam is not sampled from the
    # interpolated edge pixels, which would smear.
    m = empty
    if ops.HAVE_CV:
        import cv2 as _cv
        m = _cv.dilate((empty > 0.5).astype(np.uint8),
                       np.ones((5, 5), np.uint8), iterations=1).astype(F32)
    return ops.inpaint(img, m, radius=8)


def _perspective(img, p, ctx):
    """Keystone / tilt correction via a homography on the frame corners."""
    th, tv = n1(p["tilt_h"]), n1(p["tilt_v"])
    rot, scale = n1(p["rotate"]) * 0.12, n1(p["scale"])
    if not any((th, tv, rot, scale)):
        return img
    h, w = img.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)

    # Push the top/bottom (or left/right) edges in opposite directions:
    # that is what a tilt is, projectively.
    dx, dy = th * w * 0.22, tv * h * 0.22
    dst = np.array([
        [0 + max(dx, 0), 0 + max(dy, 0)],
        [w - max(dx, 0), 0 + max(-dy, 0) if dy < 0 else max(dy, 0)],
        [w - max(-dx, 0), h - max(dy, 0)],
        [0 + max(-dx, 0), h - max(dy, 0)],
    ], np.float32)
    if abs(th) > 1e-6:
        dst = np.array([[abs(dx) * (1 if th > 0 else 0), 0],
                        [w - abs(dx) * (1 if th > 0 else 0), 0],
                        [w - abs(dx) * (0 if th > 0 else 1), h],
                        [abs(dx) * (0 if th > 0 else 1), h]], np.float32)
    if abs(tv) > 1e-6:
        vy = abs(dy)
        dst[:, 1] = np.array([vy * (1 if tv > 0 else 0),
                              vy * (0 if tv > 0 else 1),
                              h - vy * (0 if tv > 0 else 1),
                              h - vy * (1 if tv > 0 else 0)], np.float32)

    cx, cy = w / 2.0, h / 2.0
    if abs(rot) > 1e-6:
        a = np.deg2rad(rot * 45.0)
        ca, sa = float(np.cos(a)), float(np.sin(a))
        d = dst - (cx, cy)
        dst = np.stack([d[:, 0] * ca - d[:, 1] * sa,
                        d[:, 0] * sa + d[:, 1] * ca], 1) + (cx, cy)
    if abs(scale) > 1e-6:
        k = 1.0 + scale * 0.45
        dst = (dst - (cx, cy)) * k + (cx, cy)

    if ops.HAVE_CV:
        import cv2 as _cv
        m = _cv.getPerspectiveTransform(dst.astype(np.float32),
                                        src.astype(np.float32))
    else:                                   # least-squares homography
        a_rows, b_rows = [], []
        for (sx, sy), (dx_, dy_) in zip(src, dst):
            a_rows += [[dx_, dy_, 1, 0, 0, 0, -sx * dx_, -sx * dy_],
                       [0, 0, 0, dx_, dy_, 1, -sy * dx_, -sy * dy_]]
            b_rows += [sx, sy]
        sol = np.linalg.lstsq(np.array(a_rows, np.float64),
                              np.array(b_rows, np.float64), rcond=None)[0]
        m = np.append(sol, 1.0).reshape(3, 3)

    out = ops.warp_perspective(img, m, (h, w), edge="fill", fill=0.0)
    # Track what fell outside the source by warping a solid coverage plane.
    cover = ops.warp_perspective(np.ones_like(img), m, (h, w),
                                 edge="fill", fill=0.0)
    empty = (ops.luma(cover) < 0.5).astype(F32)
    return _fill_edges(out, empty, p.get("fill", "Smart"), ctx.draft)


register(Tool(
    id="perspective", name=N_("Perspective"), group=N_("Geometry"), kind="geometry",
    maskable=False,
    description=N_("Straighten buildings and tilted lines."),
    params=(
        Param("tilt_h", N_("Tilt Horizontal")),
        Param("tilt_v", N_("Tilt Vertical")),
        Param("rotate", N_("Rotate")),
        Param("scale", N_("Scale")),
        Param("fill", N_("Fill"), kind=CHOICE, default=N_("Smart"),
              choices=(N_("Smart"), N_("White"), N_("Black"))),
    ),
    apply=_perspective))


# ========================================================================
# Geometry: Expand
# ========================================================================
def _expand(img, p, ctx):
    """Grow the canvas and fill the new area, for reframing."""
    h, w = img.shape[:2]
    amt = u1(p["amount"])
    if amt <= 0.001:
        return img
    sides = p.get("sides", "All")
    px_x = int(round(w * amt * 0.30))
    px_y = int(round(h * amt * 0.30))
    left = right = px_x
    top = bottom = px_y
    if sides == "Horizontal":
        top = bottom = 0
    elif sides == "Vertical":
        left = right = 0
    if not any((left, right, top, bottom)):
        return img

    oh, ow = h + top + bottom, w + left + right
    out = np.zeros((oh, ow, 3), F32)
    out[top:top + h, left:left + w] = img
    empty = np.ones((oh, ow), F32)
    empty[top:top + h, left:left + w] = 0.0
    mode = p.get("fill", "Smart")
    if mode == "Smart":
        # Seed the empty band by mirroring the edge before inpainting:
        # Telea has nothing to march from at the frame border otherwise.
        if ops.HAVE_CV:
            import cv2 as _cv
            out = _cv.copyMakeBorder(np.ascontiguousarray(img), top, bottom,
                                     left, right, _cv.BORDER_REFLECT_101)
        else:
            out = np.pad(img, ((top, bottom), (left, right), (0, 0)), "reflect")
        soft = ops.gaussian_blur(out, max(2.0, min(oh, ow) * 0.008))
        m = gray3(ops.gaussian_blur(empty, max(2.0, min(oh, ow) * 0.004)))
        out = np.clip(out * (1 - m) + soft * m, 0, 1).astype(F32)
    else:
        out = _fill_edges(out, empty, mode, ctx.draft)
    return out


register(Tool(
    id="expand", name=N_("Expand"), group=N_("Geometry"), kind="geometry", maskable=False,
    description=N_("Add space around the photo."),
    params=(
        Param("amount", N_("Amount"), lo=0, hi=100, default=25),
        Param("sides", N_("Sides"), kind=CHOICE, default=N_("All"),
              choices=(N_("All"), N_("Horizontal"), N_("Vertical"))),
        Param("fill", N_("Fill"), kind=CHOICE, default=N_("Smart"),
              choices=(N_("Smart"), N_("White"), N_("Black"))),
    ),
    apply=_expand))


# ========================================================================
# Local: stroke rasterisation shared by Brush and Healing
# ========================================================================
def rasterize_strokes(shape: tuple[int, int], strokes: list,
                      soften: float = 1.0) -> np.ndarray:
    """Turn recorded brush strokes into a 0..1 mask.

    Strokes are stored as normalised polylines with a normalised radius,
    so a mask painted on a 1200px preview reproduces exactly on a 6000px
    export instead of landing in the wrong place or the wrong size.
    """
    h, w = shape
    mask = np.zeros((h, w), F32)
    if not strokes:
        return mask
    diag = float(np.hypot(h, w))
    use_cv = ops.HAVE_CV
    if use_cv:
        import cv2 as _cv
    for stroke in strokes:
        pts = stroke.get("points") or []
        if not pts:
            continue
        r = max(1, int(round(float(stroke.get("radius", 0.04)) * diag * 0.5)))
        val = float(stroke.get("value", 1.0))
        layer = np.zeros((h, w), F32)
        xy = [(int(round(float(px) * (w - 1))), int(round(float(py) * (h - 1))))
              for px, py in pts]
        if use_cv:
            if len(xy) == 1:
                _cv.circle(layer, xy[0], r, 1.0, -1, lineType=_cv.LINE_AA)
            else:
                for a, b in zip(xy, xy[1:]):
                    _cv.line(layer, a, b, 1.0, r * 2, lineType=_cv.LINE_AA)
                for pt in xy:
                    _cv.circle(layer, pt, r, 1.0, -1, lineType=_cv.LINE_AA)
        else:
            ys, xs = np.ogrid[:h, :w]
            for (px, py) in xy:
                layer = np.maximum(
                    layer, ((xs - px) ** 2 + (ys - py) ** 2 <= r * r).astype(F32))
        if soften > 0:
            layer = ops.gaussian_blur(layer, max(1.0, r * 0.35 * soften))
        if stroke.get("erase"):
            mask = np.clip(mask - layer, 0, 1)
        else:
            mask = np.clip(mask + layer * val, 0, 1) if val >= 0 else \
                np.clip(mask + layer * val, -1, 1)
    return mask


# ========================================================================
# Local: Selective
# ========================================================================
def _selective(img, p, ctx):
    """Control points: a local edit bounded by colour similarity.

    Each point samples the colour under it and affects only nearby pixels
    that resemble it, so a point dropped on a sky lifts the sky and stops
    at the roofline without any mask being drawn.
    """
    points = p.get("points") or []
    if not points:
        return img
    out = img
    h, w = img.shape[:2]
    for pt in points:
        b = n1(pt.get("brightness", 0))
        c = n1(pt.get("contrast", 0))
        s = n1(pt.get("saturation", 0))
        st = n1(pt.get("structure", 0))
        if not any((b, c, s, st)):
            continue
        mask = ops.similarity_mask(
            out, float(pt.get("x", 0.5)), float(pt.get("y", 0.5)),
            max(0.02, float(pt.get("size", 0.25))),
            tolerance=max(0.05, float(pt.get("tolerance", 0.22))))
        edited = out
        if b:
            edited = ops.brightness(edited, b * 0.8)
        if c:
            edited = ops.contrast(edited, c)
        if s:
            edited = ops.saturation(edited, s)
        if st:
            edited = ops.structure(edited, st)
        out = ops.composite(out, edited, mask)
    return out


register(Tool(
    id="selective", name=N_("Selective"), group=N_("Local"), kind="local",
    maskable=False,
    description=N_("Change light or color in just one area."),
    params=(
        Param("points", N_("Points"), kind=POINT, default=[]),
    ),
    apply=_selective))


# ========================================================================
# Local: Brush
# ========================================================================
BRUSH_MODES = (N_("Dodge & Burn"), N_("Exposure"), N_("Temperature"), N_("Saturation"))


def _brush(img, p, ctx):
    """Painted local adjustments, one mask per brush mode."""
    strokes = p.get("strokes") or []
    if not strokes:
        return img
    out = img
    h, w = img.shape[:2]
    for mode in BRUSH_MODES:
        subset = [s for s in strokes if s.get("mode") == mode]
        if not subset:
            continue
        # Signed mask: strokes carry a value, so the same brush both
        # dodges and burns depending on which way the user set it.
        raw = rasterize_strokes((h, w), subset)
        pos, neg = np.clip(raw, 0, 1), np.clip(-raw, 0, 1)
        for mask, sign in ((pos, 1.0), (neg, -1.0)):
            if not mask.any():
                continue
            amt = sign * 0.55
            if mode == "Dodge & Burn":
                edited = ops.brightness(out, amt * 0.8)
                edited = ops.contrast(edited, amt * 0.2)
            elif mode == "Exposure":
                edited = ops.brightness(out, amt)
            elif mode == "Temperature":
                edited = ops.temperature_tint(out, amt * 0.9, 0.0)
            else:
                edited = ops.saturation(out, amt * 1.4)
            out = ops.composite(out, edited, mask)
    return out


register(Tool(
    id="brush", name=N_("Brush"), group=N_("Local"), kind="local", maskable=False,
    description=N_("Paint light, darkness or color onto parts of the photo."),
    params=(
        Param("strokes", N_("Strokes"), kind=STROKES, default=[]),
        Param("mode", N_("Brush"), kind=CHOICE, default=N_("Dodge & Burn"),
              choices=BRUSH_MODES),
        Param("size", N_("Size"), lo=1, hi=100, default=20),
        Param("value", N_("Amount"), lo=-100, hi=100, default=50),
    ),
    apply=_brush))


# ========================================================================
# Local: Healing
# ========================================================================
def _healing(img, p, ctx):
    strokes = p.get("strokes") or []
    if not strokes:
        return img
    h, w = img.shape[:2]
    mask = rasterize_strokes((h, w), strokes, soften=0.0)
    if not (mask > 0.5).any():
        return img
    radius = max(3, int(min(h, w) * 0.008))
    return ops.inpaint(img, mask, radius=radius)


register(Tool(
    id="healing", name=N_("Healing"), group=N_("Local"), kind="local", maskable=False,
    description=N_("Remove spots and small objects."),
    params=(
        Param("strokes", N_("Strokes"), kind=STROKES, default=[]),
        Param("size", N_("Size"), lo=1, hi=100, default=12),
    ),
    apply=_healing))


# ========================================================================
# Portrait
# ========================================================================
PORTRAIT_STYLES = {
    N_("Spotlight"):  dict(spotlight=45, smooth=35, eyes=25, warm=0.06),
    N_("Smooth"):     dict(spotlight=20, smooth=65, eyes=15, warm=0.04),
    N_("Bright"):     dict(spotlight=60, smooth=40, eyes=30, warm=0.10),
    N_("Fair"):       dict(spotlight=35, smooth=50, eyes=20, warm=-0.04),
    N_("Even"):       dict(spotlight=25, smooth=45, eyes=20, warm=0.00),
    N_("Warm"):       dict(spotlight=40, smooth=45, eyes=25, warm=0.16),
}


def _portrait(img, p, ctx):
    """Face-aware retouching: spotlight, skin smoothing and eye clarity.

    All three depend on knowing where the face is.  Without a detection
    the tool returns the image untouched rather than guessing at the
    centre of the frame, which would brighten the wrong thing.
    """
    from . import faces as facemod
    if not ctx.face_detection:
        return img
    detected = facemod.detect(img)
    if not detected:
        return img

    h, w = img.shape[:2]
    out = img
    style = PORTRAIT_STYLES.get(p.get("style", "Spotlight"),
                                PORTRAIT_STYLES["Spotlight"])

    spot = u1(p["spotlight"])
    if spot > 0.01:
        # brighten the face and let the surroundings fall away, the way a
        # softbox aimed at the subject does
        m = facemod.face_mask((h, w), detected, grow=2.1, feather=0.9)
        lit = ops.brightness(out, spot * 0.45)
        if style["warm"]:
            lit = ops.warmth(lit, style["warm"] * spot * 2.0)
        out = ops.composite(out, lit, m)
        out = np.clip(out * gray3(1.0 - (1.0 - m) * F32(spot * 0.30)), 0, 1)

    smooth = u1(p["skin_smoothing"])
    if smooth > 0.01:
        skin = facemod.skin_mask(out, detected)
        # Keep the detail layer and smooth only the base: this evens out
        # blotchy colour while leaving pores and hair, which is why it
        # does not look like a plastic filter.
        r = max(2, int(min(h, w) * 0.012))
        base = ops.guided_filter(out, out, r, 0.004 + 0.010 * smooth)
        detail = out - base
        softened = np.clip(base + detail * F32(1.0 - 0.75 * smooth), 0, 1)
        out = ops.composite(out, softened, skin * F32(min(1.0, smooth * 1.15)))

    eyes = u1(p["eye_clarity"])
    if eyes > 0.01:
        m = facemod.eye_mask((h, w), detected)
        sharp = ops.sharpen(out, eyes * 0.9, radius=max(0.7, 1.0 * ctx.scale + 0.3))
        sharp = ops.contrast(sharp, eyes * 0.35)
        sharp = ops.brightness(sharp, eyes * 0.10)
        out = ops.composite(out, sharp, m * F32(min(1.0, eyes)))
    return out


register(Tool(
    id="portrait", name=N_("Portrait"), group=N_("Portrait"), kind="global",
    maskable=False,
    description=N_("Brighten faces, smooth skin and sharpen eyes. Done on this computer."),
    params=(
        Param("spotlight", N_("Face Spotlight"), lo=0, hi=100, default=45),
        Param("skin_smoothing", N_("Skin Smoothing"), lo=0, hi=100, default=35),
        Param("eye_clarity", N_("Eye Clarity"), lo=0, hi=100, default=25),
        Param("style", N_("Style"), kind=CHOICE, default=N_("Spotlight"),
              choices=tuple(PORTRAIT_STYLES)),
    ),
    apply=_portrait))


# ========================================================================
# Head Pose
# ========================================================================
def _head_pose(img, p, ctx):
    """Subtle head reorientation by warping the face region.

    This is an honest approximation, not the 3D-model reconstruction the
    name suggests: five landmarks cannot recover a head's geometry, so
    what happens here is a smooth local warp that rotates and tilts the
    face area while leaving the background fixed.  Small corrections read
    convincingly; large ones will distort, so the ranges are deliberately
    tighter than a slider that promised true 3D would be.
    """
    from . import faces as facemod
    if not ctx.face_detection:
        return img
    pitch, yaw, roll = n1(p["pitch"]), n1(p["yaw"]), n1(p["roll"])
    smile, size = n1(p["smile"]), n1(p["pupil_size"])
    if not any((pitch, yaw, roll, smile, size)):
        return img
    detected = facemod.detect(img)
    if not detected:
        return img

    h, w = img.shape[:2]
    out = img
    for f in detected:
        cx, cy = f.center
        # region of influence, in pixels
        rx, ry = f.w * w * 1.5, f.h * h * 1.6
        px, py = cx * w, cy * h
        x0, x1 = int(max(0, px - rx)), int(min(w, px + rx))
        y0, y1 = int(max(0, py - ry)), int(min(h, py + ry))
        if x1 - x0 < 16 or y1 - y0 < 16:
            continue
        patch = out[y0:y1, x0:x1]
        ph, pw = patch.shape[:2]

        yy, xx = np.meshgrid(np.arange(ph, dtype=F32),
                            np.arange(pw, dtype=F32), indexing="ij")
        # normalised radial falloff, so the warp dies out before the patch
        # border and never produces a visible seam
        nx = (xx - pw / 2) / (pw / 2)
        ny = (yy - ph / 2) / (ph / 2)
        fall = np.clip(1.0 - (nx * nx + ny * ny), 0, 1) ** 1.5

        sx, sy = xx.copy(), yy.copy()
        if yaw:
            # horizontal shear that grows toward the chin: a turn, roughly
            sx = sx - nx * fall * F32(yaw * pw * 0.10) * (1.0 + ny * 0.35)
        if pitch:
            sy = sy - ny * fall * F32(pitch * ph * 0.10)
            sx = sx - nx * np.abs(ny) * fall * F32(pitch * pw * 0.04)
        if roll:
            a = np.deg2rad(roll * 9.0) * fall
            ca, sa = np.cos(a), np.sin(a)
            rxp = (xx - pw / 2) * ca - (yy - ph / 2) * sa + pw / 2
            ryp = (xx - pw / 2) * sa + (yy - ph / 2) * ca + ph / 2
            sx, sy = sx + (rxp - xx), sy + (ryp - yy)
        if smile:
            # lift the mouth corners only
            mcx = (f.mouth_left[0] + f.mouth_right[0]) / 2 * w - x0
            mcy = (f.mouth_left[1] + f.mouth_right[1]) / 2 * h - y0
            d = np.clip(1.0 - np.sqrt(((xx - mcx) / (f.w * w * 0.8)) ** 2
                                     + ((yy - mcy) / (f.h * h * 0.35)) ** 2), 0, 1)
            sy = sy + d * F32(smile * f.h * h * 0.055) * np.sign(xx - mcx) ** 2
            sy = sy + d * F32(smile * f.h * h * 0.05)

        warped = ops.sample_bilinear(patch, sx, sy, edge="clamp")
        out = out.copy()
        out[y0:y1, x0:x1] = warped

        if size:
            m = facemod.eye_mask((h, w), [f])
            scaled = ops.contrast(out, size * 0.3)
            scaled = ops.brightness(scaled, size * 0.12)
            out = ops.composite(out, scaled, m)
    return np.clip(out, 0, 1).astype(F32)


register(Tool(
    id="head_pose", name=N_("Head Pose"), group=N_("Portrait"), kind="global",
    maskable=False,
    description=N_("Gently turn a face or add a smile. Keep it subtle."),
    params=(
        Param("pitch", N_("Up / Down"), lo=-100, hi=100, default=0),
        Param("yaw", N_("Left / Right"), lo=-100, hi=100, default=0),
        Param("roll", N_("Tilt"), lo=-100, hi=100, default=0),
        Param("smile", N_("Smile"), lo=0, hi=100, default=0),
        Param("pupil_size", N_("Eye Emphasis"), lo=0, hi=100, default=0),
    ),
    apply=_head_pose))


# ========================================================================
# Double Exposure
# ========================================================================
DOUBLE_MODES = ("normal", "darken", "lighten", "overlay", "screen",
                "multiply", "softlight", "hardlight", "difference",
                "exclusion", "add", "subtract", "colordodge", "colorburn")


def _double_exposure(img, p, ctx):
    """Blend a second image over this one."""
    path = p.get("image")
    if not path:
        return img
    from .. import imageio as iio
    h, w = img.shape[:2]
    try:
        top = iio.load_rgb(path, max_side=max(h, w))
    except Exception:
        return img

    # Cover the frame preserving the overlay's aspect, then centre-crop:
    # stretching someone's second exposure to fit is never what they want.
    th, tw = top.shape[:2]
    s = max(w / tw, h / th)
    top = ops.resize(top, max(1, int(round(tw * s))), max(1, int(round(th * s))))
    th, tw = top.shape[:2]
    oy, ox = (th - h) // 2, (tw - w) // 2
    top = top[oy:oy + h, ox:ox + w]
    if top.shape[:2] != (h, w):
        top = ops.resize(top, w, h)

    if p.get("flip"):
        top = top[:, ::-1]
    if p.get("mono"):
        top = np.repeat(ops.luma(top)[..., None], 3, axis=2)
    return ops.blend(img, top, p.get("mode", "screen"), u1(p["opacity"]))


register(Tool(
    id="double_exposure", name=N_("Double Exposure"), group=N_("Effects"),
    kind="global", maskable=True,
    description=N_("Blend another photo into this one."),
    params=(
        Param("image", N_("Image"), kind=IMAGE, default=None),
        Param("mode", N_("Blend Mode"), kind=CHOICE, default="screen",
              choices=DOUBLE_MODES),
        Param("opacity", N_("Opacity"), lo=0, hi=100, default=50),
        Param("mono", N_("Desaturate"), kind=TOGGLE, default=False),
        Param("flip", N_("Mirror"), kind=TOGGLE, default=False),
    ),
    apply=_double_exposure))


# ========================================================================
# Text
# ========================================================================
TEXT_STYLES = {
    N_("Plain"): dict(weight="regular", bg=None, border=0, spacing=0.0),
    N_("Bold"): dict(weight="bold", bg=None, border=0, spacing=0.0),
    N_("Banner"): dict(weight="bold", bg=(0, 0, 0), border=0, spacing=0.06),
    N_("Outline"): dict(weight="bold", bg=None, border=3, spacing=0.0),
    N_("Caption"): dict(weight="regular", bg=(1, 1, 1), border=0, spacing=0.04),
    N_("Spaced"): dict(weight="regular", bg=None, border=0, spacing=0.22),
}


def _find_font(bold: bool = False) -> str | None:
    """Locate a usable TrueType face, bundled first then system."""
    import os
    from pathlib import Path as _P
    names = (["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial_Bold.ttf"]
             if bold else
             ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"])
    dirs = [_P(__file__).resolve().parent.parent.parent / "data" / "fonts"]
    if os.environ.get("APPDIR"):
        dirs.append(_P(os.environ["APPDIR"]) / "usr/share/piklin/fonts")
    dirs += [_P("/usr/share/piklin/fonts"),
             _P("/usr/share/fonts/truetype/dejavu"),
             _P("/usr/share/fonts/truetype/liberation"),
             _P("/usr/share/fonts/TTF"), _P("/usr/share/fonts")]
    for d in dirs:
        for n in names:
            c = d / n
            if c.is_file():
                return str(c)
    for d in dirs:
        if d.is_dir():
            for c in sorted(d.rglob("*.ttf"))[:1]:
                return str(c)
    return None


def _text(img, p, ctx):
    """Draw a text overlay, positioned and sized in normalised units."""
    content = (p.get("text") or "").strip()
    if not content:
        return img
    from PIL import Image as _Img, ImageDraw, ImageFont
    from .. import imageio as iio

    h, w = img.shape[:2]
    style = TEXT_STYLES.get(p.get("style", "Plain"), TEXT_STYLES["Plain"])
    # Size is a fraction of the frame height, so text occupies the same
    # proportion of the preview and the export.
    px = max(8, int(round(u1(p["size"]) * h * 0.32)))
    path = _find_font(style["weight"] == "bold")
    try:
        font = ImageFont.truetype(path, px) if path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    pil = iio.to_pil(img).convert("RGBA")
    layer = _Img.new("RGBA", pil.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    col = p.get("color") or [1.0, 1.0, 1.0]
    alpha = int(round(u1(p["opacity"]) * 255))
    rgba = tuple(int(round(np.clip(c, 0, 1) * 255)) for c in col[:3]) + (alpha,)

    lines = content.split("\n")
    if style["spacing"]:
        lines = [(" " * max(1, int(style["spacing"] * 8))).join(ln) for ln in lines]
    ascent = px * 1.25
    total_h = ascent * len(lines)
    cx, cy = p.get("position") or [0.5, 0.5]
    ox, oy = float(cx) * w, float(cy) * h - total_h / 2

    for i, line in enumerate(lines):
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = px * 0.6 * len(line)
        x = ox - tw / 2
        y = oy + i * ascent
        if style["bg"] is not None:
            pad = px * 0.22
            bg = tuple(int(round(c * 255)) for c in style["bg"]) + (
                int(alpha * 0.8),)
            draw.rectangle([x - pad, y - pad * 0.4, x + tw + pad,
                            y + ascent * 0.92], fill=bg)
        if style["border"]:
            b = max(1, int(style["border"] * px / 48))
            for dx in range(-b, b + 1):
                for dy in range(-b, b + 1):
                    if dx or dy:
                        draw.text((x + dx, y + dy), line, font=font,
                                  fill=(0, 0, 0, alpha))
        draw.text((x, y), line, font=font, fill=rgba)

    if p.get("rotation"):
        layer = layer.rotate(float(p["rotation"]), resample=_Img.BICUBIC,
                             center=(ox, oy + total_h / 2))
    merged = _Img.alpha_composite(pil, layer).convert("RGB")
    return (np.asarray(merged, dtype=F32) / 255.0)


register(Tool(
    id="text", name=N_("Text"), group=N_("Overlay"), kind="overlay", maskable=False,
    description=N_("Add text to the photo."),
    params=(
        Param("text", N_("Text"), kind=TEXT, default=""),
        Param("size", N_("Size"), lo=1, hi=100, default=22),
        Param("opacity", N_("Opacity"), lo=0, hi=100, default=100),
        Param("rotation", N_("Rotation"), lo=-180, hi=180, default=0),
        Param("color", N_("Color"), kind=COLOR, default=[1.0, 1.0, 1.0]),
        Param("position", N_("Position"), kind=POINT, default=[0.5, 0.5]),
        Param("style", N_("Style"), kind=CHOICE, default=N_("Plain"),
              choices=tuple(TEXT_STYLES)),
    ),
    apply=_text))


# ========================================================================
# Frames
# ========================================================================
FRAME_STYLES = {
    N_("None"): None,
    N_("White"): dict(color=(1.0, 1.0, 1.0), inner=0.0, rough=0.0),
    N_("Black"): dict(color=(0.0, 0.0, 0.0), inner=0.0, rough=0.0),
    N_("Cream"): dict(color=(0.96, 0.94, 0.88), inner=0.0, rough=0.0),
    N_("Charcoal"): dict(color=(0.12, 0.12, 0.13), inner=0.0, rough=0.0),
    N_("Museum"): dict(color=(1.0, 1.0, 1.0), inner=0.12, rough=0.0),
    N_("Polaroid"): dict(color=(0.98, 0.97, 0.93), inner=0.0, rough=0.0,
                     bottom_heavy=True),
    N_("Film Edge"): dict(color=(0.07, 0.07, 0.08), inner=0.0, rough=0.45),
    N_("Torn"): dict(color=(0.99, 0.98, 0.95), inner=0.0, rough=0.9),
}


def _frames(img, p, ctx):
    """Draw a border inside the frame, keeping the pixel dimensions."""
    style = FRAME_STYLES.get(p.get("style", "None"))
    if not style:
        return img
    width = u1(p["width"])
    if width <= 0.005:
        return img
    h, w = img.shape[:2]
    bw = max(1, int(round(min(h, w) * width * 0.14)))
    bottom = int(bw * 3.2) if style.get("bottom_heavy") else bw

    # The photo is scaled down to make room, so the output keeps the same
    # dimensions - a frame that grew the canvas would change the crop.
    inner_w = max(8, w - bw * 2)
    inner_h = max(8, h - bw - bottom)
    scaled = ops.resize(img, inner_w, inner_h)

    out = np.empty((h, w, 3), F32)
    out[:] = np.asarray(style["color"], F32)
    out[bw:bw + inner_h, bw:bw + inner_w] = scaled

    if style.get("inner"):
        # a thin keyline just inside the mount, as a gallery mat has
        k = max(1, int(bw * style["inner"]))
        y0, x0 = bw - k, bw - k
        y1, x1 = bw + inner_h + k, bw + inner_w + k
        line = np.asarray((0.55, 0.55, 0.55), F32)
        out[max(0, y0):bw, max(0, x0):min(w, x1)] = line
        out[bw + inner_h:min(h, y1), max(0, x0):min(w, x1)] = line
        out[max(0, y0):min(h, y1), max(0, x0):bw] = line
        out[max(0, y0):min(h, y1), bw + inner_w:min(w, x1)] = line

    rough = style.get("rough", 0.0)
    if rough > 0.01:
        # Perturb the inner edge so it reads as a torn or sprocketed
        # border instead of a perfect rectangle.
        rng = np.random.default_rng(ctx.seed + 31)
        jitter = int(max(1, bw * rough * 0.5))
        for y in range(bw, bw + inner_h):
            dl = int(rng.integers(0, jitter + 1))
            dr = int(rng.integers(0, jitter + 1))
            if dl:
                out[y, bw:bw + dl] = np.asarray(style["color"], F32)
            if dr:
                out[y, bw + inner_w - dr:bw + inner_w] = np.asarray(style["color"], F32)
    return out


register(Tool(
    id="frames", name=N_("Frames"), group=N_("Overlay"), kind="overlay", maskable=False,
    description=N_("Add a border."),
    params=(
        Param("width", N_("Width"), lo=0, hi=100, default=35),
        Param("style", N_("Style"), kind=CHOICE, default=N_("White"),
              choices=tuple(FRAME_STYLES)),
    ),
    apply=_frames))


def groups() -> dict[str, list[Tool]]:
    """Tools bucketed by group, in registration order, for the UI."""
    out: dict[str, list[Tool]] = {}
    for t in REGISTRY.values():
        out.setdefault(t.group, []).append(t)
    return out
