"""Perceptual difference metrics.

The compressor needs to answer one question: *can a person see the
difference between this and the original?*  A quality number like "JPEG
85" cannot answer it, because the same setting is invisible on a portrait
and obvious on a gradient sky.  Measuring the decoded result instead lets
the encoder search for the smallest file that still passes.

Two metrics are used together, because each misses what the other
catches:

``ssim`` tracks local structure - the smearing and blocking that shows up
across a whole region.  It is the standard measure and it correlates well
with what people notice at a glance.

``worst_block`` is the largest *local* error anywhere in the frame.  A
mean score stays high while one small area falls apart, which is exactly
where JPEG artefacts live: a single blocky patch of sky in an otherwise
clean photo. Capping the worst region is what stops that.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2 as _cv
    HAVE_CV = True
except Exception:                                    # pragma: no cover
    _cv = None
    HAVE_CV = False

F32 = np.float32


def _gauss(img: np.ndarray, sigma: float = 1.5) -> np.ndarray:
    if HAVE_CV:
        return _cv.GaussianBlur(img, (0, 0), sigma,
                                borderType=_cv.BORDER_REFLECT_101)
    # separable box approximation
    r = max(1, int(round(sigma * 2)))
    k = np.ones(2 * r + 1, F32) / (2 * r + 1)
    out = np.apply_along_axis(lambda m: np.convolve(m, k, "same"), 0, img)
    return np.apply_along_axis(lambda m: np.convolve(m, k, "same"), 1, out)


def _luma(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img.astype(F32)
    return (img @ np.array([0.2126, 0.7152, 0.0722], F32)).astype(F32)


def ssim_map(a: np.ndarray, b: np.ndarray, sigma: float = 1.5) -> np.ndarray:
    """Per-pixel SSIM between two 0..1 images, on luminance."""
    x, y = _luma(a), _luma(b)
    c1, c2 = F32(0.01 ** 2), F32(0.03 ** 2)
    mu_x, mu_y = _gauss(x, sigma), _gauss(y, sigma)
    xx, yy = _gauss(x * x, sigma), _gauss(y * y, sigma)
    xy = _gauss(x * y, sigma)
    var_x = np.maximum(xx - mu_x * mu_x, 0)
    var_y = np.maximum(yy - mu_y * mu_y, 0)
    cov = xy - mu_x * mu_y
    num = (2 * mu_x * mu_y + c1) * (2 * cov + c2)
    den = (mu_x ** 2 + mu_y ** 2 + c1) * (var_x + var_y + c2)
    return (num / np.maximum(den, 1e-12)).astype(F32)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(ssim_map(a, b)))


def worst_block(a: np.ndarray, b: np.ndarray, block: int = 16) -> float:
    """Lowest SSIM of any block-sized region.

    Catches the one ruined patch that a frame-wide mean would average
    away.
    """
    m = ssim_map(a, b)
    h, w = m.shape
    bh, bw = max(1, h // block), max(1, w // block)
    # trim to a whole number of blocks, then reduce each one
    m = m[:bh * block, :bw * block]
    if m.size == 0:
        return 1.0
    return float(m.reshape(bh, block, bw, block).mean(axis=(1, 3)).min())


def chroma_error(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute colour error, to catch chroma subsampling damage.

    Luma-only SSIM is blind to 4:2:0 subsampling wrecking a saturated
    red edge, which is one of the few artefacts people reliably spot.
    """
    if a.ndim < 3 or b.ndim < 3:
        return 0.0
    la, lb = _luma(a)[..., None], _luma(b)[..., None]
    return float(np.abs((a - la) - (b - lb)).mean())


def compare(original: np.ndarray, candidate: np.ndarray) -> dict:
    """Full comparison used to accept or reject an encoded candidate."""
    if original.shape != candidate.shape:
        from .engine import ops
        candidate = ops.resize(candidate, original.shape[1], original.shape[0])
    return {
        "ssim": ssim(original, candidate),
        "worst_block": worst_block(original, candidate),
        "chroma_error": chroma_error(original, candidate),
        "psnr": psnr(original, candidate),
    }


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a - b) ** 2))
    if mse <= 1e-12:
        return 99.0
    return float(10.0 * np.log10(1.0 / mse))
