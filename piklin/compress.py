"""Compression that targets a perceptual result, not a quality number.

The user picks an intent - keep the original bytes, stay visually
lossless, save space, or squeeze hard - and the encoder finds the
smallest file that still satisfies it, by encoding candidates, decoding
them back and measuring.

Why a ladder scan rather than a binary search
---------------------------------------------
Perceptual score is *not* monotonic in JPEG quality.  Measured on a real
photograph with a large flat dark area, quality 70 scored distinctly
worse than both 75 and 65, because that quantisation table happens to
shift the whole flat region by a constant DC offset - visible banding
that PSNR barely registers and SSIM correctly punishes.  A binary search
assumes monotonicity and would happily settle on such a setting, or
reject a whole range because of one bad rung.  Encoding a fixed ladder
and keeping the smallest candidate that passes every gate costs a handful
of encodes and cannot be fooled this way.

Nothing here overwrites an original unless the user explicitly asks for
storage optimisation, and that path keeps a recoverable copy first.
"""
from __future__ import annotations

import io
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import imageio as iio
from . import quality as qual
from .engine import ops
from .i18n import _, N_


@dataclass(frozen=True)
class Profile:
    """One compression intent."""
    id: str
    name: str
    summary: str
    # Gates a candidate must satisfy.  None disables that gate.
    min_ssim: float | None = None
    min_worst_block: float | None = None
    max_chroma_error: float | None = None
    # Ladder of encoder qualities to try, best first.
    ladder: tuple[int, ...] = (95, 92, 88, 85, 80, 75, 70, 65, 60)
    # Optional ceiling on the long edge, for the aggressive profiles.
    max_side: int | None = None
    # Never re-encode: copy the original bytes.
    passthrough: bool = False
    lossless: bool = False


PROFILES: dict[str, Profile] = {
    "original": Profile(
        id="original", name=N_("Original"),
        summary=N_("Keeps your files exactly as they are."),
        passthrough=True),
    "lossless": Profile(
        id="lossless", name=N_("Exact Picture, Smaller File"),
        summary=N_("Exactly the same picture in a smaller file. Saves the most on "
                  "screenshots."),
        lossless=True),
    # Thresholds below are calibrated against real photographs, not
    # guessed.  A busy frame (confetti, foliage, fine texture) cannot
    # reach a high SSIM at *any* JPEG quality - measured, one such image
    # peaks at 0.982 even at quality 96 - so a gate set at 0.995 is
    # unreachable and every photo falls back to the top rung, which
    # defeats the point of measuring at all.  These pass at the quality
    # where the difference is genuinely invisible and reject the rungs
    # below it.
    "visually_lossless": Profile(
        id="visually_lossless", name=N_("Looks the Same"),
        summary=N_("Much smaller files that look the same to the eye. Usually about "
                  "half the size."),
        min_ssim=0.985, min_worst_block=0.955, max_chroma_error=0.0022,
        ladder=(96, 95, 94, 93, 92, 90, 88)),
    "balanced": Profile(
        id="balanced", name=N_("Balanced"),
        summary=N_("A little softer if you look very closely, and clearly smaller. Good "
                  "for big libraries."),
        min_ssim=0.975, min_worst_block=0.900, max_chroma_error=0.0032,
        ladder=(92, 90, 88, 85, 82, 78, 75)),
    "space_saver": Profile(
        id="space_saver", name=N_("Space Saver"),
        summary=N_("Still looks good, at about a quarter of the original size."),
        min_ssim=0.960, min_worst_block=0.800, max_chroma_error=0.0048,
        ladder=(82, 76, 70, 64, 58, 52)),
    "maximum": Profile(
        id="maximum", name=N_("Smallest Files"),
        summary=N_("As small as possible. Photos are made smaller too, so it's good for "
                  "keeping but not for printing."),
        min_ssim=0.930, min_worst_block=0.700, max_chroma_error=0.0075,
        ladder=(70, 62, 55, 48, 42, 36), max_side=2560),
}

DEFAULT_PROFILE = "visually_lossless"

# Formats we can write, in preference order for photographs.
FORMATS = ("keep", "jpeg", "webp", "avif", "png")


def format_available(fmt: str) -> bool:
    if fmt in ("keep", "jpeg", "png"):
        return True
    if fmt == "webp":
        return True                              # Pillow ships WebP
    if fmt == "avif":
        try:
            Image.init()
            return "AVIF" in Image.SAVE or "AVIF" in Image.MIME
        except Exception:
            return False
    return False


@dataclass
class Result:
    """What happened to one file."""
    ok: bool
    source: Path
    output: Path | None = None
    original_bytes: int = 0
    output_bytes: int = 0
    profile: str = ""
    format: str = ""
    quality: int | None = None
    metrics: dict = field(default_factory=dict)
    note: str = ""

    @property
    def saved_bytes(self) -> int:
        return max(0, self.original_bytes - self.output_bytes)

    @property
    def ratio(self) -> float:
        if not self.original_bytes:
            return 1.0
        return self.output_bytes / self.original_bytes

    @property
    def percent_saved(self) -> float:
        return (1.0 - self.ratio) * 100.0


def _exif_bytes(path: Path) -> bytes | None:
    """Pull the EXIF block out of the source so it can be carried over.

    Losing capture date, camera and orientation during compression would
    quietly damage the library - the catalog is rebuilt from these.
    """
    try:
        with Image.open(path) as im:
            data = im.info.get("exif")
            if data:
                return data
            ex = im.getexif()
            return ex.tobytes() if ex else None
    except Exception:
        return None


def _exif_without_gps(data: bytes | None) -> bytes | None:
    """The same EXIF block minus its GPS directory: capture date, camera
    and orientation stay, where the photo was taken does not."""
    if not data:
        return data
    try:
        ex = Image.Exif()
        ex.load(data[6:] if data[:6] == b"Exif\x00\x00" else data)
        if 0x8825 in ex:
            del ex[0x8825]
        return ex.tobytes()
    except Exception:
        return None


def export_target(dest: Path | str, src: Path | str, index: int, *,
                  naming: str = "filename", subfolder: str = "none",
                  title: str | None = None, taken_at: float | None = None,
                  suffix: str | None = None) -> Path:
    """Where one exported photo goes, following the export options.

    ``naming``: "filename" keeps the original name, "title" uses the photo's
    title (falling back to the file name), "sequential" numbers the photos.
    ``subfolder``: "none", or "day" for a YYYY-MM-DD folder per capture day.
    Names are made safe for any filesystem; clashes get " 2", " 3"...
    """
    import re
    from datetime import datetime
    src = Path(src)
    base = Path(dest)
    if subfolder == "day":
        try:
            day = datetime.fromtimestamp(taken_at if taken_at else src.stat().st_mtime)
        except (OSError, ValueError, OverflowError):
            day = datetime.now()
        base = base / day.strftime("%Y-%m-%d")
    ext = suffix or src.suffix
    if naming == "title" and title and title.strip():
        # Characters no filesystem accepts become spaces, then runs of
        # spaces collapse: "Faro: norte/sur" -> "Faro norte sur".
        cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title)
        stem = re.sub(r"\s+", " ", cleaned).strip()[:120] or src.stem
    elif naming == "sequential":
        stem = f"Photo {index + 1:04d}"
    else:
        stem = src.stem
    out = base / f"{stem}{ext}"
    n = 2
    while out.exists():
        out = base / f"{stem} {n}{ext}"
        n += 1
    return out


def _encode(img: np.ndarray, fmt: str, q: int, *,
            exif: bytes | None = None, lossless: bool = False,
            subsampling: int | None = None) -> bytes:
    """Encode to an in-memory buffer."""
    pil = iio.to_pil(img)
    buf = io.BytesIO()
    kw: dict[str, Any] = {}
    if exif:
        kw["exif"] = exif

    if fmt == "jpeg":
        # 4:4:4 above q90: above that point the chroma loss from 4:2:0 is
        # the dominant error, and it is what "visually lossless" trips on.
        sub = subsampling if subsampling is not None else (0 if q >= 90 else 2)
        pil.save(buf, "JPEG", quality=int(q), subsampling=sub,
                 optimize=True, progressive=True, **kw)
    elif fmt == "webp":
        if lossless:
            pil.save(buf, "WEBP", lossless=True, method=6, **kw)
        else:
            pil.save(buf, "WEBP", quality=int(q), method=6, **kw)
    elif fmt == "avif":
        if lossless:
            pil.save(buf, "AVIF", quality=100, **kw)
        else:
            pil.save(buf, "AVIF", quality=int(q), speed=5, **kw)
    elif fmt == "png":
        pil.save(buf, "PNG", optimize=True, compress_level=9)
    else:
        raise ValueError(f"cannot encode {fmt}")
    return buf.getvalue()


def _decode(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("RGB"), np.float32) / 255.0


def _analysis_montage(img: np.ndarray, tile: int = 288,
                      count: int = 9) -> np.ndarray:
    """Assemble the regions where compression artefacts actually show.

    Running the quality ladder on a 57-megapixel frame means seven full
    encodes and decodes, which takes minutes.  It is also unnecessary:
    JPEG and WebP quantise in small independent blocks, so a montage of
    real blocks from the image predicts the full frame's quality closely.

    Tiles are chosen from both extremes of local detail, because the two
    artefacts that matter live in opposite places - ringing shows up in
    the busiest regions, and banding in the flattest ones.  Picking only
    high-variance tiles is the usual mistake, and it misses exactly the
    flat-sky DC shift that these gates exist to catch.
    """
    h, w = img.shape[:2]
    if h <= tile * 2 or w <= tile * 2:
        return img

    step = tile
    scores = []
    l = ops.luma(img)
    for y in range(0, h - tile, step):
        for x in range(0, w - tile, step):
            patch = l[y:y + tile:8, x:x + tile:8]      # subsample to rank cheaply
            scores.append((float(patch.var()), y, x))
    if not scores:
        return img
    scores.sort()

    half = max(1, count // 2)
    picks = scores[-half:]                              # busiest
    picks += scores[:count - half]                      # flattest
    cols = int(np.ceil(np.sqrt(len(picks))))
    rows = int(np.ceil(len(picks) / cols))
    out = np.zeros((rows * tile, cols * tile, 3), np.float32)
    for i, (_, y, x) in enumerate(picks):
        ry, rx = divmod(i, cols)
        out[ry * tile:(ry + 1) * tile, rx * tile:(rx + 1) * tile] = \
            img[y:y + tile, x:x + tile]
    return out


def _run_ladder(original: np.ndarray, prof: "Profile", out_fmt: str,
                exif: bytes | None, max_candidates: int
                ) -> tuple[int, dict] | None:
    """Find the lowest ladder rung whose result still passes every gate.

    Scans rather than bisects: perceptual score is not monotonic in
    encoder quality (see the module docstring), so bisection is unsound.
    """
    probe = _analysis_montage(original)
    best: tuple[int, int, dict] | None = None
    for i, q in enumerate(prof.ladder):
        if i >= max_candidates:
            break
        try:
            data = _encode(probe, out_fmt, q, exif=None)
            metrics = qual.compare(probe, _decode(data))
        except Exception:
            continue
        if prof.min_ssim is not None and metrics["ssim"] < prof.min_ssim:
            continue
        if (prof.min_worst_block is not None
                and metrics["worst_block"] < prof.min_worst_block):
            continue
        if (prof.max_chroma_error is not None
                and metrics["chroma_error"] > prof.max_chroma_error):
            continue
        if best is None or len(data) < best[1]:
            best = (q, len(data), metrics)
    if best is None:
        return None
    return best[0], best[2]


def _pick_format(source: Path, requested: str,
                 lossless: bool = False) -> str:
    """Choose the container to write.

    A lossless profile must never land on JPEG: JPEG has no lossless
    mode, so "quality 100" is still lossy *and* larger than the original
    - the worst of both. WebP's lossless mode beats PNG on photographic
    content by a wide margin, so it is preferred where available.
    """
    if lossless:
        if requested in ("png", "webp") and format_available(requested):
            return requested
        return "webp" if format_available("webp") else "png"
    if requested != "keep":
        return requested if format_available(requested) else "jpeg"
    ext = source.suffix.lower()
    if ext in (".png", ".gif", ".bmp"):
        # Screenshots and graphics: PNG-style content compresses badly as
        # JPEG and the ringing around text is obvious.
        return "png"
    if ext == ".webp":
        return "webp"
    return "jpeg"


def plan(source: Path | str, profile: str = DEFAULT_PROFILE,
         fmt: str = "keep", *, max_candidates: int = 7) -> Result:
    """Choose the best encoding for one file without writing anything.

    Returns the winning candidate's size and metrics, so a settings
    screen can show "this would save 62% on your library" honestly,
    having actually measured it.
    """
    src = Path(source)
    prof = PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])
    try:
        size = src.stat().st_size
    except OSError:
        return Result(False, src, note="unreadable")

    if prof.passthrough:
        return Result(True, src, original_bytes=size, output_bytes=size,
                      profile=prof.id, format="original", note=_("kept as-is"))

    try:
        original = iio.load_rgb(src)
    except Exception as exc:
        return Result(False, src, original_bytes=size,
                      note=_("can't read the file ({error})").format(error=type(exc).__name__))

    if prof.max_side:
        original = ops.fit_within(original, prof.max_side)

    out_fmt = _pick_format(src, fmt, prof.lossless)
    exif = _exif_bytes(src)

    if prof.lossless or out_fmt == "png":
        data = _encode(original, out_fmt, 100, exif=exif, lossless=True)
        nbytes = len(data)
        if size and nbytes >= size:
            # Re-encoding a photo losslessly is routinely larger than the
            # lossy original.  Report the honest outcome rather than a
            # negative saving.
            return Result(True, src, original_bytes=size, output_bytes=size,
                          profile=prof.id, format="original", quality=None,
                          metrics={"ssim": 1.0, "worst_block": 1.0},
                          note=_("lossless would be larger; original kept"))
        return Result(True, src, original_bytes=size, output_bytes=nbytes,
                      profile=prof.id, format=out_fmt, quality=None,
                      metrics={"ssim": 1.0, "worst_block": 1.0},
                      note="lossless")

    # Search on a montage of representative blocks, then encode the real
    # frame once at the winning setting to get its true size.
    best = _run_ladder(original, prof, out_fmt, exif, max_candidates)

    if best is None:
        # Nothing on the ladder met the gates.  Rather than silently
        # shipping something that fails the user's stated intent, fall
        # back to the top rung and say so.
        q = prof.ladder[0]
        try:
            data = _encode(original, out_fmt, q, exif=exif, subsampling=0)
            metrics = qual.compare(original, _decode(data))
            return Result(True, src, original_bytes=size,
                          output_bytes=len(data), profile=prof.id,
                          format=out_fmt, quality=q, metrics=metrics,
                          note=_("no ladder step met the target; used highest "
                               "quality"))
        except Exception as exc:
            return Result(False, src, original_bytes=size,
                          note=_("couldn't save the file ({error})").format(error=type(exc).__name__))

    q, metrics = best
    try:
        nbytes = len(_encode(original, out_fmt, q, exif=exif))
    except Exception as exc:
        return Result(False, src, original_bytes=size,
                      note=_("couldn't save the file ({error})").format(error=type(exc).__name__))
    if size and nbytes >= size:
        # Never advertise a negative saving: the honest answer is that
        # this file is already smaller than we can make it.
        return Result(True, src, original_bytes=size, output_bytes=size,
                      profile=prof.id, format="original", quality=None,
                      metrics=metrics,
                      note=_("already well compressed; original kept"))
    note = ""
    if prof.max_side:
        note = _("made smaller, up to {size} px on the long side").format(size=prof.max_side)
    return Result(True, src, original_bytes=size, output_bytes=nbytes,
                  profile=prof.id, format=out_fmt, quality=q,
                  metrics=metrics, note=note)


def compress_to(source: Path | str, dest: Path | str,
                profile: str = DEFAULT_PROFILE, fmt: str = "keep",
                *, image: np.ndarray | None = None,
                allow_larger: bool = False,
                strip_metadata: bool = False,
                strip_location: bool = False,
                max_side: int | None = None) -> Result:
    """Write a compressed copy of ``source`` (or of ``image``) to ``dest``.

    ``image`` lets an edited render be compressed without going back to
    disk for pixels that have already been computed.
    """
    src, out = Path(source), Path(dest)
    prof = PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])
    try:
        size = src.stat().st_size if src.exists() else 0
    except OSError:
        size = 0

    out.parent.mkdir(parents=True, exist_ok=True)

    if (prof.passthrough and image is None and not strip_metadata
            and not strip_location and not max_side):
        if src.resolve() != out.resolve():
            shutil.copy2(src, out)
        return Result(True, src, out, size, size, prof.id, "original",
                      note=_("copied unchanged"))

    original = image if image is not None else iio.load_rgb(src)
    if prof.max_side:
        original = ops.fit_within(original, prof.max_side)
    if max_side:
        # Export's Size option: never larger than the photo already is.
        original = ops.fit_within(original, int(max_side))
    out_fmt = _pick_format(src, fmt, prof.lossless)
    # strip_metadata is a privacy control: when it is on, no EXIF block
    # is carried into the copy at all, which is what removes the capture
    # date, the camera, and - the reason people actually ask for it -
    # the GPS coordinates of where the photo was taken.
    exif = None if strip_metadata else (_exif_bytes(src) if src.exists() else None)
    if strip_location and exif:
        exif = _exif_without_gps(exif)

    if prof.lossless or out_fmt == "png":
        data = _encode(original, out_fmt, 100, exif=exif, lossless=True)
        chosen_q, metrics = None, {"ssim": 1.0, "worst_block": 1.0}
    else:
        chosen = plan(src, profile, fmt) if image is None else None
        if chosen is not None and chosen.ok and chosen.quality:
            chosen_q = chosen.quality
            metrics = chosen.metrics
            data = _encode(original, out_fmt, chosen_q, exif=exif)
        else:
            # Pixels handed in directly: run the ladder on them.
            found = _run_ladder(original, prof, out_fmt, exif, len(prof.ladder))
            if found is None:
                chosen_q = prof.ladder[0]
                data = _encode(original, out_fmt, chosen_q, exif=exif,
                               subsampling=0)
                metrics = {}
            else:
                chosen_q, metrics = found
                data = _encode(original, out_fmt, chosen_q, exif=exif)

    if not allow_larger and size and len(data) >= size and image is None:
        # Honour the intent: "save space" that costs space is a bug.
        if src.resolve() != out.resolve():
            shutil.copy2(src, out)
        return Result(True, src, out, size, size, prof.id, "original",
                      note=_("original was already smaller; copied unchanged"))

    suffix = {"jpeg": ".jpg", "webp": ".webp", "avif": ".avif",
              "png": ".png"}.get(out_fmt, out.suffix or ".jpg")
    if out.suffix.lower() != suffix:
        out = out.with_suffix(suffix)
    tmp = out.with_suffix(out.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(out)
    return Result(True, src, out, size, len(data), prof.id, out_fmt,
                  chosen_q, metrics)


def estimate_library(paths: list[Path | str], profile: str = DEFAULT_PROFILE,
                     sample: int = 24) -> dict:
    """Measure a sample to predict savings across a whole library.

    Sampling is spread across the list rather than taken from the front,
    so a folder that starts with screenshots does not skew the estimate
    for a library of photographs.
    """
    paths = [Path(p) for p in paths]
    if not paths:
        return {"sampled": 0, "percent_saved": 0.0, "total_bytes": 0,
                "predicted_bytes": 0}
    step = max(1, len(paths) // max(1, sample))
    chosen = paths[::step][:sample]

    orig = out = 0
    ok = 0
    for p in chosen:
        r = plan(p, profile)
        if r.ok and r.original_bytes:
            orig += r.original_bytes
            out += min(r.output_bytes, r.original_bytes)
            ok += 1
    if not ok or not orig:
        return {"sampled": 0, "percent_saved": 0.0, "total_bytes": 0,
                "predicted_bytes": 0}
    ratio = out / orig
    total = sum((p.stat().st_size for p in paths if p.exists()), 0)
    return {
        "sampled": ok,
        "percent_saved": (1.0 - ratio) * 100.0,
        "total_bytes": total,
        "predicted_bytes": int(total * ratio),
        "profile": profile,
    }
