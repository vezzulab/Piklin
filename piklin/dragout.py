"""Photos dragged out of Piklin, as files anything can open.

Dragging onto an album inside Piklin only needs to say which photos. Dragging
to the desktop, a folder or another program needs files, and they should open
there: a photo kept as HEIC, WebP, AVIF, TIFF or a camera's RAW is handed over
as an ordinary JPEG, with its place and date; one that has been edited is
handed over as it looks now. A JPEG or PNG nobody changed is handed over as
it is, so nothing is re-compressed for no reason.

The converted copies are made only when something is actually dropped, and
kept for a day in the cache so dropping them again costs nothing.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

from . import compress as cz
from .logs import log

# Formats every program opens: handed over untouched unless they were edited.
UNIVERSAL = {".jpg", ".jpeg", ".png", ".gif"}
KEEP_FOR = 24 * 3600


def cache_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "Piklin"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "piklin"
    return base / "dragged"


def _edit_stack(library, path: Path):
    """The photo's edits, or None when it has none."""
    if library is None:
        return None
    try:
        sidecar = library.edit_sidecar(path)
        if not sidecar.exists():
            return None
        from .engine.stack import EditStack
        stack = EditStack.load(sidecar)
        return stack if len(stack) else None
    except Exception:
        return None


def needs_conversion(path: Path | str, edited: bool = False) -> bool:
    """Whether the photo must be turned into a JPEG to be handed over."""
    from .video import is_video
    p = Path(path)
    if is_video(p):
        return False                # a video goes as it is
    return edited or p.suffix.lower() not in UNIVERSAL


def _key(path: Path, sidecar: Path | None) -> str:
    try:
        st = path.stat()
        mark = f"{path}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        mark = str(path)
    if sidecar is not None:
        try:
            mark += f"|{sidecar.stat().st_mtime_ns}"
        except OSError:
            pass
    return hashlib.sha1(mark.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def plan_for_drag(library, path: Path | str) -> tuple[Path, bool]:
    """Where the file handed over for this photo is, and whether it still has
    to be made. Nothing is converted here: this is what a drag declares when it
    starts, before anybody knows where it will be dropped."""
    p = Path(path)
    stack = _edit_stack(library, p)
    if not needs_conversion(p, stack is not None):
        return p, False
    sidecar = library.edit_sidecar(p) if library is not None else None
    out = cache_dir() / _key(p, sidecar) / (p.stem + ".jpg")
    return out, not out.exists()


def file_for_drag(library, path: Path | str) -> Path:
    """The file to hand over for this photo: itself when it opens anywhere,
    else a JPEG made from it. Falls back to the photo itself if the copy
    cannot be made, so a drag is never left with nothing."""
    p = Path(path)
    out, missing = plan_for_drag(library, p)
    if not missing:
        return out
    try:
        stack = _edit_stack(library, p)
        out.parent.mkdir(parents=True, exist_ok=True)
        image = None
        if stack is not None:
            from .engine.stack import render_full
            image = render_full(p, stack)
        # "Looks the same" at full size, place and date kept; allow_larger
        # because a JPEG is what is wanted even where the original is smaller.
        result = cz.compress_to(p, out, "visually_lossless", "jpeg", image=image,
                                allow_larger=True, strip_metadata=False,
                                strip_location=False)
        if result.ok and out.exists():
            return out
        if result.ok and result.output and Path(result.output).exists():
            return Path(result.output)
    except Exception as exc:
        log.warning("could not make a JPEG of %s for dragging: %s", p.name, exc)
    return p


def files_for_drag(library, paths) -> list[Path]:
    return [file_for_drag(library, p) for p in paths]


def clean_cache(now: float | None = None) -> None:
    """Forget the copies made for dragging more than a day ago."""
    root = cache_dir()
    if not root.is_dir():
        return
    now = time.time() if now is None else now
    import shutil
    for entry in root.iterdir():
        try:
            if now - entry.stat().st_mtime > KEEP_FOR:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass
