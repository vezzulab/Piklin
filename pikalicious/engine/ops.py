"""Image-processing primitives.

Everything operates on float32 ``(h, w, 3)`` arrays in 0..1 sRGB unless a
function says otherwise.  float32 is not a detail: float64 doubles the
memory traffic and roughly halves throughput, and the editor's whole
interactivity budget is memory bandwidth.

Two workhorses carry most of the tools:

``box_blur`` runs in O(1) per pixel via summed-area tables, so blur cost
does not grow with radius.  Three box passes approximate a Gaussian
closely enough for photographic work (the error is below quantisation).

``guided_filter`` is the edge-aware smoother.  Structure, HDR, shadow and
highlight recovery, skin smoothing and tonal contrast are all the same
idea - separate an image into a locally-smooth base and a detail
residual, then treat them differently - and a guided filter does that
without the halos an unsharp mask produces.
"""
from __future__ import annotations

import numpy as np

F32 = np.float32
EPS = F32(1e-6)


# -- conversions ---------------------------------------------------------
# Pointwise tone functions are evaluated once into a lookup table and
# then gathered, rather than evaluated per pixel.  A single np.power over
# a 3 MP image costs about as much as a whole LUT gather, so folding a
# chain of transfer-function + curve + transfer-function into one table
# turns three passes into one.
LUT_N = 16384          # 64 KiB: fits L2, keeps transfer error under 0.25/255
_LUT_X = np.linspace(0.0, 1.0, LUT_N, dtype=F32)


def apply_lut(img: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Map an image through a LUT sampled over 0..1.

    With 1024 entries the sampling error is under half a step of 16-bit
    output, so nearest-entry lookup is used: interpolating would double
    the cost to fix an error that cannot be seen.
    """
    n = len(lut)
    idx = np.clip(img * F32(n - 1), 0, n - 1).astype(np.int32)
    return np.take(lut.astype(F32), idx, mode="clip")


def _srgb_to_linear_f(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.04045, x / 12.92,
                    np.power((x + 0.055) / 1.055, 2.4)).astype(F32)


def _linear_to_srgb_f(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92,
                    1.055 * np.power(np.maximum(x, 0.0), 1.0 / 2.4) - 0.055
                    ).astype(F32)


_LUT_TO_LINEAR = _srgb_to_linear_f(_LUT_X)
_LUT_TO_SRGB = _linear_to_srgb_f(_LUT_X)


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    if x.size < 4096:                      # exact for small arrays / scalars
        return _srgb_to_linear_f(x)
    return apply_lut(x, _LUT_TO_LINEAR)


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    if x.size < 4096:
        return _linear_to_srgb_f(x)
    return apply_lut(x, _LUT_TO_SRGB)


def pointwise(fn, cache_key=None, _cache={}) -> np.ndarray:
    """Build (and memoise) a LUT from a scalar-array function on 0..1."""
    if cache_key is not None:
        hit = _cache.get(cache_key)
        if hit is not None:
            return hit
    lut = np.clip(fn(_LUT_X), 0.0, 1.0).astype(F32)
    if cache_key is not None:
        if len(_cache) > 256:
            _cache.clear()
        _cache[cache_key] = lut
    return lut


# Rec.709 luma weights, matching how the eye weights the channels.
LUMA_W = np.array([0.2126, 0.7152, 0.0722], dtype=F32)


def luma(img: np.ndarray) -> np.ndarray:
    """Perceptual luminance, shape ``(h, w)``."""
    return (img @ LUMA_W).astype(F32)


def gray3(x: np.ndarray) -> np.ndarray:
    """Broadcast an ``(h, w)`` mask to ``(h, w, 1)`` for channel maths."""
    return x[..., None] if x.ndim == 2 else x


# -- backend -------------------------------------------------------------
# OpenCV's separable filters, resampler and warper are an order of
# magnitude faster than the equivalent numpy, and are internally
# threaded.  They are treated as an accelerator, not a requirement: the
# pure-numpy paths below produce the same results, just slower, so the
# app still runs where cv2 is unavailable.
try:
    import cv2 as _cv
    _cv.setUseOptimized(True)
    HAVE_CV = True
except Exception:                                    # pragma: no cover
    _cv = None
    HAVE_CV = False


def _np_box_axis(a: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """Box filter along one axis via a summed-area table (numpy fallback).

    Uses a shrinking window at the borders rather than padding, which
    avoids allocating two extra full-size arrays per pass.
    """
    n = a.shape[axis]
    if radius < 1 or n < 2:
        return a
    cs = np.cumsum(a, axis=axis, dtype=F32)
    idx = np.arange(n)
    hi = np.clip(idx + radius, 0, n - 1)
    lo = idx - radius - 1
    lo_c = np.clip(lo, 0, n - 1)
    take = lambda arr, i: np.take(arr, i, axis=axis)
    total = take(cs, hi) - np.where(
        (lo >= 0).reshape([-1 if d == axis else 1 for d in range(a.ndim)]),
        take(cs, lo_c), F32(0.0))
    count = (hi - np.maximum(lo, -1)).astype(F32)
    return (total / count.reshape(
        [-1 if d == axis else 1 for d in range(a.ndim)])).astype(F32)


def box_blur(img: np.ndarray, radius: int) -> np.ndarray:
    if radius < 1:
        return img
    if HAVE_CV:
        k = int(radius) * 2 + 1
        h, w = img.shape[:2]
        k = min(k, max(1, (min(h, w) // 2) * 2 - 1))
        if k < 3:
            return img
        return _cv.blur(np.ascontiguousarray(img), (k, k),
                        borderType=_cv.BORDER_REFLECT_101)
    return _np_box_axis(_np_box_axis(img, radius, 0), radius, 1)


def gaussian_blur(img: np.ndarray, sigma: float, passes: int = 3) -> np.ndarray:
    """Gaussian blur, by whichever route is cheaper at this sigma.

    A true Gaussian wins for small sigma; past about sigma 6 three box
    passes are both faster and visually identical, and their cost stops
    growing with radius.
    """
    if sigma <= 0.3:
        return img
    img = np.ascontiguousarray(img)
    if HAVE_CV and sigma <= 6.0:
        return _cv.GaussianBlur(img, (0, 0), float(sigma),
                                borderType=_cv.BORDER_REFLECT_101)
    radius = max(1, int(round(sigma * np.sqrt(3.0 * 2.0 * np.pi) / 4.0)))
    out = img
    for _ in range(passes):
        out = box_blur(out, radius)
    return out


def _downscale(a: np.ndarray, factor: int) -> np.ndarray:
    h, w = a.shape[:2]
    nh, nw = max(1, h // factor), max(1, w // factor)
    if HAVE_CV:
        return _cv.resize(np.ascontiguousarray(a), (nw, nh),
                          interpolation=_cv.INTER_AREA)
    ys = (np.arange(nh) * h // nh).astype(np.int32)
    xs = (np.arange(nw) * w // nw).astype(np.int32)
    return a[ys][:, xs]


def _upscale_to(a: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    h, w = shape
    if HAVE_CV:
        return _cv.resize(np.ascontiguousarray(a), (w, h),
                          interpolation=_cv.INTER_LINEAR)
    ys = np.clip((np.arange(h) * a.shape[0] // h), 0, a.shape[0] - 1)
    xs = np.clip((np.arange(w) * a.shape[1] // w), 0, a.shape[1] - 1)
    return a[ys][:, xs]


def guided_filter(guide: np.ndarray, src: np.ndarray, radius: int,
                  eps: float, fast: int = 4) -> np.ndarray:
    """Edge-preserving smoothing of ``src`` under the edges of ``guide``.

    He, Sun & Tang's guided filter.  ``eps`` sets what counts as an edge:
    local variance below it is smoothed as texture, above it is kept.

    ``fast`` is the Fast Guided Filter subsampling factor - the linear
    coefficients are solved on a 1/N-scale image and upsampled before
    being applied at full resolution.  Since those coefficients are
    smooth by construction this is visually indistinguishable from the
    exact filter while costing ~16x less at the default factor.
    """
    if radius < 1:
        return src
    g_full = guide if guide.ndim == 2 else luma(guide)
    h, w = g_full.shape[:2]
    single = src.ndim == 2

    factor = max(1, min(int(fast), radius, min(h, w) // 16 or 1))
    if factor > 1:
        g = _downscale(g_full, factor)
        s = _downscale(src, factor)
        r = max(1, radius // factor)
    else:
        g, s, r = g_full, src, radius

    mean_g = box_blur(g, r)
    mean_s = box_blur(s, r)
    var_g = box_blur(g * g, r) - mean_g * mean_g

    g_e = gray3(mean_g) if not single else mean_g
    gg = gray3(g) if not single else g
    cov_gs = box_blur(gg * s, r) - g_e * mean_s

    denom = (gray3(var_g) if not single else var_g) + F32(eps)
    a = cov_gs / denom
    b = mean_s - a * g_e

    mean_a = box_blur(a, r)
    mean_b = box_blur(b, r)
    if factor > 1:
        mean_a = _upscale_to(mean_a, (h, w))
        mean_b = _upscale_to(mean_b, (h, w))

    gf = gray3(g_full) if not single else g_full
    return (mean_a * gf + mean_b).astype(F32)


def detail_split(img: np.ndarray, radius: int, eps: float
                 ) -> tuple[np.ndarray, np.ndarray]:
    """Split into an edge-aware base layer and its detail residual."""
    base = guided_filter(img, img, radius, eps)
    return base, (img - base).astype(F32)


# -- tone ----------------------------------------------------------------
def spline_lut(points: list[tuple[float, float]], size: int = 256) -> np.ndarray:
    """Monotone cubic (Fritsch-Carlson) curve through control points.

    Monotone matters: a plain cubic spline overshoots between widely
    spaced points and puts dark rings around highlights.  This one cannot.
    """
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) < 2:
        return np.linspace(0.0, 1.0, size, dtype=F32)
    xs = np.array([p[0] for p in pts], F32)
    ys = np.array([p[1] for p in pts], F32)

    h = np.diff(xs)
    delta = np.diff(ys) / np.maximum(h, EPS)
    m = np.zeros_like(xs)
    m[1:-1] = (delta[:-1] + delta[1:]) / 2.0
    m[0], m[-1] = delta[0], delta[-1]
    for i in range(len(delta)):
        if delta[i] == 0:
            m[i] = m[i + 1] = 0.0
        else:
            a, b = m[i] / delta[i], m[i + 1] / delta[i]
            s = a * a + b * b
            if s > 9.0:
                t = 3.0 / np.sqrt(s)
                m[i], m[i + 1] = t * a * delta[i], t * b * delta[i]

    gx = np.linspace(0.0, 1.0, size, dtype=F32)
    idx = np.clip(np.searchsorted(xs, gx) - 1, 0, len(xs) - 2)
    hh = h[idx]
    t = (gx - xs[idx]) / np.maximum(hh, EPS)
    t2, t3 = t * t, t * t * t
    out = ((2 * t3 - 3 * t2 + 1) * ys[idx]
           + (t3 - 2 * t2 + t) * hh * m[idx]
           + (-2 * t3 + 3 * t2) * ys[idx + 1]
           + (t3 - t2) * hh * m[idx + 1])
    return np.clip(out, 0.0, 1.0).astype(F32)


def brightness(img: np.ndarray, amount: float) -> np.ndarray:
    """Exposure-like lift that rolls off instead of clipping.

    Positive amounts compress toward white along a filmic shoulder, so
    pushing brightness never flat-clips a highlight the way a plain
    multiply does.
    """
    if abs(amount) < 1e-4:
        return img
    a = float(amount)

    def f(x):
        if a > 0:
            k = 1.0 + a * 3.0
            return x * k / (1.0 + (k - 1.0) * x)
        return x * (1.0 + a)

    return apply_lut(img, pointwise(f, ("bri", round(a, 4))))


def contrast(img: np.ndarray, amount: float, pivot: float = 0.5) -> np.ndarray:
    """S-curve contrast around a pivot, computed in linear light.

    The whole chain (to linear, power, back to sRGB) collapses into one
    table, so this costs a single gather rather than three transcendental
    passes over the image.
    """
    if abs(amount) < 1e-4:
        return img
    a, pv = float(amount), float(pivot)

    def f(x):
        lin = _srgb_to_linear_f(x)
        p = float(_srgb_to_linear_f(np.array([pv], F32))[0])
        k = (1.0 + a * 1.6) if a > 0 else (1.0 / (1.0 - a * 0.8))
        return _linear_to_srgb_f(
            np.clip(np.power(np.maximum(lin / p, 1e-6), k) * p, 0.0, 1.0))

    return apply_lut(img, pointwise(f, ("con", round(a, 4), round(pv, 4))))


def saturation(img: np.ndarray, amount: float) -> np.ndarray:
    if abs(amount) < 1e-4:
        return img
    l = gray3(luma(img))
    return np.clip(l + (img - l) * F32(1.0 + amount), 0.0, 1.0).astype(F32)


def chroma(img: np.ndarray) -> np.ndarray:
    """Saturation proxy: max channel minus min channel, shape ``(h, w, 1)``.

    Computed from channel views rather than ``np.max(..., axis=2)``.  A
    reduction along the contiguous axis is roughly four times slower on a
    (h, w, 3) array because it strides through memory three-wide and
    builds a full temporary, and this runs on every vibrance call.
    """
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    return (mx - mn)[..., None]


def vibrance(img: np.ndarray, amount: float) -> np.ndarray:
    """Saturation weighted toward already-dull colours.

    Protects skin tones and stops already-saturated reds from flattening
    into a flat patch, which is what plain saturation does first.
    """
    if abs(amount) < 1e-4:
        return img
    l = gray3(luma(img))
    weight = np.clip(1.0 - chroma(img) * F32(1.6), 0.0, 1.0)
    weight *= weight
    return np.clip(l + (img - l) * (1.0 + F32(amount) * weight),
                   0.0, 1.0).astype(F32)


def shadows_highlights(img: np.ndarray, shadows: float = 0.0,
                       highlights: float = 0.0, radius_frac: float = 0.045
                       ) -> np.ndarray:
    """Recover shadow and highlight detail using a local tone mask.

    The lift is driven by a blurred, edge-aware luminance so that a dark
    subject brightens as a whole rather than raising noise pixel by pixel.
    """
    if abs(shadows) < 1e-4 and abs(highlights) < 1e-4:
        return img
    h, w = img.shape[:2]
    radius = max(2, int(min(h, w) * radius_frac))
    l = luma(img)
    base = guided_filter(l, l, radius, 0.01)

    out = img
    if abs(shadows) > 1e-4:
        # mask peaks in the darks and falls to zero by midtone
        mask = np.clip(1.0 - base * 2.0, 0.0, 1.0) ** 1.5
        amt = F32(shadows)
        gain = 1.0 + amt * 2.2 * mask
        out = out * gray3(gain)
        if amt > 0:                       # keep blacks from going milky
            out = np.clip(out, 0.0, 1.0)
    if abs(highlights) > 1e-4:
        mask = np.clip((base - 0.5) * 2.0, 0.0, 1.0) ** 1.5
        amt = F32(highlights)
        # pull toward the local base rather than scaling, which preserves
        # colour in a blown sky instead of shifting it cyan
        out = out - gray3(mask * amt * 0.85) * np.clip(out - 0.15, 0.0, 1.0)
    return np.clip(out, 0.0, 1.0).astype(F32)


def ambiance(img: np.ndarray, amount: float) -> np.ndarray:
    """Snapseed's signature control: local contrast plus selective colour.

    Positive values open up the midtones and warm the fill; negative
    values flatten local contrast and mute colour.
    """
    if abs(amount) < 1e-4:
        return img
    a = F32(amount)
    h, w = img.shape[:2]
    radius = max(3, int(min(h, w) * 0.08))
    l = luma(img)
    base = guided_filter(l, l, radius, 0.015)
    detail = l - base

    # lift the local base toward mid-grey, keeping detail intact
    lifted = base + (F32(0.5) - base) * a * F32(0.45)
    new_l = np.clip(lifted + detail * (1.0 + a * 0.35), 0.0, 1.0)
    scale = gray3((new_l + EPS) / (l + EPS))
    out = np.clip(img * scale, 0.0, 1.0)
    return vibrance(out, float(a) * 0.5)


def warmth(img: np.ndarray, amount: float) -> np.ndarray:
    """Warm/cool shift along the blue-yellow axis, luminance preserved."""
    if abs(amount) < 1e-4:
        return img
    a = F32(amount)
    before = luma(img)
    out = img.copy()
    out[..., 0] = np.clip(out[..., 0] * (1.0 + a * 0.28), 0.0, 1.0)
    out[..., 2] = np.clip(out[..., 2] * (1.0 - a * 0.26), 0.0, 1.0)
    after = luma(out)
    return np.clip(out * gray3((before + EPS) / (after + EPS)), 0.0, 1.0)


def temperature_tint(img: np.ndarray, temp: float, tint: float) -> np.ndarray:
    """White balance in linear light, the only place it is physical.

    ``temp`` runs blue (-1) to amber (+1); ``tint`` green (-1) to magenta
    (+1).  Doing this on gamma-encoded values is the classic cause of
    muddy, hue-shifted skies.
    """
    if abs(temp) < 1e-4 and abs(tint) < 1e-4:
        return img
    lin = srgb_to_linear(img)
    t, g = F32(temp), F32(tint)
    gain = np.array([1.0 + t * 0.45 + g * 0.06,
                     1.0 - g * 0.30,
                     1.0 - t * 0.45 + g * 0.06], dtype=F32)
    gain /= float(np.dot(gain, LUMA_W))     # keep overall exposure fixed
    return linear_to_srgb(np.clip(lin * gain, 0.0, 1.0))


# -- detail --------------------------------------------------------------
def structure(img: np.ndarray, amount: float) -> np.ndarray:
    """Multi-scale local contrast ("clarity"), halo-free.

    Three octaves of edge-aware detail are boosted with decreasing
    weight, which is why it reads as texture on rock and fabric rather
    than as an outline around every edge.
    """
    if abs(amount) < 1e-4:
        return img
    a = F32(amount)
    h, w = img.shape[:2]
    base_r = max(2, int(min(h, w) * 0.012))
    l = luma(img)
    acc = l.copy()
    weights = (1.0, 0.6, 0.32)
    residual = l
    for i, wgt in enumerate(weights):
        r = base_r * (2 ** i)
        if r >= min(h, w) // 2:
            break
        sm = guided_filter(residual, residual, int(r), 0.006 + 0.004 * i)
        acc = acc + (residual - sm) * (a * F32(wgt))
        residual = sm
    # Protect the extremes: pushing structure into blacks makes noise and
    # into highlights makes grey rims.
    guard = np.clip(1.0 - np.abs(l - 0.5) * 1.6, 0.15, 1.0)
    new_l = np.clip(l + (acc - l) * guard, 0.0, 1.0)
    return np.clip(img * gray3((new_l + EPS) / (l + EPS)), 0.0, 1.0)


def sharpen(img: np.ndarray, amount: float, radius: float = 1.1,
            threshold: float = 0.012) -> np.ndarray:
    """Unsharp mask with a noise threshold, applied to luminance only."""
    if amount <= 1e-4:
        return img
    l = luma(img)
    blurred = gaussian_blur(l, radius)
    diff = l - blurred
    # below the threshold is sensor noise, not detail
    diff = np.sign(diff) * np.maximum(np.abs(diff) - F32(threshold), 0.0)
    new_l = np.clip(l + diff * F32(amount * 2.2), 0.0, 1.0)
    return np.clip(img * gray3((new_l + EPS) / (l + EPS)), 0.0, 1.0)


def denoise(img: np.ndarray, amount: float) -> np.ndarray:
    """Edge-aware luminance and chroma noise reduction."""
    if amount <= 1e-4:
        return img
    a = float(np.clip(amount, 0.0, 1.0))
    h, w = img.shape[:2]
    r = max(1, int(min(h, w) * 0.002 * (1 + a)))
    smooth = guided_filter(img, img, r, 0.0004 + 0.004 * a)
    return (img * (1 - a) + smooth * a).astype(F32)


# -- blending ------------------------------------------------------------
def blend(base: np.ndarray, top: np.ndarray, mode: str = "normal",
          opacity: float = 1.0) -> np.ndarray:
    b, t = base, top
    if mode == "normal":
        out = t
    elif mode == "multiply":
        out = b * t
    elif mode == "screen":
        out = 1.0 - (1.0 - b) * (1.0 - t)
    elif mode == "overlay":
        out = np.where(b <= 0.5, 2 * b * t, 1.0 - 2 * (1 - b) * (1 - t))
    elif mode == "softlight":
        # W3C soft-light: gentler than overlay, no hard break at 0.5
        d = np.where(b <= 0.25, ((16 * b - 12) * b + 4) * b, np.sqrt(np.maximum(b, 0)))
        out = np.where(t <= 0.5, b - (1 - 2 * t) * b * (1 - b),
                       b + (2 * t - 1) * (d - b))
    elif mode == "hardlight":
        out = np.where(t <= 0.5, 2 * b * t, 1.0 - 2 * (1 - b) * (1 - t))
    elif mode == "darken":
        out = np.minimum(b, t)
    elif mode == "lighten":
        out = np.maximum(b, t)
    elif mode == "difference":
        out = np.abs(b - t)
    elif mode == "exclusion":
        out = b + t - 2 * b * t
    elif mode == "add":
        out = b + t
    elif mode == "subtract":
        out = b - t
    elif mode == "colordodge":
        out = np.where(t >= 1.0, 1.0, np.minimum(1.0, b / np.maximum(1 - t, EPS)))
    elif mode == "colorburn":
        out = np.where(t <= 0.0, 0.0, 1.0 - np.minimum(1.0, (1 - b) / np.maximum(t, EPS)))
    else:
        out = t
    out = np.clip(out, 0.0, 1.0).astype(F32)
    if opacity >= 0.999:
        return out
    return (b * F32(1.0 - opacity) + out * F32(opacity)).astype(F32)


def composite(base: np.ndarray, edited: np.ndarray,
              mask: np.ndarray | None) -> np.ndarray:
    """Blend an edited version back over the base through a 0..1 mask."""
    if mask is None:
        return edited
    m = gray3(np.clip(mask, 0.0, 1.0).astype(F32))
    return (base * (1.0 - m) + edited * m).astype(F32)


# -- masks ---------------------------------------------------------------
def radial_mask(shape: tuple[int, int], cx: float, cy: float,
                radius: float, feather: float = 0.5,
                aspect: float = 1.0, invert: bool = False) -> np.ndarray:
    """Elliptical mask.  Centre and radius are in 0..1 image units."""
    h, w = shape
    ys = (np.arange(h, dtype=F32) / max(h - 1, 1) - F32(cy))
    xs = (np.arange(w, dtype=F32) / max(w - 1, 1) - F32(cx))
    # scale x by the frame's aspect so a "circle" is round on screen
    xs = xs * F32(w / max(h, 1)) / F32(max(aspect, 0.05))
    d = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2) / F32(max(radius, 1e-3))
    f = F32(max(feather, 1e-3))
    m = np.clip((1.0 - d) / f + 0.5, 0.0, 1.0)
    m = m * m * (3.0 - 2.0 * m)             # smoothstep
    return (1.0 - m if invert else m).astype(F32)


def linear_mask(shape: tuple[int, int], cx: float, cy: float,
                angle_deg: float, width: float = 0.35,
                invert: bool = False) -> np.ndarray:
    """Graduated mask, for skies and foregrounds."""
    h, w = shape
    a = np.deg2rad(F32(angle_deg))
    ys = (np.arange(h, dtype=F32) / max(h - 1, 1) - F32(cy))[:, None]
    xs = (np.arange(w, dtype=F32) / max(w - 1, 1) - F32(cx))[None, :]
    d = xs * np.cos(a) + ys * np.sin(a)
    m = np.clip(d / F32(max(width, 1e-3)) + 0.5, 0.0, 1.0)
    m = m * m * (3.0 - 2.0 * m)
    return (1.0 - m if invert else m).astype(F32)


def similarity_mask(img: np.ndarray, cx: float, cy: float, radius: float,
                    tolerance: float = 0.22) -> np.ndarray:
    """The mask behind Selective: spatial falloff gated by colour match.

    A control point placed on a sky affects the sky and stops at the
    roofline, because pixels unlike the sampled colour are excluded even
    when they are inside the radius.
    """
    h, w = img.shape[:2]
    spatial = radial_mask((h, w), cx, cy, radius, feather=0.85)
    px = int(np.clip(cx, 0, 1) * (w - 1))
    py = int(np.clip(cy, 0, 1) * (h - 1))
    # sample a small patch, not one pixel, so noise does not pick the seed
    y0, y1 = max(0, py - 2), min(h, py + 3)
    x0, x1 = max(0, px - 2), min(w, px + 3)
    seed = img[y0:y1, x0:x1].reshape(-1, 3).mean(axis=0).astype(F32)

    l = luma(img)
    seed_l = float(np.dot(seed, LUMA_W))
    # weight luminance difference above chroma: it separates subjects better
    d_l = np.abs(l - seed_l)
    d_c = np.sqrt(np.sum((img - seed) ** 2, axis=2)) * F32(0.6)
    dist = d_l * F32(1.4) + d_c
    tol = F32(max(tolerance, 1e-3))
    colour = np.clip(1.0 - dist / tol, 0.0, 1.0)
    colour = colour * colour * (3.0 - 2.0 * colour)
    m = spatial * colour
    # Feather across edges so the boundary follows real detail.  The
    # guided filter can overshoot slightly at strong edges, and a mask
    # outside 0..1 would brighten beyond the edit when composited.
    m = guided_filter(img, m, max(2, int(min(h, w) * 0.01)), 0.002)
    return np.clip(m, 0.0, 1.0).astype(F32)


def vignette_mask(shape: tuple[int, int], cx: float = 0.5, cy: float = 0.5,
                  size: float = 0.62) -> np.ndarray:
    """0 at the centre, rising to 1 in the corners."""
    return 1.0 - radial_mask(shape, cx, cy, size, feather=1.1)


# -- geometry ------------------------------------------------------------
def sample_bilinear(img: np.ndarray, xs: np.ndarray, ys: np.ndarray,
                    fill: float = 0.0, edge: str = "fill") -> np.ndarray:
    """Bilinear resample at arbitrary float coordinates."""
    if HAVE_CV:
        border = _cv.BORDER_REPLICATE if edge == "clamp" else _cv.BORDER_CONSTANT
        return _cv.remap(np.ascontiguousarray(img),
                         xs.astype(F32), ys.astype(F32),
                         _cv.INTER_LINEAR, borderMode=border,
                         borderValue=(fill, fill, fill))
    h, w = img.shape[:2]
    if edge == "clamp":
        xs, ys = np.clip(xs, 0, w - 1), np.clip(ys, 0, h - 1)
        valid = None
    else:
        valid = (xs >= -0.5) & (xs <= w - 0.5) & (ys >= -0.5) & (ys <= h - 0.5)
        xs, ys = np.clip(xs, 0, w - 1), np.clip(ys, 0, h - 1)
    x0 = np.floor(xs).astype(np.int32)
    y0 = np.floor(ys).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (xs - x0).astype(F32)[..., None]
    fy = (ys - y0).astype(F32)[..., None]
    out = (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x1] * fx * (1 - fy)
           + img[y1, x0] * (1 - fx) * fy + img[y1, x1] * fx * fy)
    if valid is not None:
        out = np.where(valid[..., None], out, F32(fill))
    return out.astype(F32)


def warp_perspective(img: np.ndarray, matrix: np.ndarray,
                     out_shape: tuple[int, int] | None = None,
                     edge: str = "clamp", fill: float = 0.0) -> np.ndarray:
    """Apply a 3x3 homography mapping output coords -> input coords."""
    h, w = img.shape[:2]
    oh, ow = out_shape or (h, w)
    m = np.asarray(matrix, dtype=np.float64)
    if HAVE_CV:
        border = _cv.BORDER_REPLICATE if edge == "clamp" else _cv.BORDER_CONSTANT
        return _cv.warpPerspective(
            np.ascontiguousarray(img), m, (ow, oh),
            flags=_cv.INTER_LINEAR | _cv.WARP_INVERSE_MAP,
            borderMode=border, borderValue=(fill, fill, fill))
    yy, xx = np.meshgrid(np.arange(oh, dtype=F32),
                         np.arange(ow, dtype=F32), indexing="ij")
    denom = m[2, 0] * xx + m[2, 1] * yy + m[2, 2]
    denom = np.where(np.abs(denom) < 1e-9, 1e-9, denom)
    sx = (m[0, 0] * xx + m[0, 1] * yy + m[0, 2]) / denom
    sy = (m[1, 0] * xx + m[1, 1] * yy + m[1, 2]) / denom
    return sample_bilinear(img, sx, sy, fill=fill, edge=edge)


def rotate(img: np.ndarray, degrees: float, expand: bool = False,
           edge: str = "clamp", fill: float = 0.0) -> np.ndarray:
    """Rotate about the centre, optionally growing the canvas to fit."""
    if abs(degrees) < 1e-4 and not expand:
        return img
    h, w = img.shape[:2]
    a = np.deg2rad(degrees)
    ca, sa = float(np.cos(a)), float(np.sin(a))
    if expand:
        oh = max(1, int(round(abs(h * ca) + abs(w * sa))))
        ow = max(1, int(round(abs(w * ca) + abs(h * sa))))
    else:
        oh, ow = h, w
    m = np.array([
        [ca,  sa, (w - 1) / 2 - ca * (ow - 1) / 2 - sa * (oh - 1) / 2],
        [-sa, ca, (h - 1) / 2 + sa * (ow - 1) / 2 - ca * (oh - 1) / 2],
        [0.0, 0.0, 1.0]], dtype=np.float64)
    return warp_perspective(img, m, (oh, ow), edge=edge, fill=fill)


def resize(img: np.ndarray, width: int, height: int) -> np.ndarray:
    """High-quality resample; area-averaged down, Lanczos up."""
    h, w = img.shape[:2]
    width, height = max(1, int(width)), max(1, int(height))
    if (w, h) == (width, height):
        return img
    if HAVE_CV:
        interp = _cv.INTER_AREA if (width < w or height < h) else _cv.INTER_LANCZOS4
        return _cv.resize(np.ascontiguousarray(img), (width, height),
                          interpolation=interp)
    from PIL import Image as _Image
    pil = _Image.fromarray((np.clip(img, 0, 1) * 255.0 + 0.5).astype(np.uint8), "RGB")
    flt = _Image.BOX if width < w // 2 else _Image.LANCZOS
    return (np.asarray(pil.resize((width, height), flt), dtype=F32) / 255.0)


def fit_within(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    if max(h, w) <= max_side:
        return img
    s = max_side / float(max(h, w))
    return resize(img, int(round(w * s)), int(round(h * s)))


def inpaint(img: np.ndarray, mask: np.ndarray, radius: int = 6) -> np.ndarray:
    """Fill the masked region from surrounding content (the Healing tool).

    Telea's fast marching method when cv2 is present; otherwise a coarse
    blur-fill, which is visibly worse but keeps the tool functional.
    """
    m = (np.clip(mask, 0, 1) > 0.5).astype(np.uint8)
    if not m.any():
        return img
    if HAVE_CV:
        u8 = (np.clip(img, 0, 1) * 255.0 + 0.5).astype(np.uint8)
        # cv2.inpaint wants BGR; channel order is symmetric for the result
        out = _cv.inpaint(u8, m, float(radius), _cv.INPAINT_TELEA)
        return (out.astype(F32) / 255.0)
    filled = img.copy()
    soft = gray3(gaussian_blur(m.astype(F32), 3.0))
    blurred = gaussian_blur(img, max(4.0, radius * 1.5))
    return np.clip(filled * (1 - soft) + blurred * soft, 0, 1).astype(F32)


# -- texture -------------------------------------------------------------
def grain(img: np.ndarray, amount: float, size: float = 1.0,
          seed: int = 7) -> np.ndarray:
    """Film grain that lives in the midtones, as real grain does.

    Clean highlights and dense shadows carry little grain on film; making
    it uniform is the usual tell of a fake grain filter.
    """
    if amount <= 1e-4:
        return img
    h, w = img.shape[:2]
    rng = np.random.default_rng(seed)
    if size > 1.05:
        # generate small and scale up, which makes coarser clumps
        sh, sw = max(1, int(h / size)), max(1, int(w / size))
        n = rng.standard_normal((sh, sw), dtype=F32)
        n = resize(np.repeat(n[..., None], 3, axis=2) * 0.5 + 0.5, w, h)
        n = (n[..., 0] - 0.5) * 2.0
    else:
        n = rng.standard_normal((h, w), dtype=F32)
    l = luma(img)
    weight = (1.0 - np.abs(l - 0.45) * 1.7).clip(0.12, 1.0)
    g = gray3(n * weight * F32(amount * 0.16))
    return np.clip(img + g, 0.0, 1.0).astype(F32)


def texture_overlay(img: np.ndarray, kind: str, strength: float,
                    seed: int = 3) -> np.ndarray:
    """Procedural scratches / light leaks / paper, for the retro tools.

    Generated rather than shipped as assets so the package stays small
    and the texture matches the image's own resolution.
    """
    if strength <= 1e-4:
        return img
    h, w = img.shape[:2]
    rng = np.random.default_rng(seed)

    if kind == "scratches":
        tex = np.zeros((h, w), F32)
        for _ in range(rng.integers(6, 18)):
            x = rng.uniform(0, w)
            slant = rng.uniform(-0.06, 0.06)
            width_px = max(1, int(rng.uniform(1, 2.5)))
            ys = np.arange(h)
            xs = np.clip((x + ys * slant).astype(np.int32), 0, w - 1)
            val = rng.uniform(0.25, 0.8)
            for dx in range(width_px):
                tex[ys, np.clip(xs + dx, 0, w - 1)] = val
        tex = gaussian_blur(tex, 0.6)
        return blend(img, np.clip(img + gray3(tex) * F32(strength), 0, 1),
                     "normal", 1.0)

    if kind == "leak":
        # a warm gradient from one corner, like light past a film door
        corner = rng.integers(0, 4)
        cx, cy = (0.0, 0.0) if corner == 0 else (1.0, 0.0) if corner == 1 \
            else (0.0, 1.0) if corner == 2 else (1.0, 1.0)
        m = radial_mask((h, w), cx, cy, rng.uniform(0.5, 0.95), feather=1.2)
        tint = np.array([1.0, rng.uniform(0.45, 0.75), rng.uniform(0.15, 0.4)], F32)
        leak = np.clip(img + gray3(m) * tint * F32(strength * 0.55), 0, 1)
        return blend(img, leak, "screen", min(1.0, strength))

    if kind == "paper":
        n = rng.standard_normal((max(1, h // 3), max(1, w // 3)), dtype=F32)
        n = resize(np.repeat(n[..., None], 3, 2) * 0.35 + 0.5, w, h)
        return blend(img, np.clip(n, 0, 1), "overlay", strength * 0.5)

    if kind == "vignette_dirt":
        n = rng.standard_normal((max(1, h // 8), max(1, w // 8)), dtype=F32)
        n = resize(np.repeat(n[..., None], 3, 2) * 0.4 + 0.5, w, h)
        m = gray3(vignette_mask((h, w), size=0.5))
        return np.clip(img - n * m * F32(strength * 0.35), 0, 1).astype(F32)

    return img


def histogram(img: np.ndarray, bins: int = 128) -> dict[str, np.ndarray]:
    """Per-channel and luma histograms for the editor's readout."""
    out = {}
    for i, name in enumerate(("r", "g", "b")):
        out[name], _ = np.histogram(img[..., i], bins=bins, range=(0.0, 1.0))
    out["l"], _ = np.histogram(luma(img), bins=bins, range=(0.0, 1.0))
    return {k: v.astype(np.float32) for k, v in out.items()}
