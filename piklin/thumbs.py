"""Thumbnail cache.

Two sizes are kept: a grid tile and a larger one for the detail view, so
opening a photo shows something instantly while the full image decodes.

Thumbnails are JPEG on disk.  For a cache of a hundred thousand tiles the
difference between JPEG and PNG is gigabytes, and a thumbnail is a
derived artefact where a little compression costs nothing.

The cache is content-addressed by path plus mtime plus size, so editing
or replacing a file invalidates its thumbnail automatically without any
bookkeeping. A photo or video with edits gets the edited look - turned,
cropped, adjusted - and the edit file's own date joins the key, so saving
an edit gives the tile a new thumbnail.
"""
from __future__ import annotations

import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from . import imageio as iio

GRID_SIZE = 400          # stored size; the UI scales down from this
DETAIL_SIZE = 1400
SIZES = (GRID_SIZE, DETAIL_SIZE)


def cache_key(path: Path | str, mtime: float, size: int) -> str:
    raw = f"{path}|{int(mtime)}|{size}".encode()
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


class ThumbCache:
    def __init__(self, root: Path | str, workers: int = 0, edits=None):
        self.root = Path(root)
        # path -> the edit file for it (Library.edit_sidecar), or None
        self._edits = edits
        self.root.mkdir(parents=True, exist_ok=True)
        n = workers or min(8, max(2, (os.cpu_count() or 4)))
        # Decoding is I/O plus C code that releases the GIL, so threads
        # genuinely parallelise here; processes would cost more in
        # pickling the results than they save.
        self._pool = ThreadPoolExecutor(max_workers=n,
                                        thread_name_prefix="thumb")
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def path_for(self, key: str) -> Path:
        # Two-level fan-out: a single directory with 100k entries is slow
        # to stat on most filesystems.
        return self.root / key[:2] / key[2:4] / f"{key}.jpg"

    def _edit_file(self, src: Path) -> tuple[Path | None, str]:
        """The edit file for src and a short tag of its state ("" if none)."""
        if self._edits is None:
            return None, ""
        try:
            sidecar = Path(self._edits(src))
            st = sidecar.stat()
        except (OSError, ValueError, TypeError):
            return None, ""
        return sidecar, f"{st.st_mtime_ns}:{st.st_size}"

    def _key(self, src: Path, mt: float, size: int) -> tuple[str, Path | None]:
        sidecar, tag = self._edit_file(src)
        key = cache_key(src, mt, size)
        if tag:
            key = hashlib.blake2b(f"{key}|{tag}".encode(), digest_size=16).hexdigest()
        return key, sidecar

    def get_path(self, src: Path | str, size: int = GRID_SIZE,
                 mtime: float | None = None) -> Path | None:
        """Return the cached thumbnail path, or None if not generated yet."""
        src = Path(src)
        try:
            mt = mtime if mtime is not None else src.stat().st_mtime
        except OSError:
            return None
        key, _sidecar = self._key(src, mt, size)
        p = self.path_for(key)
        return p if p.is_file() else None

    @staticmethod
    def _edited_image(src: Path, sidecar: Path, size: int):
        """The photo or video frame with its edits applied, or None."""
        import json
        from .video import VIDEO_EXT
        if src.suffix.lower() in VIDEO_EXT:
            from . import video_edit as ve
            from .video import stream_info
            data = json.loads(sidecar.read_text())
            if data.get("format") != ve.FORMAT:
                return None
            edit = ve.VideoEdit.from_dict(data)
            if edit.poster is not None:
                t = edit.poster
            else:
                duration = edit.duration or (stream_info(src) or {}).get("duration") or 0.0
                t = edit.start + min(1.0, max(0.0, (edit.stop - edit.start)) * 0.1)
                t = min(t, duration) if duration else t
            return ve.frame_image(src, t, edit, max_side=size)
        from .engine.stack import EditStack, Renderer
        stack = EditStack.load(sidecar)
        if not len(stack):
            return None
        img = iio.load_rgb(src, max_side=size)
        rec = iio.probe(src) or {}
        full = (rec.get("width") or img.shape[1], rec.get("height") or img.shape[0])
        # Tools are sized relative to the whole photo; say how much smaller
        # this copy is so a blur or a crop looks the same as in the editor.
        scale = img.shape[1] / max(1, full[0])
        out = Renderer(max_cached=1).render(img, stack, scale=scale, full_size=full)
        return iio.to_pil(out)

    def generate(self, src: Path | str, size: int = GRID_SIZE,
                 mtime: float | None = None) -> Path | None:
        """Create the thumbnail synchronously. Safe to call concurrently."""
        src = Path(src)
        try:
            mt = mtime if mtime is not None else src.stat().st_mtime
        except OSError:
            return None
        key, sidecar = self._key(src, mt, size)
        out = self.path_for(key)
        if out.is_file():
            return out

        # Collapse duplicate requests for the same tile: the grid asks for
        # the same thumbnail from several scroll events in a row.
        with self._lock:
            ev = self._inflight.get(key)
            if ev is None:
                ev = self._inflight[key] = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            ev.wait(timeout=30)
            return out if out.is_file() else None

        try:
            im = None
            if sidecar is not None:
                try:
                    im = self._edited_image(src, sidecar, size)
                except Exception:
                    im = None       # a broken edit still gets a thumbnail
            if im is None:
                im = iio.load_pil(src, max_side=size)
            im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
            im.thumbnail((size, size), Image.LANCZOS)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".part")
            im.save(tmp, "JPEG", quality=88, optimize=True, progressive=False)
            tmp.replace(out)
            return out
        except Exception:
            return None
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            ev.set()

    def request(self, src: Path | str, size: int, callback,
                mtime: float | None = None) -> None:
        """Generate off the UI thread, then invoke ``callback(path)``.

        The callback runs on a worker thread; a GTK caller must hop back
        to the main loop itself.
        """
        def work():
            try:
                callback(self.generate(src, size, mtime))
            except Exception:
                callback(None)
        self._pool.submit(work)

    def size_on_disk(self) -> int:
        total = 0
        for dirpath, _, files in os.walk(self.root):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        return total

    def clear(self) -> int:
        """Delete every cached thumbnail. Returns bytes freed."""
        freed = self.size_on_disk()
        for dirpath, dirs, files in os.walk(self.root, topdown=False):
            for f in files:
                if f.endswith((".jpg", ".part")):
                    try:
                        os.remove(os.path.join(dirpath, f))
                    except OSError:
                        pass
            for dd in dirs:
                try:
                    os.rmdir(os.path.join(dirpath, dd))
                except OSError:
                    pass
        return freed
