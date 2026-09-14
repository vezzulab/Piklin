"""Video files: what the library needs to know about them.

Videos live in the library beside photos - the same days, the same albums -
so they go through the same scan, the same thumbnail cache and the same
grid. This module is the part that knows they are moving pictures:

* ``probe_video`` reads size, length and frame rate through the FFmpeg
  build inside the bundled OpenCV, and the capture date, location and
  camera from the MP4/QuickTime header itself (phones and cameras write
  them there, not in EXIF). Nothing is decoded but one frame.
* ``poster`` picks the frame a tile shows: a moment in, not frame zero,
  which is so often black or a blur.

No part of this needs the system's GStreamer; playback does (see
ui/player.py), and falls back to the same OpenCV decoder when it is absent.
"""
from __future__ import annotations

import os
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VIDEO_EXT = {
    ".mp4", ".m4v", ".mov", ".qt", ".mkv", ".webm", ".avi", ".3gp", ".3g2",
    ".mts", ".m2ts", ".mpg", ".mpeg", ".wmv", ".ogv", ".flv",
}
# Containers with an ISO base media / QuickTime header to read metadata from.
_ISO_EXT = {".mp4", ".m4v", ".mov", ".qt", ".3gp", ".3g2"}

# Seconds between 1904-01-01 (QuickTime epoch) and 1970-01-01.
_QT_EPOCH = 2082844800


def is_video(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXT


def format_duration(seconds: float | None) -> str:
    """0:07, 12:34, 1:02:03 - the way every player shows length."""
    if not seconds or seconds < 0:
        return "0:00"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# -- stream properties (OpenCV / FFmpeg) ------------------------------------
def _capture(path):
    import cv2
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    return cap if cap.isOpened() else None


def stream_info(path: Path | str) -> dict | None:
    """Width and height as shown (rotation applied), fps and duration."""
    import cv2
    cap = _capture(path)
    if cap is None:
        return _av_stream_info(path)
    try:
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        rotation = int(cap.get(cv2.CAP_PROP_ORIENTATION_META) or 0) % 360
        ok, frame = cap.read()
        if not ok or w <= 0 or h <= 0:
            return None
    finally:
        cap.release()
    if not (0 < fps < 1000):
        fps = 0.0
    duration = frames / fps if fps and frames > 0 else 0.0
    # The size as shown is the size of a decoded frame: OpenCV already turns
    # frames upright. Swapping width and height again for the rotation made
    # an upright phone video 720x1280 count as 1280x720.
    fh, fw = frame.shape[:2]
    if fw > 0 and fh > 0:
        w, h = fw, fh
    elif rotation in (90, 270):
        w, h = h, w
    return {"width": w, "height": h, "fps": fps, "duration": duration,
            "rotation": rotation}


def frame_at(path: Path | str, seconds: float = 0.0,
             max_side: int | None = None):
    """One frame as an RGB ``PIL.Image`` (rotation applied by OpenCV)."""
    import cv2
    from PIL import Image
    cap = _capture(path)
    if cap is None:
        return _av_frame_at(path, seconds, max_side)
    try:
        if seconds > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000.0)
        ok, frame = cap.read()
        if not ok and seconds > 0:
            # Some containers cannot seek (a damaged index, raw MTS): the
            # first frame is better than nothing.
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok:
            raise OSError(f"no frame in {path}")
    finally:
        cap.release()
    im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    return im


# -- the same through PyAV ---------------------------------------------------
# Some OpenCV builds come without its FFmpeg (the one for Intel Macs), and
# open no video at all. PyAV, with the FFmpeg Piklin ships, reads the same
# files on every system, so a video is never refused for that.
def _av_frame(container, stream, seconds: float):
    """The first frame at or after ``seconds`` (the last one if it runs out)."""
    if seconds > 0 and stream.time_base:
        try:
            container.seek(int(seconds / stream.time_base), stream=stream)
        except Exception:
            pass
    last = None
    for frame in container.decode(stream):
        if seconds <= 0 or frame.time is None or frame.time >= seconds - 1e-3:
            return frame
        last = frame
    return last


def _av_upright(frame):
    """The frame as an RGB array turned upright, and the rotation applied -
    the way ui/player.py turns frames, and OpenCV does by itself."""
    import numpy as np
    arr = frame.to_ndarray(format="rgb24")
    turns = int(round(-(getattr(frame, "rotation", 0) or 0))) % 360
    if turns in (90, 180, 270):
        arr = np.ascontiguousarray(np.rot90(arr, k={90: -1, 180: 2, 270: 1}[turns]))
    return arr, turns


def _av_stream_info(path) -> dict | None:
    try:
        import av
    except Exception:
        return None
    try:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            fps = float(stream.average_rate or stream.guessed_rate or 0.0)
            if stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            else:
                duration = (container.duration or 0) / 1_000_000
            frame = _av_frame(container, stream, 0.0)
            if frame is None:
                return None
            arr, rotation = _av_upright(frame)
    except Exception:
        return None
    h, w = arr.shape[:2]
    if not (0 < fps < 1000):
        fps = 0.0
    return {"width": w, "height": h, "fps": fps, "duration": duration,
            "rotation": rotation}


def _av_frame_at(path, seconds: float, max_side: int | None):
    from PIL import Image
    try:
        import av
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            frame = _av_frame(container, stream, seconds)
            if frame is None:
                raise OSError(f"no frame in {path}")
            arr, _turns = _av_upright(frame)
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"cannot open video {path}") from exc
    im = Image.fromarray(arr)
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    return im


def poster(path: Path | str, max_side: int | None = None):
    """The frame a tile shows: a little way in, never the first black frame."""
    info = stream_info(path)
    duration = (info or {}).get("duration") or 0.0
    return frame_at(path, min(1.0, duration * 0.1) if duration else 0.0, max_side)


# -- MP4 / QuickTime header --------------------------------------------------
def _atoms(fh, start: int, end: int):
    """(type, payload start, payload end) for each box between start and end."""
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        head = fh.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        body = pos + 8
        if size == 1:
            big = fh.read(8)
            if len(big) < 8:
                return
            size = struct.unpack(">Q", big)[0]
            body = pos + 16
        elif size == 0:
            size = end - pos
        if size < body - pos or pos + size > end:
            return
        yield kind, body, pos + size
        pos += size


def _child(fh, start, end, kind):
    for k, b, e in _atoms(fh, start, end):
        if k == kind:
            return b, e
    return None


_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def _location(text: str):
    m = _ISO6709.match(text.strip())
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
        return lat, lon
    return None


def _iso_date(text: str) -> float | None:
    text = text.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt.endswith("Z"):
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _udta_text(fh, b, e) -> str:
    """QuickTime user-data text: 2-byte length, 2-byte language, text."""
    fh.seek(b)
    raw = fh.read(min(e - b, 512))
    if len(raw) >= 4:
        n = struct.unpack(">H", raw[:2])[0]
        if 0 < n <= len(raw) - 4:
            return raw[4:4 + n].decode("utf-8", "replace")
    return raw.decode("utf-8", "replace").strip("\x00")


def _meta_items(fh, b, e) -> dict[str, Any]:
    """Apple 'mdta' keys (com.apple.quicktime.*) and their values."""
    # In an MP4 'meta' is a full box (4 bytes of version/flags); in a
    # QuickTime movie it is not. Look for the handler to tell which.
    fh.seek(b + 4)
    if fh.read(4) != b"hdlr":
        b += 4
    keys_box = _child(fh, b, e, b"keys")
    ilst = _child(fh, b, e, b"ilst")
    if not keys_box or not ilst:
        return {}
    fh.seek(keys_box[0] + 4)
    count = struct.unpack(">I", fh.read(4))[0]
    names, pos = [], keys_box[0] + 8
    for _ in range(min(count, 256)):
        fh.seek(pos)
        size, _ns = struct.unpack(">I4s", fh.read(8))
        if size < 8:
            break
        names.append(fh.read(size - 8).decode("utf-8", "replace"))
        pos += size
    out: dict[str, Any] = {}
    for kind, ib, ie in _atoms(fh, ilst[0], ilst[1]):
        index = struct.unpack(">I", kind)[0]
        if not 1 <= index <= len(names):
            continue
        data = _child(fh, ib, ie, b"data")
        if not data:
            continue
        fh.seek(data[0] + 8)                       # type + locale
        value = fh.read(min(data[1] - data[0] - 8, 512))
        out[names[index - 1]] = value.decode("utf-8", "replace").strip("\x00")
    return out


def container_meta(path: Path | str) -> dict[str, Any]:
    """Capture date, location and camera from an MP4/MOV header."""
    out: dict[str, Any] = {}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            moov = _child(fh, 0, size, b"moov")
            if not moov:
                return out
            mvhd = _child(fh, *moov, b"mvhd")
            if mvhd:
                fh.seek(mvhd[0])
                version = fh.read(1)[0]
                fh.read(3)
                created = (struct.unpack(">Q", fh.read(8))[0] if version == 1
                           else struct.unpack(">I", fh.read(4))[0])
                ts = created - _QT_EPOCH
                if created and 631152000 < ts < 4102444800:   # 1990..2100
                    out["created"] = float(ts)
            udta = _child(fh, *moov, b"udta")
            if udta:
                for kind, b, e in _atoms(fh, *udta):
                    if kind == b"\xa9xyz":
                        loc = _location(_udta_text(fh, b, e))
                        if loc:
                            out["location"] = loc
                    elif kind == b"\xa9mak":
                        out["make"] = _udta_text(fh, b, e)
                    elif kind == b"\xa9mod":
                        out["model"] = _udta_text(fh, b, e)
            meta = _child(fh, *moov, b"meta")
            if meta:
                items = _meta_items(fh, *meta)
                if "com.apple.quicktime.creationdate" in items:
                    ts = _iso_date(items["com.apple.quicktime.creationdate"])
                    if ts:
                        out["created_local"] = ts
                loc = _location(items.get("com.apple.quicktime.location.ISO6709", ""))
                if loc:
                    out["location"] = loc
                if items.get("com.apple.quicktime.make"):
                    out["make"] = items["com.apple.quicktime.make"]
                if items.get("com.apple.quicktime.model"):
                    out["model"] = items["com.apple.quicktime.model"]
    except (OSError, struct.error, IndexError, ValueError):
        pass
    return out


# -- probe -------------------------------------------------------------------
def probe_video(path: Path | str, root_id: int | None = None) -> dict | None:
    """A catalog record for a video, or None when it cannot be played."""
    from . import imageio as iio
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    if st.st_size == 0:
        return None
    info = stream_info(p)
    if info is None:
        return None
    rec: dict[str, Any] = {
        "path": str(p), "root_id": root_id, "filename": p.name,
        "ext": p.suffix.lower().lstrip("."), "bytes": st.st_size,
        "mtime": st.st_mtime, "orientation": 1, "thumb_state": 0,
        "width": info["width"], "height": info["height"],
        "duration": round(info["duration"], 3),
    }
    meta = container_meta(p) if p.suffix.lower() in _ISO_EXT else {}
    ts = meta.get("created_local") or meta.get("created")
    if ts:
        rec["taken_at"], rec["date_source"] = ts, "metadata"
    else:
        ts = iio._date_from_name(p.stem)
        rec["taken_at"], rec["date_source"] = ((ts, "filename") if ts
                                               else (st.st_mtime, "mtime"))
    if meta.get("location"):
        rec["gps_lat"], rec["gps_lon"] = meta["location"]
    for key, field in (("make", "camera_make"), ("model", "camera_model")):
        if meta.get(key):
            rec[field] = meta[key].strip()
    try:
        rec["fingerprint"] = iio.fingerprint(p, st.st_size)
    except OSError:
        pass
    return rec
