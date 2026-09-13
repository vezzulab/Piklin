"""Decoding, metadata probing and encoding.

Two things here are worth more than they look:

``probe`` never fully decodes an image.  Scanning a 60k-photo folder has
to read headers only, or the first library import takes an afternoon.

``load_rgb`` uses JPEG DCT scaling (``Image.draft``) when a downscaled
result is wanted.  Asking libjpeg for a 1/8-scale decode is roughly 6x
faster than decoding full size and resampling, which is the difference
between a thumbnail pass that keeps up with an SSD and one that does not.
"""
from __future__ import annotations

import hashlib
import os
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFile, ImageOps

# A truncated JPEG should render what it has rather than raising: photo
# libraries accumulate partially-copied files and one of them should not
# abort a scan.
ImageFile.LOAD_TRUNCATED_IMAGES = True
# Guard against decompression-bomb aborts on legitimately huge panoramas
# while still refusing something absurd.
Image.MAX_IMAGE_PIXELS = 512_000_000

# -- format support ------------------------------------------------------
RASTER_EXT = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".tif", ".tiff",
    ".bmp", ".gif", ".ppm", ".pgm", ".tga", ".ico", ".jp2", ".j2k",
}
HEIF_EXT = {".heic", ".heif", ".avif"}
RAW_EXT = {
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf",
    ".rw2", ".pef", ".dng", ".raw", ".erf", ".kdc", ".mos", ".mrw", ".x3f",
    ".3fr", ".iiq", ".rwl", ".srw",
}

_HEIF = False
# pi-heif first: it is the decode-only build of pillow-heif. pillow-heif
# also bundles the x265 encoder, which is GPL - shipping it inside a paid,
# closed package is not allowed, and we never write HEIC anyway. AVIF is
# read and written by Pillow itself (12.x wheels carry libavif).
for _mod in ("pi_heif", "pillow_heif"):
    try:
        _heif = __import__(_mod)
        _heif.register_heif_opener()
        _HEIF = True
        break
    except Exception:
        pass

_RAWPY = False
try:                                        # optional: rawpy (libraw)
    import rawpy                            # type: ignore
    _RAWPY = True
except Exception:
    pass


def supported_extensions() -> set[str]:
    from .video import VIDEO_EXT
    ext = set(RASTER_EXT) | VIDEO_EXT
    if _HEIF:
        ext |= HEIF_EXT
    if _RAWPY:
        ext |= RAW_EXT
    return ext


def is_supported(path: Path | str) -> bool:
    return Path(path).suffix.lower() in supported_extensions()


def missing_codecs() -> list[str]:
    """Human-readable list of formats this install cannot open."""
    gaps = []
    if not _HEIF:
        gaps.append("HEIC/HEIF/AVIF (install pi-heif)")
    if not _RAWPY:
        gaps.append("camera RAW (install rawpy)")
    return gaps


# -- EXIF ----------------------------------------------------------------
# Tag numbers rather than names: PIL's name table varies between versions
# and the numbers are fixed by the spec.
_EXIF = {
    0x010F: "camera_make", 0x0110: "camera_model", 0x0112: "orientation",
    0x829A: "exposure", 0x829D: "f_number", 0x8827: "iso",
    0x9003: "dt_original", 0x9004: "dt_digitized", 0x0132: "dt_modified",
    0x920A: "focal_length", 0xA434: "lens", 0xA002: "px_width",
    0xA003: "px_height", 0x9291: "subsec",
}


def _rational(v: Any) -> float | None:
    try:
        if isinstance(v, tuple) and len(v) == 2:
            return float(v[0]) / float(v[1]) if v[1] else None
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _exif_datetime(raw: str | None, subsec: str | None = None) -> float | None:
    if not raw:
        return None
    raw = str(raw).strip().rstrip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            dt = datetime.strptime(raw[:19], fmt)
        except ValueError:
            continue
        ts = dt.timestamp()
        if subsec:
            try:
                ts += float(f"0.{str(subsec).strip()}")
            except ValueError:
                pass
        return ts
    return None


def _gps(exif) -> tuple[float | None, float | None]:
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:
        return None, None
    if not gps:
        return None, None

    def deg(vals, ref, neg):
        try:
            d = _rational(vals[0]) or 0.0
            m = _rational(vals[1]) or 0.0
            s = _rational(vals[2]) or 0.0
        except (TypeError, IndexError, KeyError):
            return None
        val = d + m / 60.0 + s / 3600.0
        if str(ref).upper().startswith(neg):
            val = -val
        return val if -180.0 <= val <= 180.0 else None

    lat = deg(gps.get(2), gps.get(1, "N"), "S") if gps.get(2) else None
    lon = deg(gps.get(4), gps.get(3, "E"), "W") if gps.get(4) else None
    return lat, lon


# Filenames cameras and phones actually produce, when EXIF is absent.
_NAME_DATE_PATTERNS = (
    ("%Y%m%d_%H%M%S", 15), ("%Y-%m-%d_%H-%M-%S", 19), ("%Y%m%d%H%M%S", 14),
    ("%Y-%m-%d %H.%M.%S", 19), ("%Y-%m-%d", 10), ("%Y%m%d", 8),
)


def _date_from_name(name: str) -> float | None:
    digits = "".join(c if c.isalnum() else ("-" if c in "-_. " else "")
                     for c in name)
    for prefix in (name, digits):
        for token in (prefix, prefix.lstrip("IMGVIDPXLDSC_-")):
            for fmt, ln in _NAME_DATE_PATTERNS:
                cand = token[:ln]
                if len(cand) < ln:
                    continue
                try:
                    dt = datetime.strptime(cand, fmt)
                except ValueError:
                    continue
                if 1990 <= dt.year <= datetime.now().year + 1:
                    return dt.timestamp()
    return None


def fingerprint(path: Path | str, size: int | None = None) -> str:
    """Cheap content fingerprint: size plus the head and tail of the file.

    Full hashing of a library of 40 MB RAWs is I/O-bound and slow; head +
    tail + length is enough to group genuine duplicates (a copied file
    matches byte for byte) without ever producing a false match that
    matters, since candidates are confirmed by size first.
    """
    p = Path(path)
    if size is None:
        size = p.stat().st_size
    h = hashlib.blake2b(digest_size=16)
    h.update(struct.pack("<Q", size))
    chunk = 65536
    with open(p, "rb") as fh:
        h.update(fh.read(chunk))
        if size > chunk * 2:
            fh.seek(-chunk, os.SEEK_END)
            h.update(fh.read(chunk))
    return h.hexdigest()


def probe(path: Path | str, root_id: int | None = None) -> dict | None:
    """Read one photo's header and metadata without decoding pixels."""
    p = Path(path)
    from .video import VIDEO_EXT, probe_video
    if p.suffix.lower() in VIDEO_EXT:
        return probe_video(p, root_id)
    try:
        st = p.stat()
    except OSError:
        return None

    rec: dict[str, Any] = {
        "path": str(p), "root_id": root_id, "filename": p.name,
        "ext": p.suffix.lower().lstrip("."), "bytes": st.st_size,
        "mtime": st.st_mtime, "orientation": 1, "thumb_state": 0,
    }

    ext = p.suffix.lower()
    tags: dict[str, Any] = {}
    # A file no decoder can even open is not a photo - an empty file, a
    # truncated download, something else with a .jpg name. It is left out
    # of the library rather than kept as a blank tile that opens nothing.
    if st.st_size == 0:
        return None
    try:
        if ext in RAW_EXT and _RAWPY:
            # libraw gives dimensions; EXIF still comes from PIL where the
            # RAW carries an embedded JPEG with a readable APP1 segment.
            with rawpy.imread(str(p)) as raw:
                rec["width"], rec["height"] = raw.sizes.width, raw.sizes.height
            return _finish_probe(rec, tags)
        im = Image.open(p)
    except Exception:
        return None
    with im:
        rec["width"], rec["height"] = im.size
        # Metadata is best effort: a photo with a damaged EXIF block is
        # still a photo.
        try:
            exif = im.getexif()
            if exif:
                for tag, name in _EXIF.items():
                    if tag in exif:
                        tags[name] = exif[tag]
                try:
                    sub = exif.get_ifd(0x8769)
                    for tag, name in _EXIF.items():
                        if tag in sub and name not in tags:
                            tags[name] = sub[tag]
                except Exception:
                    pass
                rec["gps_lat"], rec["gps_lon"] = _gps(exif)
        except Exception:
            pass
    return _finish_probe(rec, tags)


def _finish_probe(rec: dict[str, Any], tags: dict[str, Any]) -> dict | None:
    p = Path(rec["path"])
    try:
        st = p.stat()
    except OSError:
        return None
    o = tags.get("orientation")
    rec["orientation"] = int(o) if isinstance(o, int) and 1 <= o <= 8 else 1
    # Orientations 5-8 turn the picture a quarter turn: store the size it
    # is shown at, not the sensor's, so a portrait photo reads as portrait
    # in the info panel and in "width/height" smart album rules.
    if rec["orientation"] >= 5 and rec.get("width") and rec.get("height"):
        rec["width"], rec["height"] = rec["height"], rec["width"]
    for key, cast in (("camera_make", str), ("camera_model", str),
                      ("lens", str), ("iso", int)):
        v = tags.get(key)
        if v is None:
            continue
        try:
            if cast is str:
                # Cameras pad fixed-width text fields - Nikon pads the
                # lens name with NULs, which str.strip() leaves alone,
                # so the field came back as a long run of blanks rather
                # than empty.
                text = str(v).replace("\x00", " ").strip()
                if text:
                    rec[key] = text
            else:
                rec[key] = cast(v)
        except (TypeError, ValueError):
            pass
    for key in ("f_number", "exposure", "focal_length"):
        v = _rational(tags.get(key))
        # A manual or adapted lens has no electronic contacts, so the
        # camera writes zero rather than omitting the tag. Zero aperture
        # and zero focal length are not measurements - recording them
        # would print "f/0.0" in the info panel as though it were real.
        if v is not None and v > 0:
            rec[key] = v

    ts = (_exif_datetime(tags.get("dt_original"), tags.get("subsec"))
          or _exif_datetime(tags.get("dt_digitized")))
    if ts:
        rec["taken_at"], rec["date_source"] = ts, "exif"
    else:
        ts = _date_from_name(p.stem)
        if ts:
            rec["taken_at"], rec["date_source"] = ts, "filename"
        else:
            rec["taken_at"], rec["date_source"] = st.st_mtime, "mtime"

    try:
        rec["fingerprint"] = fingerprint(p, st.st_size)
    except OSError:
        pass
    return rec


# -- decoding ------------------------------------------------------------
def load_pil(path: Path | str, max_side: int | None = None,
             apply_orientation: bool = True) -> Image.Image:
    """Open as an orientation-corrected RGB ``PIL.Image``.

    ``max_side`` is a ceiling, not a target: the result is never upscaled,
    and for JPEG the decoder is asked for a reduced-scale decode first so
    the full-size buffer is never materialised.
    """
    p = Path(path)
    ext = p.suffix.lower()

    from .video import VIDEO_EXT, poster
    if ext in VIDEO_EXT:
        # A video's thumbnail and still preview: a frame a moment in.
        return poster(p, max_side)

    if ext in RAW_EXT and _RAWPY:
        with rawpy.imread(str(p)) as raw:
            try:                              # embedded preview is ~50x faster
                if max_side:
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        import io
                        im = Image.open(io.BytesIO(thumb.data))
                        im = ImageOps.exif_transpose(im) or im
                        if max(im.size) >= max_side:
                            im = im.convert("RGB")
                            im.thumbnail((max_side, max_side), Image.LANCZOS)
                            return im
            except Exception:
                pass
            rgb = raw.postprocess(
                use_camera_wb=True, no_auto_bright=False,
                output_bps=8, half_size=bool(max_side and max_side <= 2048))
        im = Image.fromarray(rgb)
    else:
        im = Image.open(p)
        if max_side and im.format == "JPEG":
            # libjpeg DCT scaling: decode at 1/2, 1/4 or 1/8 directly.
            im.draft("RGB", (max_side, max_side))
        if apply_orientation:
            im = ImageOps.exif_transpose(im) or im

    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode == "L":
        im = im.convert("RGB")

    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    return im


def load_rgb(path: Path | str, max_side: int | None = None,
             dtype=np.float32) -> np.ndarray:
    """Decode to an ``(h, w, 3)`` array.

    float32 output is scaled to 0..1, which is what the whole edit engine
    works in; uint8 is returned untouched for thumbnailing.
    """
    im = load_pil(path, max_side)
    arr = np.asarray(im, dtype=np.uint8)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    elif arr.shape[2] == 4:
        arr = arr[:, :, :3]
    if dtype == np.uint8:
        return np.ascontiguousarray(arr)
    return np.ascontiguousarray(arr.astype(dtype) / 255.0)


def to_pil(arr: np.ndarray) -> Image.Image:
    """Array (float 0..1 or uint8) back to a PIL image, clipped safely."""
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0.0, 1.0)
        arr = (arr * 255.0 + 0.5).astype(np.uint8)
    return Image.fromarray(arr, "RGB")
